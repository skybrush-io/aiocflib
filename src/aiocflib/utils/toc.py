"""Classes and functions related to the handling and caching of parameter
and log table-of-contents entries from a Crazyflie.
"""

from abc import abstractmethod, ABCMeta
from anyio import Lock, open_file
from binascii import hexlify
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from struct import Struct
from typing import Awaitable, Callable, Iterable, Optional, Union, Tuple, TypeVar

from aiocflib.utils.registry import Registry

__all__ = ("TOCCache",)


#: Type alias for items stored in a TOC cache
TOCItem = bytes

#: Type alias for TOC namespaces
Namespace = str

#: Type alias for objects from which we can create a TOC cache instance
TOCCacheLike = Union[None, str, Path, "TOCCache"]


class TOCCache(metaclass=ABCMeta):
    """Interface specification for table-of-contents caches."""

    @classmethod
    def create(cls, spec: TOCCacheLike):
        """Creates a table-of-contents cache from a URI-style string
        specification or a filesystem path.

        Parameters:
            spec: a Python Path_ object pointing to a folder in the
                filesystem, a URI-style cache specification, or a string
                pointing to a folder in the filesystem. `None` means to
                create a null cache that does nothing.
        """
        if spec is None:
            spec = "null://"

        if isinstance(spec, TOCCache):
            return spec
        elif isinstance(spec, Path):
            return cls.create(str(spec))
        else:
            scheme, sep, rest = spec.partition("://")
            if not sep:
                scheme, rest = "file", scheme

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

    async def has(self, hash: bytes, namespace: Optional[Namespace] = None) -> bool:
        """Returns whether the table-of-contents object contains an item with
        the given hash.

        Parameters:
            hash: the hash code to look up
            namespace: the namespace that the hash value should be looked up
                in. Useful if the same TOC cache is used to store different
                types of TOC items.
        """
        raise NotImplementedError

    def get_key(self) -> Optional[str]:
        """Returns a short string identifier that uniquely identifies this
        cache, or `None` if no such key can be derived.

        The purpose of this method is to allow us to create locks to
        semantically equivalent caches. The idea is that the locks are
        associated to the keys of the TOC caches; in other words, if two
        caches have the same key, then locking one of them implicitly locks the
        other and vice versa.
        """
        return None

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


@TOCCacheRegistry.register("null")
class NullTOCCache(TOCCache):
    """Null TOC cache that does nothing."""

    async def find(
        self, hash: bytes, namespace: Optional[Namespace] = None
    ) -> Iterable[TOCItem]:
        raise KeyError("no such hash: {0!r}".format(hash))

    async def has(self, hash: bytes, namespace: Optional[Namespace] = None) -> bool:
        return False

    def get_key(self) -> Optional[str]:
        return "null"

    async def store(
        self,
        hash: bytes,
        items: Iterable[TOCItem],
        namespace: Optional[Namespace] = None,
    ) -> None:
        pass


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


