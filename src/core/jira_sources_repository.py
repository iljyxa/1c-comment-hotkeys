"""Репозиторий конфигурации источников Jira."""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from core.config_paths import get_config_dir

logger = logging.getLogger(__name__)


@dataclass
class JiraSource:
    """Конфигурация источника Jira."""

    name: str
    url: str
    token: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "JiraSource":
        return JiraSource(
            name=str(data.get("name", "")).strip(),
            url=str(data.get("url", "")).strip(),
            token=str(data.get("token", "")).strip(),
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
            with open(self.sources_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
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
