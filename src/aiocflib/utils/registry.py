from typing import Callable, Generic, Optional, TypeVar, Union, overload


T = TypeVar("T")


class Registry(Generic[T]):
    """Generic registry object that associates string keys to factory
    functions.
    """

    def __init__(self):
        """Constructor."""
        self._items = {}

    def find(self, key: str, **kwds) -> T:
        """Finds an item in the registry with the given key.

        Parameters:
            key: the key to look up

        Keyword arguments:
            default: the default value to return if there is no value
                associated to the key

        Returns:
            the value associated to the key, or the default value if there is
            no such key and a default value is provided

        Raises:
            KeyError: if there is no such item for the given key, and no default
                value was provided
        """
        try:
            return self._items[key]
        except KeyError:
            if "default" in kwds:
                return kwds["default"]
            else:
                raise

    @overload
    def register(self, key: str) -> Callable[[T], T]:
        ...

    @overload
    def register(self, key: str, value: T) -> T:
        ...

    def register(
        self, key: str, value: Optional[T] = None
    ) -> Union[T, Callable[[T], T]]:
        """When called with two arguments, associates an item to the given key
        and checks for duplicates to ensure that already registered items cannot
        be overridden. When called with a single argument, returns a decorator
        that can be applied to a value to register it.

        Parameters:
            key: the key to register the item to
            value: the value to register, or `None` to return a decorator
        """
        if value is not None:
            existing = self._items.get(key)
            if existing:
                raise ValueError(
                    "Name {0!r} is already registered for {1!r}".format(key, existing)
                )
            self._items[key] = value
            return value
        else:

            def decorator(item):
                if item is not None:
                    return self.register(key, item)
                else:
                    raise ValueError("None cannot be registered")

            return decorator