@TOCCacheRegistry.register("file")
class FilesystemBasedTOCCache(TOCCache):
    """TOC cache specialization that stores the table-of-contents entries on
    a filesystem in a given folder.
    """

    _key: Optional[Path]
    _read_only: bool
    _path: Optional[Path]

    def __init__(self, read_only: bool = False):
        """Constructor.

        Parameters:
            read_only: whether the cache is read only
        """
        self._path = None
        self._read_only = bool(read_only)
        self._key = None

    def _configure(self, uri: str) -> None:
        self.path = uri

    def get_key(self) -> Optional[str]:
        if self._key is None:
            self._key = self.path.resolve() if self.path else None
        return str(self._key)

    @property
    def path(self) -> Path:
        if self._path is None:
            raise ValueError("TOC cache is not configured yet")
        return self._path

    @path.setter
    def path(self, value) -> None:
        if self._path is not None:
            raise ValueError("TOC cache is already configured")

        self._path = Path(value)
        self._key = None

    def _path_for_hash(
        self, hash: bytes, namespace: Optional[Namespace] = None
    ) -> Path:
        """Sanitizes the given hash so it can be used in a filesystem path."""
        path = self._path_for_namespace(namespace)
        return path / "{0}.bin".format(hexlify(hash).decode("ascii").lower())

    def _path_for_namespace(self, namespace: Optional[Namespace] = None) -> Path:
        """Returns the path corresponding to the given namespace on the filesystem."""
        if self._path is None:
            raise ValueError("TOC cache is not configured yet")

        result = self._path
        if namespace is not None:
            namespace = self._sanitize_namespace(namespace)

        return result / namespace if namespace else result

    @staticmethod
    def _sanitize_namespace(namespace: Namespace) -> str:
        """Sanitizes the given namespace so it can be used in a filesystem
        path.
        """
        if namespace is None:
            return ""
        return (
            namespace.encode("ascii", errors="backslashreplace")
            .replace(b"/", b"\\2f")
            .replace(b"\\", b"=")
            .replace(b".", b"/")
            .decode("ascii")
        )

    async def find(
        self, hash: bytes, namespace: Optional[Namespace] = None
    ) -> Iterable[TOCItem]:
        path = self._path_for_hash(hash, namespace)
        if not path.exists() or not path.is_file():
            raise KeyError(
                "no such namespace or hash: {0!r} / {1!r}".format(namespace, hash)
            )

        result = []

        async with await open_file(str(path), "rb") as fp:
            data = await fp.read(1)
            if not data:
                raise IOError("unexpected end of file")

            version = data[0]
            if version != 1:
                raise IOError("only version 1 TOC files are supported")

            while True:
                length = await fp.read(2)
                if length is None or len(length) < 2:
                    break

                assert isinstance(length, bytes)
                length = length[0] + (length[1] << 8)
                data = await fp.read(length)
                if len(data) < length:
                    raise IOError("unexpected end of file")

                result.append(data)

        return result

    async def has(self, hash: bytes, namespace: Optional[Namespace] = None) -> bool:
        path = self._path_for_hash(hash, namespace)
        return path.exists() and path.is_file()

    async def store(
        self,
        hash: bytes,
        items: Iterable[TOCItem],
        namespace: Optional[Namespace] = None,
    ) -> None:
        if self._read_only:
            return

        path = self._path_for_hash(hash, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)

        success = False

        try:
            async with await open_file(str(path), "wb") as fp:
                await fp.write(b"\x01")
                for item in items:
                    length = len(item)
                    if length > 65535:
                        raise IOError("exceeded maximum item length")

                    await fp.write(bytes((length & 0xFF, length >> 8)))
                    await fp.write(item)

            success = True
        finally:
            if not success:
                path.unlink()


TOCCacheRegistry.register("file+ro", partial(FilesystemBasedTOCCache, read_only=True))


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

    def get_key(self) -> Optional[str]:
        if self._wrapped is not None and self._namespace:
            root_key = self._wrapped.get_key()
            return f"{root_key}{self._separator}{self._namespace}"
        else:
            return None

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
            return self._separator.join((self._namespace, namespace))


#: Dictionary that maps TOCCache classes to dictionaries that map cache keys to their locks
_cache_locks = defaultdict(lambda: defaultdict(Lock))


@asynccontextmanager
async def _locked_cache(cache: Optional[TOCCache], key_suffix: Optional[str] = None):
    key = cache.get_key() if cache is not None else None
    if key is None:
        yield
    else:
        cls = cache.__class__
        locks = _cache_locks[cls]
        if key_suffix:
            key = f"{key}:{key_suffix}"
        async with locks[key]:
            yield


T = TypeVar("T")


async def fetch_table_of_contents_gracefully(
    cache: Optional[TOCCache],
    info_func: Callable[[], Awaitable[Tuple[int, int]]],
    single_item_fetcher_func: Callable[[int], Awaitable[T]],
    from_bytes: Callable[[bytes, int], T],
    to_bytes: Callable[[T], bytes],
):
    num_items, hash = await info_func()
    hash = Struct("<I").pack(hash)
    result = None

    async with _locked_cache(cache, key_suffix=hash.hex()):
        try:
            # Try to fetch the parameters from the cache based on the hash
            if cache:
                items = await cache.find(hash)
                result = [from_bytes(data, id) for id, data in enumerate(items)]
        except Exception:
            pass

        if result is None:
            # Retrieving the cached entries failed; let's try to fetch on
            # our own
            result = [await single_item_fetcher_func(i) for i in range(num_items)]

            # Store the fetched entries in the cache
            if cache:
                try:
                    await cache.store(hash, [to_bytes(item) for item in result])
                except Exception:
                    # Storing items in the cache failed, but let's not freak out
                    pass

    return result
