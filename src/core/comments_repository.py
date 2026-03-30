"""Репозиторий шаблонов комментариев с хранением в JSON."""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional
import logging

from core.atomic_io import atomic_write_json
from core.config_paths import get_config_dir

logger = logging.getLogger(__name__)


@dataclass
class Comment:
    """Модель шаблона комментария."""
    name: str
    template: str
    hotkey: str = ""
    source: str = ""
    hidden: bool = False
    
    def to_dict(self) -> dict:
        """Преобразовать в словарь."""
        return asdict(self)
    
    @staticmethod
    def from_dict(data: dict) -> 'Comment':
        """Создать объект из словаря."""
        hidden_raw = data.get("hidden", False)
        if isinstance(hidden_raw, bool):
            hidden = hidden_raw
        elif isinstance(hidden_raw, str):
            hidden = hidden_raw.strip().lower() in ("1", "true", "yes", "on", "да", "истина")
        else:
            hidden = bool(hidden_raw)
        return Comment(
            name=data.get("name", ""),
            template=data.get("template", ""),
            hotkey=data.get("hotkey", ""),
            source=data.get("source", ""),
            hidden=hidden,
        )


class CommentsRepository:
    """Репозиторий для управления шаблонами комментариев."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Инициализировать репозиторий.
        
        Args:
            config_dir: Путь к каталогу конфигурации. Если `None`, берется путь по умолчанию.
        """
        if config_dir is None:
            config_dir = get_config_dir()
        
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        self.comments_file = self.config_dir / "comments.json"
        self._comments: List[Comment] = []
    
    def load(self) -> None:
        """Загрузить комментарии из файла `comments.json`."""
        if not self.comments_file.exists():
            logger.info("Файл комментариев не найден, используется набор по умолчанию")
            self._create_fallback_comment()
            return

        try:
            with open(self.comments_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._comments = [Comment.from_dict(item) for item in data]
            logger.info("Загружено комментариев: %d", len(self._comments))
        except Exception as e:
            logger.error("Не удалось загрузить комментарии: %s", e)
            self._comments = []
    
    def save(self) -> None:
        """Сохранить комментарии в файл `comments.json`."""
        try:
            data = [comment.to_dict() for comment in self._comments]
            atomic_write_json(self.comments_file, data, indent=2, ensure_ascii=False)
            logger.info("Сохранено комментариев: %d", len(self._comments))
        except Exception as e:
            logger.error("Не удалось сохранить комментарии: %s", e)
    
    def get_all(self) -> List[Comment]:
        """Получить все комментарии."""
        return self._comments.copy()

    def set_all(self, comments: List[Comment]) -> None:
        """Заменить весь список комментариев новым порядком."""
        self._comments = comments.copy()
    
    def get_by_index(self, index: int) -> Optional[Comment]:
        """Получить комментарий по индексу."""
        if 0 <= index < len(self._comments):
            return self._comments[index]
        return None
    
    def add(self, comment: Comment) -> None:
        """Добавить новый комментарий."""
        self._comments.append(comment)
    
    def update(self, index: int, comment: Comment) -> None:
        """Обновить существующий комментарий по индексу."""
        if not (0 <= index < len(self._comments)):
            raise ValueError(f"Comment index '{index}' is out of range")
        self._comments[index] = comment
    
    def delete(self, index: int) -> None:
        """Удалить комментарий по индексу."""
        if not (0 <= index < len(self._comments)):
            raise ValueError(f"Comment index '{index}' is out of range")
        self._comments.pop(index)
    
    def search(self, query: str) -> List[Comment]:
        """Поиск комментариев по названию или шаблону."""
        query_lower = query.lower()
        return [
            c for c in self._comments
            if query_lower in c.name.lower() or query_lower in c.template.lower()
        ]
    
    def _create_fallback_comment(self) -> None:
        """Создать набор шаблонов по умолчанию, если файл отсутствует."""
        self._comments = [
            Comment(
                name="Код добавлен",
                template="{@line_limit max=110 mode=wrap suffix=\"// \"}// + {author}. {datetime}. [{issue_key}] {issue_summary}{@end}\n{text}\n// - {author}. {datetime}.",
            ),
            Comment(
                name="Код удален",
                template="{@line_limit max=110 mode=wrap suffix=\"// \"}// + {author}. {datetime}. [{issue_key}] {issue_summary}{@end}\n{text|prefix=\"// \"}\n// - {author}. {datetime}.",
            ),
            Comment(
                name="Функция добавлена",
                template="{@line_limit max=110 mode=wrap suffix=\"// \"}// {author}. {datetime}. [{issue_key}] {issue_summary}{@end}",
            ),
            Comment(
                name="Коммит",
                template="[{issue_key}] {issue_summary}",
            ),
            Comment(
                name="Метаданные",
                template="// {author}. {date}. {issue_key}.",
            ),
        ]
