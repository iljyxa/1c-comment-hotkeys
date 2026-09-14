"""Утилиты безопасной (атомарной) записи файлов."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def _fsync_directory(path: Path) -> None:
    """Синхронизировать каталог на диск (best-effort)."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except Exception:
        return
    try:
        os.fsync(fd)
    except Exception:
        pass
    finally:
        os.close(fd)


def atomic_write_json(
    target_file: Path,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """Записать JSON атомарно через временный файл и `os.replace`."""
    target_path = Path(target_file)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f"{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, indent=indent, ensure_ascii=ensure_ascii)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp_path = Path(tmp.name)

        os.replace(str(temp_path), str(target_path))
        _fsync_directory(target_path.parent)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def backup_corrupted_file(target_file: Path) -> Path | None:
    """Переименовать поврежденный файл в `<имя>.broken-<метка времени>`.

    Используется при ошибке чтения конфигурации, чтобы последующее сохранение
    не затерло данные, которые еще можно восстановить вручную.
    Возвращает путь резервной копии или `None`, если переименовать не удалось.
    """
    source = Path(target_file)
    if not source.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(f"{source.name}.broken-{stamp}")
    try:
        os.replace(str(source), str(backup))
    except Exception:
        return None
    return backup
