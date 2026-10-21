"""Capability-based, symlink-safe filesystem root.

The allowed directory is opened once. Every later access walks one path
component at a time with ``O_NOFOLLOW`` relative to that file descriptor, so a
string-prefix check is never the thing standing between a request and the
filesystem, and a symlink swapped in after a path check cannot redirect a read.
"""
from __future__ import annotations

import errno
import fnmatch
import hashlib
import os
import stat
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock

# The configured root may itself be a symlink; the operator chose it, and the
# directory fd keeps pointing at that directory even if the path is replaced.
_ROOT_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: a FIFO planted in the root must not hang a worker thread.
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

_DENIED = (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM)


class SandboxError(Exception):
    """Base class. ``code`` is a stable, machine-readable reason."""

    code = "sandbox_error"


class InvalidPath(SandboxError):
    code = "invalid_path"


class NotFound(SandboxError):
    code = "not_found"


class Denied(SandboxError):
    code = "denied"


class TooLarge(SandboxError):
    code = "too_large"


class BinaryContent(SandboxError):
    code = "binary_content"


@dataclass(frozen=True, slots=True)
class Policy:
    include: tuple[str, ...] = ("*.md", "*.txt")
    exclude: tuple[str, ...] = (".*", "*.key", "*.pem", ".env*")
    max_file_bytes: int = 8 * 1024 * 1024
    max_depth: int = 8

    def allows(self, rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        parts = rel.split("/")
        if any(fnmatch.fnmatchcase(part, pattern) for part in parts for pattern in self.exclude):
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.include)


@dataclass(frozen=True, slots=True)
class FileKey:
    dev: int
    ino: int
    mtime_ns: int
    size: int

    @property
    def etag(self) -> str:
        raw = f"{self.dev}:{self.ino}:{self.mtime_ns}:{self.size}".encode()
        return hashlib.blake2b(raw, digest_size=8).hexdigest()


class _LRU:
    """Byte-budgeted cache. The key includes mtime and size, so edits miss."""

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self._used = 0
        self._data: OrderedDict[FileKey, bytes] = OrderedDict()
        self._lock = Lock()

    def get(self, key: FileKey) -> bytes | None:
        with self._lock:
            blob = self._data.get(key)
            if blob is not None:
                self._data.move_to_end(key)
            return blob

    def put(self, key: FileKey, blob: bytes) -> None:
        if len(blob) > self._budget:
            return
        with self._lock:
            if key in self._data:
                return
            self._data[key] = blob
            self._used += len(blob)
            while self._used > self._budget:
                _, old = self._data.popitem(last=False)
                self._used -= len(old)


def _validate(rel: str) -> tuple[str, ...]:
    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel:
        raise InvalidPath("path must be a non-empty relative POSIX path")
    path = PurePosixPath(rel)
    if path.is_absolute() or any(part in ("..", ".") for part in path.parts):
        raise InvalidPath("path must not be absolute or contain '.' / '..'")
    return path.parts


class Root:
    def __init__(
        self,
        path: str | os.PathLike[str],
        policy: Policy,
        cache_bytes: int = 32 << 20,
    ) -> None:
        real = Path(path).expanduser().resolve(strict=True)
        self.fd = os.open(real, _ROOT_FLAGS)
        self.policy = policy
        self._cache = _LRU(cache_bytes)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def _open(self, parts: tuple[str, ...]) -> int:
        """Walk components under the root fd. A symlink anywhere yields ELOOP."""
        directory = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                try:
                    nxt = os.open(part, _DIR_FLAGS, dir_fd=directory)
                except OSError as exc:
                    raise self._translate(exc) from None
                os.close(directory)
                directory = nxt
            try:
                return os.open(parts[-1], _FILE_FLAGS, dir_fd=directory)
            except OSError as exc:
                raise self._translate(exc) from None
        finally:
            os.close(directory)

    @staticmethod
    def _translate(exc: OSError) -> SandboxError:
        if exc.errno == errno.ENOENT:
            return NotFound("no such document")
        if exc.errno == errno.ENAMETOOLONG:
            return InvalidPath("path is too long")
        if exc.errno in _DENIED:
            return Denied("access denied")
        return exc  # type: ignore[return-value]

    def read(self, rel: str, offset: int, limit: int) -> tuple[bytes, FileKey]:
        if offset < 0 or limit < 0:
            raise InvalidPath("offset and limit must be non-negative")
        parts = _validate(rel)
        if not self.policy.allows("/".join(parts)):
            raise Denied("access denied")
        fd = self._open(parts)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise Denied("not a regular file")
            if info.st_size > self.policy.max_file_bytes:
                raise TooLarge(f"file exceeds {self.policy.max_file_bytes} bytes")
            key = FileKey(info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
            blob = self._cache.get(key)
            if blob is None:
                blob = os.pread(fd, info.st_size, 0)
                if b"\x00" in blob:
                    raise BinaryContent("file appears to be binary")
                self._cache.put(key, blob)
            return blob[offset : offset + limit], key
        finally:
            os.close(fd)

    def list(self) -> list[tuple[str, int]]:
        """Recursive listing through directory fds. Symlinks are never followed."""
        found: list[tuple[str, int]] = []

        def walk(directory: int, prefix: str, depth: int) -> None:
            with os.scandir(directory) as entries:
                for entry in entries:
                    rel = f"{prefix}{entry.name}"
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        is_file = entry.is_file(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir and depth < self.policy.max_depth:
                        if any(fnmatch.fnmatchcase(entry.name, pattern) for pattern in self.policy.exclude):
                            continue
                        try:
                            sub = os.open(entry.name, _DIR_FLAGS, dir_fd=directory)
                        except OSError:
                            continue
                        try:
                            walk(sub, rel + "/", depth + 1)
                        finally:
                            os.close(sub)
                    elif is_file and self.policy.allows(rel):
                        try:
                            size = entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
                        found.append((rel, size))

        dup = os.dup(self.fd)
        try:
            walk(dup, "", 0)
        finally:
            os.close(dup)
        found.sort()
        return found
