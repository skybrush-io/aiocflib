"""Utility functions related to concurrency management."""

from collections.abc import Awaitable, Callable, Coroutine, Generator, Iterable
from contextlib import contextmanager
from functools import partial
from queue import Full, Queue
from sys import exc_info
from types import TracebackType
from typing import (
    Any,
    Generic,
    TypeAlias,
    TypeVar,
    cast,
)

from anyio import (
    CapacityLimiter,
    Condition,
    Event,
    TaskHandle,
    create_task_group,
    from_thread,
    to_thread,
)
from anyio.abc import ObjectStream, TaskGroup
from exceptiongroup import BaseExceptionGroup
from outcome import capture

__all__ = (
    "AwaitableValue",
    "aclosing",
    "create_daemon_task_group",
    "Full",
    "ObservableValue",
    "ThreadContext",
)

T = TypeVar("T")

TaskStartedNotifier = Callable[[], None]


@contextmanager
def collapse_excgroups() -> Generator[None, None, None]:
    """Context manager that collapses exception groups holding a single
    exception into the exception itself. Used to work around compatibility
    differences between AnyIO 3 and 4.
    """
    try:
        yield
    except BaseException as exc:
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]

        raise exc from None


class aclosing:
    """Context manager that handles the closing of an asynchronous generator
    when the context is exited.
    """

    def __init__(self, aiter):
        self._aiter = aiter

    async def __aenter__(self):
        return self._aiter

    async def __aexit__(self, *args) -> bool:
        await self._aiter.aclose()
        return False


class AwaitableValue(Generic[T]):
    """Object that combines an asyncio-style event and a value. Initially, the
    object contains no value and the corresponding event is not set. Tasks may
    wait for the object to be populated with a value.
    """

    _event: Event
    _value: T | None

    def __init__(self):
        """Constructor."""
        self._event = Event()
        self._value = None

    def set(self, value: T) -> None:
        """Sets a value and notifies all tasks waiting for the value."""
        if self._event.is_set():
            raise RuntimeError("awaitable value is already set")

        self._value = value
        self._event.set()

    async def wait(self) -> T:
        """Waits for the value to be populated, and returns the value when
        it is populated.
        """
        await self._event.wait()
        return self._value  # ty:ignore[invalid-return-type]


T_co = TypeVar("T_co", covariant=True)


class DaemonTaskGroup(TaskGroup):
    """Task group that cancels all its child tasks when the execution is about
    to leave the context (instead of waiting for the child tasks to finish).
    """

    _spawner: TaskGroup | None = None
    _task_group: TaskGroup

    def __init__(self):
        self._task_group = create_task_group()
        self._spawner = None

    async def __aenter__(self):
        self._spawner = await self._task_group.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self._spawner = None

        self._task_group.cancel_scope.cancel()
        with collapse_excgroups():
            return bool(await self._task_group.__aexit__(exc_type, exc_val, exc_tb))

    def create_task(self, coro: Coroutine[Any, Any, T_co], **kwds) -> TaskHandle[T_co]:
        if self._spawner is None:
            raise RuntimeError("task group is not running")

        return self._spawner.create_task(coro, **kwds)

    async def start(self, func, *args, **kwds):
        on_opened = Event()
        self.start_soon(partial(func, notify_started=on_opened.set), *args, **kwds)
        await on_opened.wait()


create_daemon_task_group = DaemonTaskGroup


async def _gather_execute(
    func: Callable[[], Awaitable[T]], result: list[T], index: int
) -> None:
    result[index] = await func()


async def _gather_execute_limited(
    limiter: CapacityLimiter,
    func: Callable[[], Awaitable[T]],
    result: list[T],
    index: int,
):
    async with limiter:
        result[index] = await func()


async def gather(
    funcs: Iterable[Callable[[], Awaitable[T]]],
    limiter: CapacityLimiter | int | None = None,
) -> list[T]:
    result: list[T | None] = []

    if isinstance(limiter, int):
        limiter = CapacityLimiter(limiter)

    run = (
        _gather_execute
        if limiter is None
        else partial(_gather_execute_limited, limiter)
    )

    async with create_task_group() as group:
        for func in funcs:
            result.append(None)
            group.start_soon(run, func, result, len(result) - 1)

    # At this point all None instances from result should be gone
    return cast(list[T], result)


