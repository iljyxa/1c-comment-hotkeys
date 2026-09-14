"""Репозиторий профилей LLM (OpenAI-совместимые API)."""

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
class LlmProfile:
    """Настройки подключения к OpenAI-совместимому API."""

    name: str
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: int = 60
    # `None` — параметр не передается, действует значение по умолчанию провайдера
    # (часть моделей отвергает любое значение, кроме умолчания).
    temperature: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "LlmProfile":
        def _safe_int(value: object, fallback: int) -> int:
            try:
                return int(value)
            except Exception:
                return fallback

        def _safe_optional_float(value: object) -> Optional[float]:
            if value is None or (isinstance(value, str) and not value.strip()):
                return None
            try:
                return float(value)
            except Exception:
                return None

        return LlmProfile(
            name=str(data.get("name", "")).strip(),
            base_url=str(data.get("base_url", "")).strip(),
            model=str(data.get("model", "")).strip(),
            api_key=str(data.get("api_key", "")).strip(),
            timeout_seconds=_safe_int(data.get("timeout_seconds", 60), 60),
            temperature=_safe_optional_float(data.get("temperature")),
        )


class LlmProfilesRepository:
    """Репозиторий профилей LLM с хранением в `llm_profiles.json`.

    Ключи API защищаются так же, как токены Jira: при включенной настройке
    `security.encrypt_tokens` на диске лежит `dpapi:<base64>`, в памяти —
    открытый текст (нужен для заголовка `Authorization`).
    """

    def __init__(self, config_dir: Optional[Path] = None, encrypt_tokens: bool = True):
        if config_dir is None:
            config_dir = get_config_dir()

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_file = self.config_dir / "llm_profiles.json"
        self._profiles: List[LlmProfile] = []
        self.load_warning: Optional[str] = None
        self._encrypt_tokens = bool(encrypt_tokens)

    def get_encrypt_tokens(self) -> bool:
        return self._encrypt_tokens

    def set_encrypt_tokens(self, value: bool) -> None:
        """Переключить шифрование ключей; вступает в силу при следующем `save()`."""
        self._encrypt_tokens = bool(value)

    def load(self) -> None:
        """Загрузить профили из файла конфигурации.

        Как и для источников Jira, файл приводится к текущей настройке
        шифрования: открытые ключи шифруются, зашифрованные раскрываются.
        """
        self.load_warning = None
        if not self.profiles_file.exists():
            self._profiles = []
            logger.info("Файл профилей LLM не найден, используется пустой список")
            return

        try:
            with open(self.profiles_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            profiles = [LlmProfile.from_dict(item) for item in data]
        except Exception as exc:
            # Поврежденный файл откладываем в сторону, чтобы следующее сохранение
            # из окна профилей не затерло его пустым списком.
            backup = backup_corrupted_file(self.profiles_file)
            logger.error("Не удалось загрузить профили LLM: %s (резервная копия: %s)", exc, backup)
            self.load_warning = (
                f"Файл профилей LLM поврежден и не был загружен: {exc}\n"
                f"Резервная копия: {backup or 'создать не удалось'}"
            )
            self._profiles = []
            return

        secrets = secret_store.unprotect_all(
            [profile.api_key for profile in profiles],
            encrypt_enabled=self._encrypt_tokens,
            labels=[profile.name for profile in profiles],
        )
        for profile, api_key in zip(profiles, secrets.values):
            profile.api_key = api_key
        self._profiles = profiles
        logger.info("Загружено профилей LLM: %d", len(self._profiles))

        if secrets.undecryptable:
            self.load_warning = (
                "Не удалось расшифровать ключи API профилей LLM: "
                + ", ".join(profiles[index].name for index in secrets.undecryptable)
                + ".\nКлючи зашифрованы для другой учетной записи Windows. "
                "Введите их заново в окне «Профили LLM»."
            )

        if secrets.needs_rewrite:
            logger.info(
                "Состояние ключей LLM в файле не совпадает с настройкой шифрования (%s), файл пересохраняется",
                "включено" if self._encrypt_tokens else "выключено",
            )
            try:
                self.save()
            except Exception:
                logger.warning("Не удалось пересохранить ключи LLM при загрузке")

    def save(self) -> None:
        """Сохранить профили в файл конфигурации."""
        try:
            data = []
            for profile in self._profiles:
                item = profile.to_dict()
                if self._encrypt_tokens:
                    item["api_key"] = secret_store.protect(profile.api_key)
                data.append(item)
            atomic_write_json(self.profiles_file, data, indent=2, ensure_ascii=False)
            logger.info("Сохранено профилей LLM: %d", len(self._profiles))
        except Exception as exc:
            logger.error("Не удалось сохранить профили LLM: %s", exc)
            raise

    def get_all(self) -> List[LlmProfile]:
        return self._profiles.copy()

    def set_all(self, profiles: List[LlmProfile]) -> None:
        self._profiles = profiles.copy()

    def get_by_name(self, name: str) -> Optional[LlmProfile]:
        target = (name or "").strip()
        for profile in self._profiles:
            if profile.name == target:
                return profile
        return None
