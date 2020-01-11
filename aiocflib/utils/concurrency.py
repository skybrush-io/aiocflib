"""Utility functions related to concurrency management."""

from anyio import (
    CapacityLimiter,
    create_capacity_limiter,
    create_condition,
    create_event,
    create_task_group,
    open_cancel_scope,
    run_async_from_thread,
    run_in_thread,
    TaskGroup,
)
from async_generator import async_generator, yield_
from functools import partial
from inspect import iscoroutinefunction
from outcome import capture
from queue import Full, Queue
from sys import exc_info
from typing import (
    Any,
    Awaitable,
    Callable,
    Generic,
    Iterable,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

__all__ = (
    "AwaitableValue",
    "create_daemon_task_group",
    "Full",
    "ObservableValue",
    "ThreadContext",
)

T = TypeVar("T")

TaskStartedNotifier = Callable[[], Awaitable[None]]


class AwaitableValue(Generic[T]):
    """Object that combines an asyncio-style event and a value. Initially, the
    object contains no value and the corresponding event is not set. Tasks may
    wait for the object to be populated with a value.
    """

    def __init__(self):
        """Constructor."""
        self._event = create_event()
        self._value = None

    async def set(self, value: T) -> None:
        """Sets a value and notifies all tasks waiting for the value."""
        if self._event.is_set():
            raise RuntimeError("awaitable value is already set")

        self._value = value
        await self._event.set()

    async def wait(self) -> T:
        """Waits for the value to be populated, and returns the value when
        it is populated.
        """
        await self._event.wait()
        return self._value


class DaemonTaskGroup(TaskGroup):
    """Task group that cancels all its child tasks when the execution is about
    to leave the context (instead of waiting for the child tasks to finish).
    """

    def __init__(self):
        self._cancel_scopes = []
        self._task_group = None
        self._spawner = None

    async def __aenter__(self):
        self._task_group = create_task_group()
        self._spawner = await self._task_group.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        for cancel_scope in reversed(self._cancel_scopes):
            try:
                await cancel_scope.cancel()
            except Exception:
                # well, meh
                pass
        return await self._task_group.__aexit__(exc_type, exc_value, tb)

    async def spawn(self, func, *args, **kwds):
        scope = open_cancel_scope()
        self._cancel_scopes.append(scope)
        try:
            return await self._spawner.spawn(self._run, scope, func, *args, **kwds)
        except Exception:
            self._cancel_scopes.remove(scope)
            raise

    async def spawn_and_wait_until_started(self, func, *args, **kwds):
        on_opened = create_event()
        await self.spawn(partial(func, notify_started=on_opened.set), *args, **kwds)
        await on_opened.wait()

    async def _run(self, scope, func, *args, **kwds):
        try:
            async with scope:
                return await func(*args, **kwds)
        finally:
            self._cancel_scopes.remove(scope)


create_daemon_task_group = DaemonTaskGroup


async def _gather_execute(func: Callable[..., T], args: Any, result: List, index: int):
    result[index] = await func(*args)


async def _gather_execute_limited(
    limiter: Optional[CapacityLimiter],
    func: Callable[..., T],
    args: Any,
    result: List,
    index: int,
):
    async with limiter:
        result[index] = await func(*args)


async def gather(
    funcs: Iterable[Union[Callable[[], T], Tuple[Callable[..., T], ...]]],
    limiter: Optional[Union[CapacityLimiter, int]] = None,
):
    to_execute = [
        (func, ()) if callable(func) else (func[0], func[1:]) for func in funcs
    ]
    result = []

    if isinstance(limiter, int):
        limiter = create_capacity_limiter(limiter)

    run = (
        _gather_execute
        if limiter is None
        else partial(_gather_execute_limited, limiter)
    )

    async with create_task_group() as group:
        for func, args in to_execute:
            if iscoroutinefunction(func):
                result.append(None)
                await group.spawn(run, func, args, result, len(result) - 1)
            else:
                result.append(func(*args))

    return result


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
        self._condition = create_condition()
        self._value = value

    async def set(self, value: T) -> None:
        """Sets a new value and notifies all tasks waiting for the value."""
        async with self._condition:
            self._value = value
            await self._condition.notify_all()

    update = set

    @property
    def value(self) -> T:
        """Returns the current value of the observable."""
        return self._value

    async def wait(self) -> T:
        """Waits for the value to be change, and returns the most recent value
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

    @async_generator
    async def _observe(self):
        await yield_(self._value)
        while True:
            await yield_(await self.wait())


class ThreadContext(Generic[T]):
    """Context manager that spawns a thread when entering the context and
    kills the thread upon exiting the context.
    """

    #: Type alias for the target function of a ThreadContext
    Target = Callable[[Queue, Callable[[], None]], None]

    @classmethod
    def create_reader(
        cls, reader, queue_to_caller, *, setup=None, teardown=None, skip=None, **kwds
    ):
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

        def respond_from_reader(value: Any) -> None:
            run_async_from_thread(queue_to_caller.put, value)

        def reader_thread(queue: Queue, on_started: Callable[[Any], None]):
            if setup:
                setup()

            on_started(queue_to_caller.get)

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
                    teardown(*exc_info())

        return cls(target=reader_thread, **kwds)

    @classmethod
    def create_worker(cls, *, setup=None, teardown=None, **kwds):
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
            run_async_from_thread(container.set, value)

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
                    teardown(*exc_info())

            print("Exited worker")

        return cls(target=worker_thread, **kwds)

    def __init__(self, target: Target, *, queue_factory: Callable[[], Queue] = Queue):
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
        self._queue = None  # type: Optional[Queue]
        self._task_group = None  # type: Optional[TaskGroup]
        self._value = None  # type: Optional[AwaitableValue]

        self._queue_factory = queue_factory
        self._target = target

    def _notify_thread_started(self, value: Any = None) -> None:
        if self._value is None:
            raise RuntimeError("self._value must not be None here")
        else:
            run_async_from_thread(self._value.set, value)

    async def __aenter__(self) -> T:
        print("Entering ThreadContext")
        if self._task_group is not None:
            raise RuntimeError("thread is already running")

        self._queue = self._queue_factory()
        self._value = AwaitableValue()

        self._task_group = create_task_group()
        await self._task_group.__aenter__()

        success = False
        try:
            await self._task_group.spawn(
                run_in_thread, self._target, self._queue, self._notify_thread_started
            )
            result = await self._value.wait()
            success = True
        finally:
            if not success:
                await self._task_group.__aexit__(*exc_info())
                self._task_group = None

        return result if result is not None else self._queue.put_nowait

    async def __aexit__(self, exc_type, exc_value, tb):
        if self._queue:
            print("Sent signal to exit worker")
            self._queue.put(None)
            self._queue = None

        if self._task_group:
            await self._task_group.__aexit__(exc_type, exc_value, tb)
            self._task_group = None

        self._value = None