class ObservableValue(Generic[T]):
    """Object that combines an asyncio-style condition and a value. Tasks may
    observe value changes in the object either by waiting for the value to
    change using the `wait()` method, or by starting a generator that yields
    new values by using the ObservableValue_ in an `async for` loop.

    Note that observers of the value are not guaranteed to receive all values
    if the value is changing rapidly. Whenever the generator running the observer
    task gets waken up, it retrieves the _current_ value and yields that to the
    caller.
    """

    @classmethod
    def constant(cls, value: T):
        return cls(value)

    def __init__(self, value: T):
        """Constructor.

        Parameters:
            value: the initial value
        """
        self._condition = Condition()
        self._value = value

    async def set(self, value: T, force: bool = False) -> None:
        """Sets a new value and notifies all tasks waiting for the value.

        Does not notify other tasks if the new value is exactly the same as
        the old one, unless `force` is set to `True`.
        """
        if value == self._value and not force:
            return

        self._value = value

        async with self._condition:
            self._condition.notify_all()

    update = set

    @property
    def value(self) -> T:
        """Returns the current value of the observable."""
        return self._value

    async def wait(self) -> T:
        """Waits for the value to change, and returns the most recent value
        at the earliest possible occasion.
        """
        async with self._condition:
            await self._condition.wait()
            return self._value

    async def wait_for(self, expected: T) -> T:
        """Blocks until the value becomes equal to an expected value."""
        async with self._condition:
            while True:
                if self._value == expected:
                    return self._value

                await self._condition.wait()

    async def wait_until(self, predicate: Callable[[T], bool]) -> T:
        """Blocks until the value satisfies a predicate."""
        async with self._condition:
            while True:
                if predicate(self._value):
                    return self._value

                await self._condition.wait()

    def __aiter__(self):
        return self._observe()

    async def _observe(self):
        last_value = self._value
        yield last_value

        while True:
            if last_value != self._value:
                last_value = self._value
                yield last_value
                # By the time we get back here, the value might have changed
                # again so we cannot call wait() now, we need to loop and
                # compare the current value with the last one
            else:
                await self.wait()


