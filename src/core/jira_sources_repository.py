"""Репозиторий конфигурации источников Jira."""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from core import secret_store
from core.atomic_io import atomic_write_json, backup_corrupted_file
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

    def __init__(self, config_dir: Optional[Path] = None, encrypt_tokens: bool = True):
        if config_dir is None:
            config_dir = get_config_dir()

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.sources_file = self.config_dir / "jira_sources.json"
        self._sources: List[JiraSource] = []
        self.load_warning: Optional[str] = None
        self._encrypt_tokens = bool(encrypt_tokens)

    def get_encrypt_tokens(self) -> bool:
        return self._encrypt_tokens

    def set_encrypt_tokens(self, value: bool) -> None:
        """Переключить шифрование токенов; вступает в силу при следующем `save()`."""
        self._encrypt_tokens = bool(value)

    def load(self) -> None:
        """Загрузить источники из файла конфигурации.

        При включенном шифровании токены в файле хранятся как `dpapi:<base64>`,
        при выключенном — открытым текстом. Если состояние файла не совпадает
        с настройкой (например, настройку только что переключили или файл
        пришел из старой версии), файл сразу пересохраняется в нужном виде.
        """
        self.load_warning = None
        if not self.sources_file.exists():
            self._sources = []
            logger.info("Файл источников Jira не найден, используется пустой список")
            return

        try:
            with open(self.sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            sources = [JiraSource.from_dict(item) for item in data]
        except Exception as exc:
            # Поврежденный файл откладываем в сторону, чтобы следующее сохранение
            # из окна "Источники" не затерло его пустым списком.
            backup = backup_corrupted_file(self.sources_file)
            logger.error("Не удалось загрузить источники Jira: %s (резервная копия: %s)", exc, backup)
            self.load_warning = (
                f"Файл источников Jira поврежден и не был загружен: {exc}\n"
                f"Резервная копия: {backup or 'создать не удалось'}"
            )
            self._sources = []
            return

        has_plaintext_token = False
        has_protected_token = False
        undecryptable: List[str] = []
        for source in sources:
            if not secret_store.is_protected(source.token):
                if source.token:
                    has_plaintext_token = True
                continue
            has_protected_token = True
            try:
                source.token = secret_store.unprotect(source.token)
            except Exception as exc:
                # Например, файл скопирован от другого пользователя Windows.
                logger.error("Не удалось расшифровать токен источника '%s': %s", source.name, exc)
                source.token = ""
                undecryptable.append(source.name)
        self._sources = sources
        logger.info("Загружено источников Jira: %d", len(self._sources))

        if undecryptable:
            self.load_warning = (
                "Не удалось расшифровать токены источников Jira: "
                + ", ".join(undecryptable)
                + ".\nТокены зашифрованы для другой учетной записи Windows. "
                "Введите их заново в окне «Источники»."
            )

        needs_rewrite = (
            (self._encrypt_tokens and has_plaintext_token and secret_store.is_available())
            or (not self._encrypt_tokens and has_protected_token and not undecryptable)
        )
        if needs_rewrite:
            logger.info(
                "Состояние токенов Jira в файле не совпадает с настройкой шифрования (%s), файл пересохраняется",
                "включено" if self._encrypt_tokens else "выключено",
            )
            try:
                self.save()
            except Exception:
                logger.warning("Не удалось пересохранить токены Jira при загрузке")

    def save(self) -> None:
        """Сохранить источники в файл конфигурации.

        Токены шифруются через DPAPI, если шифрование включено в настройках.
        """
        try:
            data = []
            for source in self._sources:
                item = source.to_dict()
                if self._encrypt_tokens:
                    item["token"] = secret_store.protect(source.token)
                data.append(item)
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
