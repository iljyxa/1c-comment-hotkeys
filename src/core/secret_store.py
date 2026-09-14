"""Защита секретов (токенов) на диске через Windows DPAPI.

Зашифрованное значение хранится в виде строки ``dpapi:<base64>``. Значение без
префикса считается открытым текстом (legacy-конфигурация) и перешифровывается
при следующем сохранении. Расшифровать данные может только та же учетная запись
Windows, под которой они были зашифрованы.
"""

from __future__ import annotations

import base64
import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

PROTECTED_PREFIX = "dpapi:"

# Дополнительная энтропия привязывает blob к приложению: другой процесс того же
# пользователя должен знать это значение, чтобы расшифровать данные через DPAPI.
_ENTROPY = b"1CCommentHotkeys.jira_sources.v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def is_available() -> bool:
    """Вернуть `True`, если DPAPI доступен на текущей платформе."""
    return os.name == "nt"


def is_protected(value: str) -> bool:
    """Проверить, хранится ли значение в зашифрованном виде."""
    return isinstance(value, str) and value.startswith(PROTECTED_PREFIX)


def _make_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    # Буфер возвращается вместе с blob, чтобы не был освобожден сборщиком мусора.
    return blob, buffer


def _call_dpapi(function_name: str, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = getattr(crypt32, function_name)
    function.restype = wintypes.BOOL

    blob_in, _buffer_in = _make_blob(data)
    blob_entropy, _buffer_entropy = _make_blob(_ENTROPY)
    blob_out = _DataBlob()

    ok = function(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(blob_entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect(plaintext: str) -> str:
    """Зашифровать строку. Вне Windows или при ошибке возвращается открытый текст."""
    if not plaintext or is_protected(plaintext):
        return plaintext
    if not is_available():
        return plaintext
    try:
        encrypted = _call_dpapi("CryptProtectData", plaintext.encode("utf-8"))
    except Exception as exc:
        logger.warning("DPAPI недоступен, секрет будет сохранен открытым текстом: %s", exc)
        return plaintext
    return PROTECTED_PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect(value: str) -> str:
    """Расшифровать строку. Значение без префикса возвращается как есть."""
    if not is_protected(value):
        return value
    if not is_available():
        raise RuntimeError("Зашифрованный секрет можно расшифровать только в Windows")
    encrypted = base64.b64decode(value[len(PROTECTED_PREFIX):])
    decrypted = _call_dpapi("CryptUnprotectData", encrypted)
    return decrypted.decode("utf-8")