class ThreadContext(Generic[T]):
    """Context manager that spawns a thread when entering the context and
    kills the thread upon exiting the context.
    """

    Target: TypeAlias = Callable[[Queue, Callable[[Any], None]], None]
    """Type alias for the target function of a ThreadContext."""

    SetupFunc: TypeAlias = Callable[[], None]
    """Type alias for the setup function of a ThreadContext."""

    TeardownFunc: TypeAlias = Callable[
        [type[BaseException] | None, BaseException | None, TracebackType | None], None
    ]
    """Type alias for the teardown function of a ThreadContext."""

    _queue: Queue[T | None] | None
    _task_group: TaskGroup | None
    _value: AwaitableValue[T] | None

    @classmethod
    def create_reader(
        cls,
        reader: Callable[[], T],
        queue_to_caller: ObjectStream[T],
        *,
        setup: SetupFunc | None = None,
        teardown: TeardownFunc | None = None,
        skip: T | None = None,
        **kwds,
    ) -> "ThreadContext":
        """Convenience constructor for a common use-case: the thread is
        executing a blocking reader function in an infinite loop and sends the
        return values of the function in a queue back to the caller.

        Parameter:
            reader: the reader function to call periodically in the reader
                thread
            queue_to_caller: the queue in which the return values of the reader
                function will be placed
            setup: an optional setup function that will be called with no
                arguments _before_ the reader thread enters its main loop.
            teardown: an optional teardown function that will be called with
                an exception type, value and the associated traceback when the
                thread exits its main loop. The exception type, value and
                traceback are all `None` if the main loop terminated without
                an exception.
            skip: when specified, return values from the reader function that
                are equal to this value (using the ``is`` operator) will not be
                put into the queue that leads back to the caller.

        Additional keyword arguments are forwarded intact to the ThreadContext_
        constructor.
        """

        def respond_from_reader(value: T) -> None:
            from_thread.run(queue_to_caller.send, value)

        def reader_thread(queue: Queue, on_started: Callable[[Any], None]) -> None:
            if setup:
                setup()

            on_started(queue_to_caller.receive)

            try:
                while True:
                    if not queue.empty():
                        item = queue.get()
                        if item is None:
                            break

                    value = reader()
                    if value is not skip:
                        respond_from_reader(value)

            finally:
                if teardown:
                    exc_type, exc, exc_tb = exc_info()
                    assert exc_type is not None
                    assert exc is not None
                    assert exc_tb is not None
                    teardown(exc_type, exc, exc_tb)

        return cls(target=reader_thread, **kwds)

    @classmethod
    def create_worker(
        cls,
        *,
        setup: SetupFunc | None = None,
        teardown: TeardownFunc | None = None,
        **kwds,
    ) -> "ThreadContext":
        """Convenience constructor for a common use-case: the thread is
        executing an infinite loop that receives functions to call via a queue,
        executes the functions sequentially, and passes the return values
        back to the caller in an AwaitableValue_.

        Parameter:
            setup: an optional setup function that will be called with no
                arguments _before_ the worker thread enters its main loop.
            teardown: an optional teardown function that will be called with
                an exception type, value and the associated traceback when the
                thread exits its main loop. The exception type, value and
                traceback are all `None` if the main loop terminated without
                an exception.

        Additional keyword arguments are forwarded intact to the ThreadContext_
        constructor.
        """

        def respond_from_worker(container: AwaitableValue, value: Any) -> None:
            from_thread.run_sync(container.set, value)

        def worker_thread(queue: Queue, on_started: Callable[[Any], None]):
            if setup:
                setup()

            sender = queue.put_nowait
            closed = False

            async def call_in_worker(func, *args, **kwargs):
                """Helper function that calls the given function in the worker
                thread with the given positional and keyword arguments.
                """
                if closed:
                    # This may happen if the main event loop has already sent
                    # us a request to terminate, but other tasks in the main
                    # event loop managed to sneak in some more requests into
                    # the queue. This should be handled better; for instance,
                    # these tasks should not be allowed to send requests once
                    # the queue is closed.
                    raise RuntimeError("Worker thread already closed")

                value = AwaitableValue()
                sender((func, args, kwargs, partial(respond_from_worker, value)))
                outcome = await value.wait()
                return outcome.unwrap()

            on_started(call_in_worker)

            try:
                while True:
                    item = queue.get()
                    if item is None:
                        closed = True
                        break

                    func, args, kwargs, responder = item
                    result = capture(func, *args, **kwargs)
                    if responder:
                        responder(result)

            finally:
                if teardown:
                    exc_type, exc, exc_tb = exc_info()
                    teardown(exc_type, exc, exc_tb)

        return cls(target=worker_thread, **kwds)

    def __init__(
        self, target: Target, *, queue_factory: Callable[[], Queue[T | None]] = Queue
    ):
        """Constructor.

        Parameters:
            target: the function to call in a separate thread. It will receive
                a Queue_ as its first argument and a function as its second
                argument. The thread should first call the function when it has
                performed any preparations that the caller should wait for. The
                thread should then regularly retrieve items from the queue and
                stop immediately if it receives `None`.
            queue_factory: a callable that constructs a new Queue_ instance when
                invoked with no arguments. You may use it to pass your own
                Queue_ subclass if needed.
        """
        self._queue = None
        self._task_group = None
        self._value = None

        self._queue_factory = queue_factory
        self._target = target

    def _notify_thread_started(self, value: T | None = None) -> None:
        if self._value is None:
            raise RuntimeError("self._value must not be None here")
        else:
            from_thread.run_sync(self._value.set, value)

    async def __aenter__(self) -> T | Callable[[T], None]:
        if self._task_group is not None:
            raise RuntimeError("thread is already running")

        self._queue = self._queue_factory()
        self._value = AwaitableValue()

        self._task_group = create_task_group()
        await self._task_group.__aenter__()

        success = False
        result: T | None = None
        try:
            self._task_group.start_soon(
                to_thread.run_sync,
                self._target,
                self._queue,
                self._notify_thread_started,
            )
            result = await self._value.wait()
            success = True
        finally:
            if not success:
                await self._task_group.__aexit__(*exc_info())
                self._task_group = None

        return result if result is not None else self._queue.put_nowait

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        if self._queue:
            self._queue.put(None)
            self._queue = None

        try:
            if self._task_group:
                try:
                    with collapse_excgroups():
                        return bool(
                            await self._task_group.__aexit__(exc_type, exc_val, exc_tb)
                        )
                finally:
                    self._task_group = None
            else:
                return False
        finally:
            self._value = None
