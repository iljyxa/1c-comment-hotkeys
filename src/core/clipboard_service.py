"""Сервис работы с буфером обмена и вставкой комментариев."""

import time
import logging
import os
import re
import ctypes
from typing import Optional

import pyperclip
from pynput.keyboard import Controller, Key, KeyCode

from .template_engine import TemplateEngine
from .comments_repository import Comment

logger = logging.getLogger(__name__)


class ClipboardService:
    """Сервис операций с буфером обмена и обработкой текста."""
    
    def __init__(self, template_engine: TemplateEngine):
        """Инициализировать сервис буфера обмена.
        
        Args:
            template_engine: Движок рендера шаблонов комментариев.
        """
        self.template_engine = template_engine
        self._keyboard = Controller()
        self._copy_delay = 0.04  # Минимальная стабильная задержка после Ctrl+C
        self._paste_delay = 0.03  # Увеличено для более надежной вставки перед Ctrl+V
        self._post_paste_clear_delay = 0.06  # Увеличено перед восстановлением буфера после Ctrl+V
        self._copy_shortcut = "ctrl+c"
        self._paste_shortcut = "ctrl+v"
        self._clipboard_backup: Optional[str] = None

    def _ensure_clipboard_backup(self) -> None:
        """Сохранить текущее значение буфера, если еще не сохранено."""
        if self._clipboard_backup is not None:
            return
        self._clipboard_backup = pyperclip.paste()
        logger.debug("Исходное значение буфера обмена сохранено")

    def restore_original_clipboard(self) -> None:
        """Восстановить исходное значение буфера обмена после обработки."""
        if self._clipboard_backup is None:
            return
        try:
            pyperclip.copy(self._clipboard_backup)
            logger.debug("Буфер обмена восстановлен")
        finally:
            self._clipboard_backup = None
    
    def capture_selected_text(
        self,
        copy_delay: Optional[float] = None,
        max_wait: float = 0.35,
        poll_interval: float = 0.05,
    ) -> Optional[str]:
        """Скопировать выделенный текст и вернуть его из буфера обмена."""
        try:
            self._ensure_clipboard_backup()
            baseline_text = pyperclip.paste()
            baseline_seq = self._get_clipboard_sequence_number()
            logger.info("Захват выделенного текста через комбинацию копирования")

            effective_copy_delay = self._copy_delay if copy_delay is None else max(0.0, copy_delay)
            self._send_shortcut(self._copy_shortcut)
            if effective_copy_delay > 0:
                time.sleep(effective_copy_delay)

            text = baseline_text
            copied = False
            deadline = time.time() + max(0.0, max_wait)
            while time.time() < deadline:
                time.sleep(max(0.005, poll_interval))
                text = pyperclip.paste()
                if baseline_seq is not None:
                    current_seq = self._get_clipboard_sequence_number()
                    if current_seq is not None and current_seq != baseline_seq:
                        copied = True
                        break
                elif text != baseline_text:
                    copied = True
                    break

            if not copied:
                logger.warning("Буфер обмена не изменился после копирования, продолжаем с пустым текстом")
                return ""

            if text and not str(text).strip():
                # Некоторые приложения кладут в буфер только пробелы/перевод строки.
                text = ""
            if not text:
                logger.warning("Буфер обмена пуст после копирования, продолжаем с пустым текстом")
                return ""

            logger.info("Захвачен текст длиной %d символов", len(text))
            return text
        except Exception as e:
            logger.error("Не удалось захватить выделенный текст: %s", e, exc_info=True)
            return None

    @staticmethod
    def _get_clipboard_sequence_number() -> Optional[int]:
        """Вернуть номер изменения буфера обмена (Windows API)."""
        if os.name != "nt":
            return None
        try:
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return None

    def process_captured_text(
        self,
        selected_text: str,
        comment: Comment,
        context: Optional[dict] = None,
    ) -> bool:
        """Обработать ранее захваченный текст и вставить результат обратно."""
        try:
            if selected_text is None:
                logger.warning("Выделенный текст отсутствует, обрабатывать нечего")
                return False
            
            # Шаг 1. Применяем шаблон
            logger.info("Применяется шаблон: %s", comment.name)
            modified_text = self._apply_template(selected_text, comment, context)
            first_line_indent = self._detect_first_line_indent(selected_text)
            indent_prefix = self._detect_indent_prefix(selected_text)
            if first_line_indent or indent_prefix:
                modified_text = self._apply_indent_prefix(
                    modified_text,
                    first_line_indent,
                    indent_prefix,
                )
            
            # Шаг 2. Записываем результат в буфер обмена
            logger.info("Запись результата в буфер обмена")
            pyperclip.copy(modified_text)
            if self._paste_delay > 0:
                time.sleep(self._paste_delay)
            
            # Шаг 3. Эмулируем сочетание вставки
            logger.info(
                "Эмуляция сочетания вставки (%s), длина текста=%d",
                self._paste_shortcut,
                len(modified_text),
            )
            self._send_shortcut(self._paste_shortcut)
            # Даем приложению время принять Ctrl+V перед восстановлением буфера.
            time.sleep(self._post_paste_clear_delay)

            logger.info("Обработка текста завершена успешно")
            return True
            
        except Exception as e:
            logger.error("Не удалось обработать текст: %s", e, exc_info=True)
            return False
        finally:
            self.restore_original_clipboard()

    def _send_shortcut(self, shortcut: str) -> None:
        """Отправить сочетание клавиш через backend `pynput`."""
        parts = [part.strip().lower() for part in shortcut.split("+") if part.strip()]
        if not parts:
            return

        key_map = {
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "cmd": Key.cmd,
            "win": Key.cmd,
        }

        modifiers = []
        primary_key = None
        for part in parts:
            if part in key_map:
                modifiers.append(key_map[part])
                continue
            primary_key = part

        if primary_key is None:
            raise ValueError(f"Shortcut without primary key: {shortcut}")

        # На момент обработки глобальной клавиши модификаторы могут быть еще "зажаты".
        # Пробуем принудительно отпустить базовые модификаторы и даем ОС применить состояние.
        for modifier in (Key.ctrl, Key.alt, Key.shift, Key.cmd):
            try:
                self._keyboard.release(modifier)
            except Exception:
                pass
        time.sleep(0.002)

        for modifier in modifiers:
            self._keyboard.press(modifier)
        try:
            key_obj = self._resolve_primary_key(primary_key)
            self._keyboard.press(key_obj)
            self._keyboard.release(key_obj)
        finally:
            for modifier in reversed(modifiers):
                self._keyboard.release(modifier)
    
    def _apply_template(self, text: str, comment: Comment, context: Optional[dict] = None) -> str:
        """Применить шаблон комментария к тексту.
        
        Args:
            text: Исходный текст.
            comment: Шаблон комментария.
            context: Дополнительный контекст.
            
        Returns:
            Преобразованный текст.
        """
        if context is None:
            context = {}
        
        return self.template_engine.render(comment.template, text, context)
    
    def set_delays(self, copy_delay: float, paste_delay: float) -> None:
        """Установить задержки для операций буфера обмена.
        
        Args:
            copy_delay: Задержка после Ctrl+C в секундах.
            paste_delay: Задержка перед Ctrl+V в секундах.
        """
        self._copy_delay = copy_delay
        self._paste_delay = paste_delay
        logger.info("Установлены задержки: copy=%ss, paste=%ss", copy_delay, paste_delay)

    def set_post_paste_clear_delay(self, delay: float) -> None:
        """Установить паузу после вставки и перед очисткой буфера."""
        self._post_paste_clear_delay = max(0.0, delay)
        logger.info("Установлена пауза после вставки перед очисткой буфера: %ss", delay)

    @staticmethod
    def _resolve_primary_key(token: str):
        """Преобразовать токен клавиши в объект pynput key/keycode."""
        normalized = (token or "").strip().lower()
        if not normalized:
            return normalized
        if os.name == "nt":
            if len(normalized) == 1 and "a" <= normalized <= "z":
                return KeyCode.from_vk(ord(normalized.upper()))
            if len(normalized) == 1 and "0" <= normalized <= "9":
                return KeyCode.from_vk(ord(normalized))
            vk_map = {
                "=": 0xBB,
                "+": 0xBB,
                "-": 0xBD,
                "_": 0xBD,
                ".": 0xBE,
                ",": 0xBC,
                "/": 0xBF,
                "\\": 0xDC,
                ";": 0xBA,
                ":": 0xBA,
                "'": 0xDE,
                '"': 0xDE,
                "[": 0xDB,
                "]": 0xDD,
                "`": 0xC0,
            }
            vk = vk_map.get(normalized)
            if vk is not None:
                return KeyCode.from_vk(vk)
        return normalized

    @staticmethod
    def _indent_width(prefix: str) -> int:
        """Рассчитать ширину отступа, где табуляция считается как 4 пробела."""
        width = 0
        for ch in prefix:
            if ch == "\t":
                width += 4
            elif ch == " ":
                width += 1
        return width

    def _detect_indent_prefix(self, text: str) -> str:
        """Найти минимальный отступ среди строк выделенного текста.

        Если среди непустых строк, начиная со второй, есть строка без отступа,
        считаем минимальный отступ нулевым и не добавляем префикс.
        """
        candidate_prefix = ""
        candidate_width = None

        for index, line in enumerate(text.splitlines()):
            if not line:
                continue
            match = re.match(r"^[ \t]+", line)
            if not match:
                if index > 0 and line.strip():
                    return ""
                continue
            prefix = match.group(0)
            # Пропускаем строки, состоящие только из отступа.
            if len(prefix) == len(line):
                continue
            width = self._indent_width(prefix)
            if width <= 0:
                continue
            if candidate_width is None or width < candidate_width:
                candidate_width = width
                candidate_prefix = prefix

        return candidate_prefix

    @staticmethod
    def _detect_first_line_indent(text: str) -> str:
        """Получить префикс отступа первой строки выделенного текста."""
        lines = text.splitlines()
        if not lines:
            return ""
        if not lines[0].strip():
            return ""
        match = re.match(r"^[ \t]*", lines[0])
        return match.group(0) if match else ""

    @staticmethod
    def _apply_indent_prefix(
        text: str,
        first_line_indent: str,
        following_lines_indent: str,
    ) -> str:
        """Применить правила отступов для первой и последующих строк."""
        if not first_line_indent and not following_lines_indent:
            return text

        lines = text.splitlines()
        if not lines:
            return text

        adjusted = []
        for index, line in enumerate(lines):
            if not line:
                adjusted.append(line)
                continue
            if index == 0:
                if first_line_indent and not line.startswith((" ", "\t")):
                    adjusted.append(f"{first_line_indent}{line}")
                else:
                    adjusted.append(line)
                continue
            if line.startswith((" ", "\t")):
                adjusted.append(line)
                continue
            if following_lines_indent:
                adjusted.append(f"{following_lines_indent}{line}")
            else:
                adjusted.append(line)

        return "\n".join(adjusted)
