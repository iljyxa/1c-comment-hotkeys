"""Сервис получения задач Jira по URL фильтра источника."""

import json
import logging
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

from core.jira_issues_cache import JiraIssuesCache
from core.jira_sources_repository import JiraSource

logger = logging.getLogger(__name__)


class JiraIssuesError(RuntimeError):
    """Ошибка получения задач Jira."""


class JiraIssuesService:
    """Получение задач Jira с кэшем и стратегией fallback по таймаутам."""

    def __init__(self, cache: JiraIssuesCache):
        self.cache = cache
        self.cache_ttl_seconds = 180
        self.max_issues = 20
        self._refresh_in_progress: set[str] = set()
        self._refresh_lock = threading.Lock()

    def get_issues_for_source(self, source: JiraSource) -> Tuple[List[Dict[str, str]], str]:
        """Получить задачи по источнику с учетом стратегии таймаутов.

        Returns:
            Кортеж: `(список_задач, уведомление_для_UI)`.
        """
        fresh = self.cache.get_fresh(source.name, self.cache_ttl_seconds)
        if fresh:
            return fresh, ""

        try:
            issues = self._fetch(source, timeout_seconds=2)
            self.cache.update(source.name, issues)
            return issues, ""
        except TimeoutError:
            stale = self.cache.get_any(source.name)
            if stale:
                self._start_background_refresh(source)
                return stale, (
                    "Получение задач заняло слишком много времени. "
                    "Показаны кэшированные данные, обновление выполняется в фоне."
                )

            issues = self._fetch(source, timeout_seconds=5)
            self.cache.update(source.name, issues)
            return issues, ""
        except Exception as exc:
            raise JiraIssuesError(str(exc)) from exc

    def _start_background_refresh(self, source: JiraSource) -> None:
        source_name = source.name
        with self._refresh_lock:
            if source_name in self._refresh_in_progress:
                return
            self._refresh_in_progress.add(source_name)

        def worker() -> None:
            try:
                issues = self._fetch(source, timeout_seconds=30)
                self.cache.update(source.name, issues)
                logger.info("Фоновое обновление кэша Jira завершено для источника: %s", source.name)
            except Exception as exc:
                logger.warning(
                    "Фоновое обновление кэша Jira завершилось ошибкой для источника '%s': %s",
                    source.name,
                    exc,
                )
            finally:
                with self._refresh_lock:
                    self._refresh_in_progress.discard(source_name)

        threading.Thread(target=worker, daemon=True).start()

    def _fetch(self, source: JiraSource, timeout_seconds: int) -> List[Dict[str, str]]:
        jql = self._extract_jql(source.url)
        base = self._extract_base_url(source.url)
        query = urllib.parse.urlencode(
            {
                "jql": jql,
                "maxResults": self.max_issues,
                "fields": "summary",
            }
        )
        url = f"{base}/rest/api/2/search?{query}"
        logger.info(
            "Запрос Jira: источник='%s', таймаут=%ss, url='%s', maxResults=%d",
            source.name,
            timeout_seconds,
            url,
            self.max_issues,
        )

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {source.token}",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
                status_code = getattr(response, "status", None)
                logger.info(
                    "Получен ответ Jira: источник='%s', статус=%s, байт=%d",
                    source.name,
                    status_code,
                    len(payload),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            logger.error(
                "HTTP-ошибка Jira: источник='%s', статус=%s, url='%s', ответ='%s'",
                source.name,
                exc.code,
                url,
                body[:4000],
            )
            raise JiraIssuesError(f"Jira HTTP {exc.code}: {body[:200]}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                logger.error(
                    "Таймаут запроса Jira: источник='%s', таймаут=%ss, url='%s'",
                    source.name,
                    timeout_seconds,
                    url,
                )
                raise TimeoutError("Таймаут запроса Jira") from exc
            logger.error(
                "Ошибка URL при запросе Jira: источник='%s', таймаут=%ss, url='%s', причина='%s'",
                source.name,
                timeout_seconds,
                url,
                exc.reason,
            )
            raise JiraIssuesError(f"Запрос Jira завершился ошибкой: {exc.reason}") from exc
        except socket.timeout as exc:
            logger.error(
                "Сокет-таймаут Jira: источник='%s', таймаут=%ss, url='%s'",
                source.name,
                timeout_seconds,
                url,
            )
            raise TimeoutError("Таймаут запроса Jira") from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            logger.error(
                "Некорректный JSON Jira: источник='%s', url='%s', payload='%s'",
                source.name,
                url,
                payload.decode("utf-8", errors="ignore")[:4000],
            )
            raise JiraIssuesError("Некорректный JSON в ответе Jira") from exc

        issues = []
        for item in (data.get("issues") or [])[: self.max_issues]:
            key = str(item.get("key", "")).strip()
            summary = str((item.get("fields") or {}).get("summary", "")).strip()
            if key:
                issues.append({"key": key, "summary": summary})
        logger.info(
            "Распарсены задачи Jira: источник='%s', количество=%d",
            source.name,
            len(issues),
        )
        return issues

    @staticmethod
    def _extract_base_url(source_url: str) -> str:
        parsed = urllib.parse.urlparse(source_url)
        if not parsed.scheme or not parsed.netloc:
            raise JiraIssuesError("Некорректная ссылка источника Jira")
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _extract_jql(source_url: str) -> str:
        parsed = urllib.parse.urlparse(source_url)
        qs = urllib.parse.parse_qs(parsed.query)
        jql_values = qs.get("jql")
        if not jql_values:
            raise JiraIssuesError("В ссылке источника отсутствует параметр jql")
        jql = jql_values[0].strip()
        if not jql:
            raise JiraIssuesError("Параметр jql пустой")
        return jql
