"""Protects the FACEIT key stored in settings.json (spec: Settings and data, The FACEIT key).

Windows' data protection API (DPAPI), through ctypes: ``CryptProtectData`` / ``CryptUnprotectData``,
scoped to the current Windows account (no optional entropy, no description string, not machine-wide).
Only the account that called ``protect()`` on a secret can call ``unprotect()`` and get it back;
another account, or a corrupted or foreign blob, raises ``ProtectError``.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class ProtectError(ValueError):
    """A protected value cannot be decrypted: a different Windows account, or a corrupted blob."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_of(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _bytes_of(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect(secret: str) -> str:
    """Encrypt ``secret`` for the current Windows account only. Returns base64 text."""
    in_blob = _blob_of(secret.encode("utf-8"))
    out_blob = _DataBlob()
    crypt_protect_data = ctypes.windll.crypt32.CryptProtectData
    crypt_protect_data.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt_protect_data.restype = wintypes.BOOL
    try:
        ok = crypt_protect_data(
            ctypes.byref(in_blob), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)
        )
        if not ok:
            raise ctypes.WinError()
        return base64.b64encode(_bytes_of(out_blob)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def unprotect(blob: str) -> str:
    """Decrypt text from ``protect``. Raises ``ProtectError`` on any failure (bad base64, a
    corrupted or non-DPAPI blob, a different Windows account) without ever including ``blob`` or the
    decrypted bytes in the error."""
    try:
        data = base64.b64decode(blob, validate=True)
    except ValueError:
        raise ProtectError("cannot be decrypted") from None

    in_blob = _blob_of(data)
    out_blob = _DataBlob()
    crypt_unprotect_data = ctypes.windll.crypt32.CryptUnprotectData
    crypt_unprotect_data.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt_unprotect_data.restype = wintypes.BOOL
    try:
        ok = crypt_unprotect_data(
            ctypes.byref(in_blob), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)
        )
        if not ok:
            raise ProtectError("cannot be decrypted")
        plain = _bytes_of(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)

    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtectError("cannot be decrypted") from None
