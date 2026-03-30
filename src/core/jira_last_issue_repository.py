"""Хранилище последней выбранной Jira-задачи по каждому источнику."""

import json
import logging
from pathlib import Path

from core.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)


class JiraLastIssueRepository:
    """Репозиторий последнего выбранного issue key по имени источника."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.storage_file = self.config_dir / "jira_last_issue.json"
        self._by_source: dict[str, str] = {}

    def load(self) -> None:
        """Загрузить состояние из файла."""
        if not self.storage_file.exists():
            self._by_source = {}
            return
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                self._by_source = {}
                return
            self._by_source = {
                str(source).strip(): str(issue_key).strip()
                for source, issue_key in raw.items()
                if str(source).strip() and str(issue_key).strip()
            }
        except Exception as exc:
            logger.warning("Не удалось загрузить last issue state: %s", exc)
            self._by_source = {}

    def get_last_issue_key(self, source_name: str) -> str:
        """Вернуть последний выбранный issue key для источника."""
        return self._by_source.get((source_name or "").strip(), "")

    def set_last_issue_key(self, source_name: str, issue_key: str) -> None:
        """Сохранить последний выбранный issue key для источника."""
        source = (source_name or "").strip()
        key = (issue_key or "").strip()
        if not source or not key:
            return

        if self._by_source.get(source) == key:
            return

        self._by_source[source] = key
        self.save()

    def save(self) -> None:
        """Сохранить состояние в файл."""
        try:
            atomic_write_json(self.storage_file, self._by_source, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Не удалось сохранить last issue state: %s", exc)
