"""Utility functions related to concurrency management."""

from anyio import (
    create_event,
    create_task_group,
    run_async_from_thread,
    run_in_thread,
    TaskGroup,
)
from functools import partial
from outcome import capture
from queue import Full, Queue
from typing import Any, Callable, Generic, Optional, TypeVar
from sys import exc_info

__all__ = ("AwaitableValue", "Full", "ThreadContext")

T = TypeVar("T")


class AwaitableValue(Generic[T]):
    """Object that combines an asyncio-style event and a value. Initially, the
    object contains no value and the corresponding event is not set. Tasks may
    wait for the object to be populated with a value.
    """

    def __init__(self):
        """Constructor."""
        self._event = create_event()
        self._value = None

    async def wait(self) -> T:
        """Waits for the value to be populated, and returns the value when
        it is populated.
        """
        await self._event.wait()
        return self._value

    async def set(self, value: T) -> None:
        """Sets a value and notifies all tasks waiting for the value."""
        if self._event.is_set():
            raise RuntimeError("awaitable value is already set")

        self._value = value
        await self._event.set()


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

            async def call_in_worker(func, *args, **kwargs):
                """Helper function that calls the given function in the worker
                thread with the given positional and keyword arguments.
                """
                value = AwaitableValue()
                sender((func, args, kwargs, partial(respond_from_worker, value)))
                outcome = await value.wait()
                return outcome.unwrap()

            on_started(call_in_worker)

            try:
                while True:
                    item = queue.get()
                    if item is None:
                        break

                    func, args, kwargs, responder = item
                    result = capture(func, *args, **kwargs)
                    if responder:
                        responder(result)

            finally:
                if teardown:
                    teardown(*exc_info())

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
            self._queue.put(None)
            self._queue = None

        if self._task_group:
            await self._task_group.__aexit__(exc_type, exc_value, tb)
            self._task_group = None

        self._value = None
