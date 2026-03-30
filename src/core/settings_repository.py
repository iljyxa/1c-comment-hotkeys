"""Репозиторий настроек приложения с хранением в JSON."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.atomic_io import atomic_write_json
from core.config_paths import get_config_dir

logger = logging.getLogger(__name__)


@dataclass
class AppSettings:
    """Настройки приложения."""

    hotkey_combination: str
    start_minimized: bool = True
    log_to_file: bool = False
    author: str = "AUTHOR"


class SettingsRepository:
    """Репозиторий загрузки/сохранения настроек приложения."""

    def __init__(self, default_hotkey: str, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir is not None else get_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self.default_hotkey = default_hotkey
        self.settings = AppSettings(
            hotkey_combination=default_hotkey,
            start_minimized=True,
            log_to_file=False,
            author="AUTHOR",
        )

    def load(self) -> None:
        """Загрузить настройки из `config.json`."""
        if not self.config_file.exists():
            logger.info("Файл настроек не найден, используются значения по умолчанию в памяти")
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            extracted_hotkey = self._extract_hotkey(data)
            hotkey = self.default_hotkey if extracted_hotkey is None else extracted_hotkey
            start_minimized = self._extract_start_minimized(data)
            log_to_file = self._extract_log_to_file(data)
            author = self._extract_author(data)
            self.settings = AppSettings(
                hotkey_combination=hotkey,
                start_minimized=start_minimized,
                log_to_file=log_to_file,
                author=author,
            )
            logger.info("Настройки загружены")
        except Exception as exc:
            logger.error("Не удалось загрузить настройки: %s", exc)
            self.settings = AppSettings(
                hotkey_combination=self.default_hotkey,
                start_minimized=True,
                log_to_file=False,
                author="AUTHOR",
            )

    def save(self) -> None:
        """Сохранить настройки в `config.json`."""
        data = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                logger.warning("Не удалось прочитать config перед сохранением, будет создан новый объект")

        data.update({
            "hotkeys": [
                {
                    "combination": self.settings.hotkey_combination,
                    "action": "show_comment_dialog",
                }
            ],
            "ui": {
                "start_minimized": self.settings.start_minimized,
                "log_to_file": self.settings.log_to_file,
                "author": self.settings.author,
            },
        })
        try:
            atomic_write_json(self.config_file, data, indent=2, ensure_ascii=False)
            logger.info("Настройки сохранены")
        except Exception as exc:
            logger.error("Не удалось сохранить настройки: %s", exc)
            raise

    def get_hotkey(self) -> str:
        """Вернуть текущую горячую клавишу."""
        return self.settings.hotkey_combination

    def set_hotkey(self, hotkey: str) -> None:
        """Обновить горячую клавишу."""
        self.settings.hotkey_combination = hotkey.strip()

    def get_start_minimized(self) -> bool:
        """Вернуть флаг запуска в свернутом режиме."""
        return self.settings.start_minimized

    def set_start_minimized(self, value: bool) -> None:
        """Обновить флаг запуска в свернутом режиме."""
        self.settings.start_minimized = bool(value)

    def get_config_dir(self) -> Path:
        """Вернуть путь к каталогу конфигурации."""
        return self.config_dir

    def get_log_to_file(self) -> bool:
        """Вернуть флаг записи логов в файл."""
        return self.settings.log_to_file

    def set_log_to_file(self, value: bool) -> None:
        """Обновить флаг записи логов в файл."""
        self.settings.log_to_file = bool(value)

    def get_author(self) -> str:
        """Вернуть автора для макроса `{author}`."""
        return self.settings.author

    def set_author(self, value: str) -> None:
        """Обновить автора для макроса `{author}`."""
        self.settings.author = str(value or "").strip()

    @staticmethod
    def _extract_hotkey(config: dict) -> Optional[str]:
        """Извлечь горячую клавишу из структуры конфигурации."""
        hotkeys = config.get("hotkeys")
        if isinstance(hotkeys, list):
            for item in hotkeys:
                if item.get("action") == "show_comment_dialog":
                    return item.get("combination")
            if hotkeys and isinstance(hotkeys[0], dict):
                return hotkeys[0].get("combination")
        return None

    @staticmethod
    def _extract_start_minimized(config: dict) -> bool:
        """Извлечь настройку запуска в трее из структуры конфигурации."""
        ui = config.get("ui")
        if isinstance(ui, dict) and "start_minimized" in ui:
            return bool(ui.get("start_minimized"))
        return True

    @staticmethod
    def _extract_log_to_file(config: dict) -> bool:
        """Извлечь настройку логирования в файл из структуры конфигурации."""
        ui = config.get("ui")
        if isinstance(ui, dict) and "log_to_file" in ui:
            return bool(ui.get("log_to_file"))
        return False

    @staticmethod
    def _extract_author(config: dict) -> str:
        """Извлечь автора из структуры конфигурации."""
        ui = config.get("ui")
        if isinstance(ui, dict):
            value = ui.get("author")
            if value is not None:
                return str(value).strip()
        root_value = config.get("author")
        if root_value is not None:
            return str(root_value).strip()
        return "AUTHOR"
