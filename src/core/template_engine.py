"""Движок подстановки макросов для шаблонов комментариев."""

import re
from datetime import datetime
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class TemplateEngine:
    """Движок обработки макросов в шаблонах."""
    
    def _get_replacements(self, text: str, context: dict) -> Dict[str, str]:
        """Сформировать словарь замен для макросов.
        
        Args:
            text: Выделенный текст.
            context: Дополнительный контекст.
            
        Returns:
            Словарь вида ``макрос -> значение``.
        """
        now = datetime.now()
        
        replacements = {
            '{text}': text,
            '{date}': now.strftime('%d.%m.%Y'),
            '{time}': now.strftime('%H:%M'),
            '{datetime}': now.strftime('%d.%m.%Y %H:%M'),
            '{issue_key}': context.get('issue_key', ''),
            '{issue_summary}': self._sanitize_jira_text(context.get('issue_summary', '')),
        }
        
        return replacements

    def render(self, template: str, text: str, context: Optional[Dict] = None) -> str:
        """Подставить значения макросов в шаблон."""
        if context is None:
            context = {}

        replacements = self._get_replacements(text, context)

        result = template
        for macro, value in replacements.items():
            result = result.replace(macro, value)

        return result

    @staticmethod
    def _sanitize_jira_text(value: str) -> str:
        """Нормализовать текст из Jira после копирования из rich text."""
        text = str(value or "")

        quote_map = {
            "«": '"',
            "»": '"',
            "“": '"',
            "”": '"',
            "„": '"',
            "‟": '"',
            "’": "'",
            "‘": "'",
            "‚": "'",
            "‛": "'",
        }
        for src, dst in quote_map.items():
            text = text.replace(src, dst)

        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

        # Заменяем нестандартные пробелы на обычный пробел.
        text = re.sub(r"[\u00A0\u1680\u2000-\u200B\u202F\u205F\u3000\uFEFF]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    
    def get_available_macros(self) -> Dict[str, str]:
        """Вернуть список доступных макросов с описаниями.
        
        Returns:
            Словарь вида ``макрос -> описание``.
        """
        return {
            '{text}': 'Выделенный текст',
            '{date}': 'Текущая дата (dd.MM.yyyy)',
            '{time}': 'Текущее время (HH:mm)',
            '{datetime}': 'Дата и время (dd.MM.yyyy HH:mm)',
            '{issue_key}': 'Ключ задачи Jira',
            '{issue_summary}': 'Краткое описание задачи Jira',
        }
