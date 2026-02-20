"""Вспомогательные функции для определения каталога конфигурации."""

import os
from pathlib import Path


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


def get_config_dir() -> Path:
    """Вернуть каталог конфигурации приложения для Windows."""
    app_name = "1CCommentHotkeys"

    store_roaming = _get_windows_store_roaming_dir()
    if store_roaming:
        store_target = store_roaming / app_name
        if store_target.exists():
            return store_target

    appdata = os.getenv("APPDATA")
    if appdata:
        env_target = Path(appdata) / app_name
        if env_target.exists():
            return env_target

    if store_roaming:
        return store_roaming / app_name

    base_dir = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base_dir / app_name
