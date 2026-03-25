"""Вспомогательные функции для определения каталога конфигурации."""

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_windows_store_roaming_dir() -> Path | None:
    """Попробовать найти каталог Roaming для Python из Microsoft Store."""
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        return None

    packages_root = Path(local_appdata) / "Packages"
    if not packages_root.exists():
        return None

    candidates = sorted(packages_root.glob("PythonSoftwareFoundation.Python.*"))
    for package_dir in candidates:
        roaming = package_dir / "LocalCache" / "Roaming"
        if roaming.exists():
            return roaming
    return None


def _migrate_from_windows_store_dir(target_dir: Path, app_name: str) -> None:
    """Перенести legacy-конфиг из LocalCache\\Roaming в стабильный APPDATA-каталог."""
    source_root = _get_windows_store_roaming_dir()
    if not source_root:
        return

    source_dir = source_root / app_name
    if not source_dir.exists() or source_dir.resolve() == target_dir.resolve():
        return

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("Не удалось создать каталог конфигурации %s: %s", target_dir, exc)
        return

    migrated_any = False
    for item in source_dir.iterdir():
        destination = target_dir / item.name
        if destination.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, destination)
            else:
                shutil.copy2(item, destination)
            migrated_any = True
        except Exception as exc:
            logger.warning("Не удалось мигрировать %s -> %s: %s", item, destination, exc)

    if migrated_any:
        logger.info("Legacy-конфигурация перенесена из %s в %s", source_dir, target_dir)


def get_config_dir() -> Path:
    """Вернуть каталог конфигурации приложения для Windows."""
    app_name = "1CCommentHotkeys"
    appdata = os.getenv("APPDATA")
    base_dir = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    target_dir = base_dir / app_name
    _migrate_from_windows_store_dir(target_dir=target_dir, app_name=app_name)
    return target_dir
