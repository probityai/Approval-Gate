"""Shared validation and artifact binding for every claim adapter."""

from __future__ import annotations

import hashlib
import os
import stat
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CaseError(ValueError):
    """The case or consumer policy is malformed; no verdict was reached."""


def _object(value: Any, label: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise CaseError(f"{label}: expected fields {sorted(required)}, optional {sorted(optional)}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaseError(f"{label}: expected nonempty string")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise CaseError(f"{label}: expected lowercase SHA-256")
    return value


def _instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise CaseError(f"{label}: expected UTC RFC 3339 seconds (ending Z)")
    try:
        instant = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CaseError(f"{label}: invalid UTC instant") from exc
    return instant


def _descriptor_io_supported() -> bool:
    """Fail closed where Python cannot anchor non-following descriptor reads."""
    return (os.name == "posix" and os.open in os.supports_dir_fd
            and os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks
            and all(hasattr(os, name) for name in
                    ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC")))


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _directory_descriptor(parent: int, name: str, label: str) -> int:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode):
        raise CaseError(f"{label}.path: symlink traversal refused")
    if not stat.S_ISDIR(before.st_mode):
        raise CaseError(f"{label}.path: expected directory")
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                             | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError as exc:
        raise CaseError(f"{label}.path: directory disappeared during open") from exc
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISDIR(after.st_mode) or not _same_inode(before, after):
            raise CaseError(f"{label}.path: directory changed during open")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_bound_artifact(root: Path, path: PurePosixPath, length: int, label: str) -> bytes:
    """Read only a regular inode reached through anchored directory descriptors.

    Renames after a directory opens do not redirect its descriptor. The consumed
    inode and bytes are checked; this does not authenticate an immutable host.
    """
    if not _descriptor_io_supported():
        raise CaseError(f"{label}: secure descriptor artifact reads unsupported on this platform")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                        | os.O_NONBLOCK | os.O_CLOEXEC)
    descriptor = None
    try:
        # Do not resolve symlinks in the consumer's root or any ancestor.
        root_parts = Path(os.path.abspath(root)).parts[1:]
        for component in (*root_parts, *path.parts[:-1]):
            child = _directory_descriptor(directory, component, label)
            os.close(directory)
            directory = child
        name = path.parts[-1]
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise CaseError(f"{label}.path: symlink traversal refused")
        if not stat.S_ISREG(before.st_mode):
            raise CaseError(f"{label}.path: expected regular file")
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                                 | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError as exc:
            raise CaseError(f"{label}.path: file disappeared during open") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_inode(before, opened):
            raise CaseError(f"{label}.path: file changed during open")
        if opened.st_size != length:
            raise CaseError(f"{label}: artifact binding_mismatch (length)")
        chunks = []
        remaining = length + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise CaseError(f"{label}: artifact changed during read")
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _artifact(root: Path, metadata: Any, label: str) -> tuple[bytes | None, dict]:
    meta = _object(metadata, label, {"path", "sha256", "length"})
    name = _string(meta["path"], f"{label}.path")
    path = PurePosixPath(name)
    if ("\x00" in name or path.is_absolute() or name != str(path)
            or any(p in (".", "..") for p in name.split("/"))):
        raise CaseError(f"{label}.path: must be a normalized relative path")
    digest = _digest(meta["sha256"], f"{label}.sha256")
    length = meta["length"]
    if type(length) is not int or not 0 <= length <= MAX_ARTIFACT_BYTES:
        raise CaseError(f"{label}.length: expected integer from 0 to {MAX_ARTIFACT_BYTES}")
    try:
        data = _read_bound_artifact(root, path, length, label)
    except FileNotFoundError:
        return None, {"status": "unavailable", "path": name}
    except OSError as exc:
        raise CaseError(f"{label}: unsafe or unreadable artifact") from exc
    actual = hashlib.sha256(data).hexdigest()
    if len(data) != length or actual != digest:
        raise CaseError(f"{label}: artifact binding_mismatch (length or SHA-256)")
    return data, {"status": "bound", "path": name, "sha256": actual, "length": length}


def _read_input(path: Path, maximum: int, label: str) -> bytes:
    """Read a consumer control file through the same secure inode boundary."""
    size = path.lstat().st_size
    if size > maximum:
        raise CaseError(f"{label}: input exceeds byte budget")
    return _read_bound_artifact(path.parent, PurePosixPath(path.name), size, label)


def _normalized(data: bytes, label: str) -> str:
    try:
        decoded = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CaseError(f"{label}: expected UTF-8 text") from exc
    return " ".join(unicodedata.normalize("NFC", decoded).split())
