"""Защита секретов (токенов Jira, ключей LLM) на диске через Windows DPAPI.

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
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

PROTECTED_PREFIX = "dpapi:"

# Дополнительная энтропия привязывает blob к приложению: другой процесс того же
# пользователя должен знать это значение, чтобы расшифровать данные через DPAPI.
# Значение общее для всех секретов приложения; менять его нельзя — уже
# сохраненные токены перестанут расшифровываться.
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


@dataclass
class SecretsLoadResult:
    """Результат приведения прочитанных с диска секретов к открытому тексту."""

    # Значения открытым текстом; нерасшифрованные заменены пустой строкой.
    values: list[str] = field(default_factory=list)
    # Индексы значений, которые не удалось расшифровать.
    undecryptable: list[int] = field(default_factory=list)
    # Состояние файла не совпадает с настройкой шифрования, файл нужно пересохранить.
    needs_rewrite: bool = False


def unprotect_all(
    values: list[str],
    encrypt_enabled: bool,
    labels: Optional[list[str]] = None,
) -> SecretsLoadResult:
    """Расшифровать список секретов и решить, нужно ли пересохранять файл.

    Общая логика для всех репозиториев с секретами: открытые значения при
    включенном шифровании и зашифрованные при выключенном означают, что файл
    записан старой версией или настройку только что переключили. Нерасшифрованные
    значения (например, файл скопирован от другого пользователя Windows)
    обнуляются, и в этом случае файл пересохранять нельзя.

    Args:
        values: Значения секретов в том виде, как они прочитаны из файла.
        encrypt_enabled: Текущее значение настройки шифрования.
        labels: Подписи для логов (по одной на значение), например имена источников.
    """
    result = SecretsLoadResult()
    has_plaintext = False
    has_protected = False
    for index, value in enumerate(values):
        if not is_protected(value):
            if value:
                has_plaintext = True
            result.values.append(value)
            continue
        has_protected = True
        try:
            result.values.append(unprotect(value))
        except Exception as exc:
            label = labels[index] if labels and index < len(labels) else str(index)
            logger.error("Не удалось расшифровать секрет '%s': %s", label, exc)
            result.values.append("")
            result.undecryptable.append(index)

    result.needs_rewrite = (
        (encrypt_enabled and has_plaintext and is_available())
        or (not encrypt_enabled and has_protected and not result.undecryptable)
    )
    return result
