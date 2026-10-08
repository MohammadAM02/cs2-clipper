"""Windows helpers: the real Downloads folder, and keeping the PC awake during a render."""

from __future__ import annotations

import ctypes
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# FOLDERID_Downloads, from KnownFolders.h
_FOLDERID_DOWNLOADS = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def downloads_dir() -> Path:
    """The user's Downloads folder as Windows knows it. Never derived from USERPROFILE, which a child
    process can be given another of."""
    guid = ctypes.create_string_buffer(_FOLDERID_DOWNLOADS.bytes_le, 16)
    path_ptr = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path_ptr))
    if result != 0:
        raise OSError(f"SHGetKnownFolderPath failed with HRESULT {result & 0xFFFFFFFF:#010x}")
    try:
        return Path(path_ptr.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)


@contextmanager
def keep_awake() -> Iterator[None]:
    """Ask Windows not to sleep until the block ends."""
    set_state = ctypes.windll.kernel32.SetThreadExecutionState
    set_state.argtypes = [ctypes.c_uint32]
    set_state.restype = ctypes.c_uint32
    set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        set_state(_ES_CONTINUOUS)
