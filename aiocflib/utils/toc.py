"""Classes and functions related to the handling and caching of parameter
and log table-of-contents entries from a Crazyflie.
"""

from abc import abstractmethod, ABCMeta
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional, Union
from aiocflib.utils.registry import Registry


#: Type alias for items stored in a TOC cache
TOCItem = bytes

#: Type alias for TOC namespaces
Namespace = str

#: Type alias for objects from which we can create a TOC cache instance
TOCCacheLike = Union[str, Path, "TOCCache"]


class TOCCache(metaclass=ABCMeta):
    """Interface specification for table-of-contents caches."""

    @classmethod
    def create(cls, spec: TOCCacheLike):
        """Creates a table-of-contents cache from a URI-style string
        specification or a filesystem path.

        Parameters:
            spec: a Python Path_ object pointing to a folder in the
                filesystem, a URI-style cache specification, or a string
                pointing to a folder in the filesystem
        """
        if isinstance(spec, TOCCache):
            return spec
        elif isinstance(spec, Path):
            raise NotImplementedError("filesystem TOC cache not implemented yet")
        else:
            scheme, sep, rest = spec.partition("://")
            if not sep:
                # No URI separator so this is simply a filesystem path
                raise NotImplementedError("filesystem TOC cache not implemented yet")
            else:
                try:
                    factory = TOCCacheRegistry.find(scheme)
                except KeyError:
                    raise KeyError("no such TOC cache type: {0!r}".format(scheme))

                cache = factory()
                cache._configure(rest)

            return cache

    def _configure(self, uri: str) -> None:
        """Configures the cache instance from the given URI specification.

        This method is not public; use the `TOCCache.create()` constructor to
        create a TOC cache from a URI.

        Since the method is not meant for public use, it does _not_ check
        whether the received URI has a scheme that corresponds to the TOC cache
        type; it is assumed that the caller already took care of it.

        Parameters:
            uri: the URI to create the cache instance from
        """
        pass

    async def find(
        self, hash: bytes, namespace: Optional[Namespace] = None
    ) -> Iterable[TOCItem]:
        """Looks up a cached table-of-contents by its hash value.

        Parameters:
            hash: the hash code to look up
            namespace: the namespace that the hash value should be looked up
                in. Useful if the same TOC cache is used to store different
                types of TOC items.

        Raises:
            KeyError: if no cached table-of-contents entries exist for the
                given hash value
        """
        raise NotImplementedError

    def namespace(self, namespace: str) -> "TOCCache":
        """Returns another TOC cache instance that is restricted to the given
        namespace.

        Retrieving or setting a hash in the base namespace of the returned
        instance will retrieve or set it in the given namespace of the wrapped
        instance.
        """
        return NamespacedTOCCacheWrapper(self, namespace)

    @abstractmethod
    async def store(
        self,
        hash: bytes,
        items: Iterable[TOCItem],
        namespace: Optional[Namespace] = None,
    ) -> None:
        """Stores a list of table-of-contents entries under the given hash code.

        Parameters:
            hash: the hash code to use up
            items: the entries in the table-of-contents object to store
            namespace: the namespace that the hash value should be looked up
                in. Useful if the same TOC cache is used to store different
                types of TOC items.

        """
        raise NotImplementedError


#: Type alias for factory functions that can create a TOC cache instance
TOCCacheFactory = Callable[[], TOCCache]

#: Mapping that maps names to the corresponding TOCCache classes
TOCCacheRegistry = Registry()  # type: Registry[TOCCacheFactory]


@TOCCacheRegistry.register("memory")
class InMemoryTOCCache(TOCCache):
    """TOC cache specialization that stores the table-of-contents entries
    purely in memory.
    """

    def __init__(self):
        """Constructor."""
        self._namespaces = defaultdict(dict)

    async def find(
        self, hash: bytes, namespace: Optional[Namespace] = None
    ) -> Iterable[TOCItem]:
        items = self._namespaces.get(namespace)
        if not items:
            raise KeyError("no such namespace: {0!r}".format(namespace))

        try:
            return items[hash]
        except KeyError:
            raise KeyError("no such hash: {0!r}".format(hash)) from None

    async def has(self, hash: bytes, namespace: Optional[Namespace] = None) -> bool:
        try:
            await self.find(hash, namespace)
            return True
        except KeyError:
            return False

    async def store(
        self,
        hash: bytes,
        items: Iterable[TOCItem],
        namespace: Optional[Namespace] = None,
    ) -> None:
        self._namespaces[namespace][hash] = tuple(items)


class NamespacedTOCCacheWrapper(TOCCache):
    """TOCCache implementation that wraps another TOC cache and ensures that
    the operations are performed only in a given namespace of the wrapped
    instance.
    """

    def __init__(self, wrapped: TOCCache, namespace: Namespace, sep: str = "."):
        self._wrapped = wrapped
        self._namespace = namespace
        self._separator = sep

    async def find(
        self, hash: bytes, namespace: Optional[Namespace] = None
    ) -> Iterable[TOCItem]:
        return await self._wrapped.find(hash, self._remap(namespace))

    async def has(self, hash: bytes, namespace: Optional[Namespace] = None) -> bool:
        return await self._wrapped.has(hash, self._remap(namespace))

    async def store(
        self,
        hash: bytes,
        items: Iterable[TOCItem],
        namespace: Optional[Namespace] = None,
    ) -> None:
        return await self._wrapped.store(hash, items, self._remap(namespace))

    def _remap(self, namespace: Optional[Namespace]) -> Namespace:
        if namespace is None:
            return self._namespace
        else:
            return self._separator.join(self._namespace, namespace)
