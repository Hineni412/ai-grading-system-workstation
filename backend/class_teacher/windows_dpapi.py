from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .errors import VaultError


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _blob(value: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(value)
    return (
        _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        ),
        buffer,
    )


class WindowsCurrentUserProtection:
    """CryptProtectData adapter using CurrentUser scope.

    CRYPTPROTECT_LOCAL_MACHINE is intentionally never supplied.
    """

    @staticmethod
    def _libraries():
        if os.name != "nt":
            raise VaultError(
                "vault_windows_protection_unavailable",
                "当前系统不支持 Windows 用户保护，未创建敏感保险箱",
                status_code=503,
            )
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_pointer = ctypes.POINTER(_DataBlob)
        crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return crypt32, kernel32

    def protect_current_user(self, plaintext: bytes, entropy: bytes) -> bytes:
        crypt32, kernel32 = self._libraries()
        source, source_buffer = _blob(plaintext)
        extra, extra_buffer = _blob(entropy)
        output = _DataBlob()
        _ = (source_buffer, extra_buffer)
        if not crypt32.CryptProtectData(
            ctypes.byref(source),
            "class-teacher-sensitive-v2",
            ctypes.byref(extra),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise VaultError(
                "vault_windows_protection_failed",
                "Windows 用户保护失败，未创建敏感保险箱",
                status_code=503,
            )
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

    def unprotect_current_user(self, protected: bytes, entropy: bytes) -> bytes:
        crypt32, kernel32 = self._libraries()
        source, source_buffer = _blob(protected)
        extra, extra_buffer = _blob(entropy)
        output = _DataBlob()
        description = wintypes.LPWSTR()
        _ = (source_buffer, extra_buffer)
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            ctypes.byref(extra),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise VaultError(
                "vault_windows_binding_unavailable",
                "当前 Windows 用户无法打开敏感保险箱，请使用恢复密钥重新绑定",
                status_code=409,
            )
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            if description:
                kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


__all__ = ["WindowsCurrentUserProtection"]
