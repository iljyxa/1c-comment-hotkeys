"""Кэш задач Jira по источникам."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List

from core.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)


class JiraIssuesCache:
    """Кэш задач Jira с ключом по имени источника."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "jira_issues_cache.json"
        self._lock = Lock()
        self._data: Dict[str, Dict[str, Any]] = {}

    def load(self) -> None:
        if not self.cache_file.exists():
            self._data = {}
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._data = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            logger.error("Не удалось загрузить кэш задач Jira: %s", exc)
            self._data = {}

    def save(self) -> None:
        with self._lock:
            try:
                atomic_write_json(self.cache_file, self._data, indent=2, ensure_ascii=False)
            except Exception as exc:
                logger.error("Не удалось сохранить кэш задач Jira: %s", exc)

    def update(self, source_name: str, issues: List[Dict[str, str]]) -> None:
        key = (source_name or "").strip()
        with self._lock:
            self._data[key] = {
                "updated_at": datetime.now().isoformat(),
                "issues": issues,
            }
        self.save()

    def get_any(self, source_name: str) -> List[Dict[str, str]]:
        key = (source_name or "").strip()
        entry = self._data.get(key) or {}
        issues = entry.get("issues") or []
        return issues if isinstance(issues, list) else []

    def get_fresh(self, source_name: str, ttl_seconds: int) -> List[Dict[str, str]]:
        key = (source_name or "").strip()
        entry = self._data.get(key)
        if not entry:
            return []

        updated_at = entry.get("updated_at")
        if not updated_at:
            return []
        try:
            updated_dt = datetime.fromisoformat(updated_at)
        except Exception:
            return []

        if datetime.now() - updated_dt > timedelta(seconds=ttl_seconds):
            return []
        return self.get_any(source_name)
