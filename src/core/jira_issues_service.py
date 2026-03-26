"""Сервис получения задач Jira по URL фильтра источника."""

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from core.jira_issues_cache import JiraIssuesCache
from core.jira_sources_repository import JiraSource

logger = logging.getLogger(__name__)


class JiraIssuesError(RuntimeError):
    """Ошибка получения задач Jira."""


class JiraIssuesService:
    """Получение задач Jira с кэшем и стратегией fallback по таймаутам."""

    def __init__(self, cache: JiraIssuesCache):
        self.cache = cache
        self.max_issues = 20
        self._refresh_in_progress: set[str] = set()
        self._refresh_lock = threading.Lock()

    def get_issues_for_source(
        self,
        source: JiraSource,
        on_refresh_success: Optional[Callable[[], None]] = None,
    ) -> Tuple[List[Dict[str, str]], bool]:
        """Получить задачи по источнику с учетом стратегии таймаутов.

        Returns:
            Кортеж: `(список_задач, данные_из_устаревшего_кэша_с_фоновым_обновлением)`.
        """
        started_at = time.perf_counter()
        ttl_seconds = self._resolve_ttl_seconds(source)
        timeout_seconds = self._resolve_timeout_seconds(source)
        fresh = self._normalize_issues(self._get_cached_fresh(source, ttl_seconds))
        if fresh:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "Jira cache-hit: источник='%s', ttl=%s, задач=%d, время=%dms",
                source.name,
                ttl_seconds,
                len(fresh),
                elapsed_ms,
            )
            return fresh, False

        stale = self._normalize_issues(self.cache.get_any(source.name) if ttl_seconds != 0 else [])
        if stale:
            self._start_background_refresh(source, timeout_seconds, on_refresh_success)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "Jira stale-cache fallback: источник='%s', ttl=%s, задач=%d, время=%dms",
                source.name,
                ttl_seconds,
                len(stale),
                elapsed_ms,
            )
            return stale, True

        try:
            issues = self._normalize_issues(self._fetch(source, timeout_seconds=timeout_seconds))
            self._update_cache_if_enabled(source, ttl_seconds, issues)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "Jira live-fetch success: источник='%s', задач=%d, время=%dms",
                source.name,
                len(issues),
                elapsed_ms,
            )
            return issues, False
        except TimeoutError:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.warning(
                "Jira timeout without cache fallback: источник='%s', таймаут=%ss, время=%dms",
                source.name,
                timeout_seconds,
                elapsed_ms,
            )
            raise
        except Exception as exc:
            raise JiraIssuesError(str(exc)) from exc

    def refresh_source(self, source: JiraSource) -> None:
        """Принудительно обновить кэш по источнику."""
        started_at = time.perf_counter()
        ttl_seconds = self._resolve_ttl_seconds(source)
        timeout_seconds = self._resolve_timeout_seconds(source)
        issues = self._normalize_issues(self._fetch(source, timeout_seconds=timeout_seconds))
        self._update_cache_if_enabled(source, ttl_seconds, issues)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "Jira source refresh: источник='%s', задач=%d, время=%dms",
            source.name,
            len(issues),
            elapsed_ms,
        )

    def _start_background_refresh(
        self,
        source: JiraSource,
        timeout_seconds: int,
        on_success: Optional[Callable[[], None]] = None,
    ) -> None:
        source_name = source.name
        with self._refresh_lock:
            if source_name in self._refresh_in_progress:
                return
            self._refresh_in_progress.add(source_name)

        def worker() -> None:
            started_at = time.perf_counter()
            try:
                ttl_seconds = self._resolve_ttl_seconds(source)
                issues = self._normalize_issues(self._fetch(source, timeout_seconds=timeout_seconds))
                self._update_cache_if_enabled(source, ttl_seconds, issues)
                logger.info("Фоновое обновление кэша Jira завершено для источника: %s", source.name)
                if on_success:
                    try:
                        on_success()
                    except Exception as exc:
                        logger.debug("Ошибка callback обновления кэша: %s", exc)
            except Exception as exc:
                logger.warning(
                    "Фоновое обновление кэша Jira завершилось ошибкой для источника '%s': %s",
                    source.name,
                    exc,
                )
            finally:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(
                    "Фоновое обновление Jira завершено: источник='%s', время=%dms",
                    source.name,
                    elapsed_ms,
                )
                with self._refresh_lock:
                    self._refresh_in_progress.discard(source_name)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _resolve_timeout_seconds(source: JiraSource) -> int:
        try:
            value = int(getattr(source, "timeout_seconds", 2))
        except Exception:
            value = 2
        return max(1, value)

    @staticmethod
    def _resolve_ttl_seconds(source: JiraSource) -> int:
        try:
            ttl_minutes = int(getattr(source, "ttl_minutes", 5))
        except Exception:
            ttl_minutes = 5
        if ttl_minutes == -1:
            return -1
        if ttl_minutes <= 0:
            return 0
        return ttl_minutes * 60

    def _get_cached_fresh(self, source: JiraSource, ttl_seconds: int) -> List[Dict[str, str]]:
        if ttl_seconds == 0:
            return []
        if ttl_seconds == -1:
            return self.cache.get_any(source.name)
        return self.cache.get_fresh(source.name, ttl_seconds)

    def _update_cache_if_enabled(
        self,
        source: JiraSource,
        ttl_seconds: int,
        issues: List[Dict[str, str]],
    ) -> None:
        if ttl_seconds == 0:
            return
        self.cache.update(source.name, issues)

    def _fetch(self, source: JiraSource, timeout_seconds: int) -> List[Dict[str, str]]:
        started_at = time.perf_counter()
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
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(
                    "Получен ответ Jira: источник='%s', статус=%s, байт=%d, время=%dms",
                    source.name,
                    status_code,
                    len(payload),
                    elapsed_ms,
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
    def _normalize_issues(issues: List[Dict[str, str]]) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for issue in issues or []:
            key = str(issue.get("key") or issue.get("issue_key") or "").strip()
            summary = str(issue.get("summary", "")).strip()
            if not key:
                continue
            normalized.append({"key": key, "summary": summary})
        return normalized

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
