"""Windows helpers: the real Downloads and Videos folders, keeping the PC awake during a render, how long the PC
has been idle, showing a file in Explorer, and what registers the app with Windows (its toasts' identity and its
cs2clipper: links)."""

from __future__ import annotations

import ctypes
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# FOLDERID_Downloads and FOLDERID_Videos, from KnownFolders.h
_FOLDERID_DOWNLOADS = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")
_FOLDERID_VIDEOS = uuid.UUID("18989B1D-99B5-455B-841C-AB7C74E4DDFC")
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def downloads_dir() -> Path:
    """The user's Downloads folder as Windows knows it. Never derived from USERPROFILE, which a child
    process can be given another of."""
    return _known_folder(_FOLDERID_DOWNLOADS)


def videos_dir() -> Path:
    """The user's Videos folder as Windows knows it: where a fresh install keeps its clips."""
    return _known_folder(_FOLDERID_VIDEOS)


def _known_folder(folder_id: uuid.UUID) -> Path:
    guid = ctypes.create_string_buffer(folder_id.bytes_le, 16)
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


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """How long since the last keyboard or mouse input on this PC, in seconds."""
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo failed")
    tick_count = ctypes.windll.kernel32.GetTickCount
    tick_count.restype = ctypes.c_uint32
    return ((tick_count() - info.dwTime) & 0xFFFFFFFF) / 1000


def set_user_value(key: str, name: str, value: str) -> None:
    """Sets the string `name` (the key's default value when "") of `key` under HKEY_CURRENT_USER, making the
    key first when it is missing."""
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def set_app_id(app_id: str) -> None:
    """Gives this process the app's AppUserModelID, so its window shares a taskbar button with the Start menu
    shortcut that carries the same id."""
    result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    if result != 0:
        raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed with HRESULT {result & 0xFFFFFFFF:#010x}")


def reveal(path: Path) -> None:
    """Opens Explorer on `path`'s folder with `path` selected. A Windows path holds no quote to break out of."""
    subprocess.Popen(f'explorer.exe /select,"{path}"')
