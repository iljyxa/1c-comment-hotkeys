"""Клиент OpenAI-совместимого Chat Completions API."""

import json
import logging
import socket
import time
import urllib.error
import urllib.request

from core.llm_profiles_repository import LlmProfile

logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """Ошибка запроса к LLM (сеть, HTTP-статус, некорректный ответ)."""


class LlmClient:
    """Отправка промпта в `<base_url>/chat/completions` и извлечение текста ответа.

    Стриминг не используется: результат нужен целиком, чтобы вставить его в
    шаблон. Ключ API и текст промпта (это код пользователя) в логи не пишутся.
    """

    def complete(self, profile: LlmProfile, prompt: str) -> str:
        """Выполнить запрос и вернуть текст ответа модели.

        Raises:
            TimeoutError: Ответ не получен за `profile.timeout_seconds`.
            LlmError: Любая другая ошибка запроса или разбора ответа.
        """
        url = self._build_url(profile.base_url)
        timeout_seconds = max(1, int(profile.timeout_seconds or 60))
        body: dict = {
            "model": profile.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if profile.temperature is not None:
            body["temperature"] = profile.temperature

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if profile.api_key:
            headers["Authorization"] = f"Bearer {profile.api_key}"

        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        logger.info(
            "Запрос LLM: профиль='%s', модель='%s', url='%s', таймаут=%ss, длина промпта=%d",
            profile.name,
            profile.model,
            url,
            timeout_seconds,
            len(prompt),
        )

        started_at = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
                status_code = getattr(response, "status", None)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            logger.error(
                "HTTP-ошибка LLM: профиль='%s', статус=%s, ответ='%s'",
                profile.name,
                exc.code,
                error_body[:4000],
            )
            raise LlmError(f"LLM HTTP {exc.code}: {self._extract_error_message(error_body)}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                logger.error("Таймаут запроса LLM: профиль='%s', таймаут=%ss", profile.name, timeout_seconds)
                raise TimeoutError(f"Таймаут запроса LLM ({timeout_seconds} с)") from exc
            logger.error("Ошибка URL при запросе LLM: профиль='%s', причина='%s'", profile.name, exc.reason)
            raise LlmError(f"Запрос LLM завершился ошибкой: {exc.reason}") from exc
        except (socket.timeout, TimeoutError) as exc:
            logger.error("Сокет-таймаут LLM: профиль='%s', таймаут=%ss", profile.name, timeout_seconds)
            raise TimeoutError(f"Таймаут запроса LLM ({timeout_seconds} с)") from exc

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            logger.error(
                "Некорректный JSON LLM: профиль='%s', payload='%s'",
                profile.name,
                payload.decode("utf-8", errors="ignore")[:4000],
            )
            raise LlmError("Некорректный JSON в ответе LLM") from exc

        content = self._extract_content(data)
        logger.info(
            "Получен ответ LLM: профиль='%s', статус=%s, длина ответа=%d, время=%dms",
            profile.name,
            status_code,
            len(content),
            elapsed_ms,
        )
        return content

    @staticmethod
    def _build_url(base_url: str) -> str:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise LlmError("Не указан адрес API в профиле LLM")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @staticmethod
    def _extract_error_message(error_body: str) -> str:
        """Достать человекочитаемое сообщение из `{"error": {"message": ...}}`."""
        try:
            data = json.loads(error_body)
            error = data.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])[:200]
            if isinstance(error, str) and error:
                return error[:200]
        except Exception:
            pass
        return error_body[:200]

    @staticmethod
    def _extract_content(data: object) -> str:
        if not isinstance(data, dict):
            raise LlmError("Неожиданный формат ответа LLM")
        # Некоторые провайдеры возвращают ошибку с кодом 200.
        error = data.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            raise LlmError(f"Ошибка LLM: {str(message)[:200]}")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlmError("Ответ LLM не содержит вариантов (choices)")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        # Content может быть списком частей (`[{"type": "text", "text": ...}]`).
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type", "text") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            raise LlmError("Пустой ответ LLM")
        return content
