"""Кэш последних ответов LLM в памяти."""

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Optional

from core.llm_profiles_repository import LlmProfile


class LlmAnswerCache:
    """Последние ответы модели по промпту и параметрам профиля.

    Нужен, чтобы повторный вызов того же шаблона на том же тексте (например,
    когда вставка не удалась) не ждал модель заново. Хранится только в памяти:
    промпт и ответ — код пользователя, на диск они не пишутся. Вместо промпта
    в ключе лежит его хэш. Доступ потокобезопасен: запись идет из фонового
    потока рендера, в том числе из потока уже отмененного сценария.
    """

    def __init__(self, max_entries: int = 8):
        self._max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _make_key(profile: LlmProfile, prompt: str) -> str:
        # Название профиля и ключ API на ответ не влияют; адрес, модель и
        # температура — влияют, поэтому правка профиля сбрасывает попадания.
        payload = json.dumps(
            [profile.base_url.strip(), profile.model.strip(), profile.temperature, prompt],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, profile: LlmProfile, prompt: str) -> Optional[str]:
        key = self._make_key(profile, prompt)
        with self._lock:
            answer = self._entries.get(key)
            if answer is not None:
                self._entries.move_to_end(key)
            return answer

    def put(self, profile: LlmProfile, prompt: str, answer: str) -> None:
        key = self._make_key(profile, prompt)
        with self._lock:
            self._entries[key] = answer
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> int:
        """Очистить кэш и вернуть количество удаленных ответов."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count
