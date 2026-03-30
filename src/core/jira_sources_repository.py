"""Репозиторий конфигурации источников Jira."""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from core.atomic_io import atomic_write_json
from core.config_paths import get_config_dir

logger = logging.getLogger(__name__)


@dataclass
class JiraSource:
    """Конфигурация источника Jira."""

    name: str
    url: str
    token: str
    ttl_minutes: int = 5
    timeout_seconds: int = 2
    auto_refresh: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "JiraSource":
        def _safe_int(value: object, fallback: int) -> int:
            try:
                return int(value)
            except Exception:
                return fallback

        def _safe_bool(value: object, fallback: bool) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
            return fallback

        return JiraSource(
            name=str(data.get("name", "")).strip(),
            url=str(data.get("url", "")).strip(),
            token=str(data.get("token", "")).strip(),
            ttl_minutes=_safe_int(data.get("ttl_minutes", 5), 5),
            timeout_seconds=_safe_int(data.get("timeout_seconds", 2), 2),
            auto_refresh=_safe_bool(data.get("auto_refresh", False), False),
        )


class JiraSourcesRepository:
    """Репозиторий источников Jira."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = get_config_dir()

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.sources_file = self.config_dir / "jira_sources.json"
        self._sources: List[JiraSource] = []

    def load(self) -> None:
        """Загрузить источники из файла конфигурации."""
        if not self.sources_file.exists():
            self._sources = []
            logger.info("Файл источников Jira не найден, используется пустой список")
            return

        try:
            with open(self.sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._sources = [JiraSource.from_dict(item) for item in data]
            logger.info("Загружено источников Jira: %d", len(self._sources))
        except Exception as exc:
            logger.error("Не удалось загрузить источники Jira: %s", exc)
            self._sources = []

    def save(self) -> None:
        """Сохранить источники в файл конфигурации."""
        try:
            data = [source.to_dict() for source in self._sources]
            atomic_write_json(self.sources_file, data, indent=2, ensure_ascii=False)
            logger.info("Сохранено источников Jira: %d", len(self._sources))
        except Exception as exc:
            logger.error("Не удалось сохранить источники Jira: %s", exc)
            raise

    def get_all(self) -> List[JiraSource]:
        return self._sources.copy()

    def set_all(self, sources: List[JiraSource]) -> None:
        self._sources = sources.copy()

    def get_by_name(self, name: str) -> Optional[JiraSource]:
        target = (name or "").strip()
        for source in self._sources:
            if source.name == target:
                return source
        return None
