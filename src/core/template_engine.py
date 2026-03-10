"""Движок подстановки макросов для шаблонов комментариев."""

import re
import shlex
import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class TemplateEngine:
    """Движок обработки макросов в шаблонах."""

    @dataclass(frozen=True)
    class _LiteralToken:
        text: str

    @dataclass(frozen=True)
    class _MacroToken:
        raw: str
        name: str
        modifiers: tuple[tuple[str, str], ...]

    @dataclass(frozen=True)
    class _BlockToken:
        raw_start: str
        name: str
        args: tuple[tuple[str, str], ...]
        children: tuple["TemplateEngine._Token", ...]

    @dataclass
    class _OpenBlock:
        raw_start: str
        name: str
        args: tuple[tuple[str, str], ...]
        children: list["TemplateEngine._Token"]

    _Token = _LiteralToken | _MacroToken | _BlockToken

    def __init__(self):
        self._compiled_cache: dict[str, tuple[TemplateEngine._Token, ...]] = {}
        self._macro_handlers: dict[str, Callable[[str, dict], str]] = {
            "text": lambda text, _context: text,
            "date": self._macro_date,
            "time": self._macro_time,
            "datetime": self._macro_datetime,
            "author": lambda _text, context: str(context.get("author", "")),
            "issue_key": lambda _text, context: str(context.get("issue_key", "")),
            "issue_summary": lambda _text, context: self._sanitize_jira_text(context.get("issue_summary", "")),
        }
        self._modifier_handlers: dict[str, Callable[[str, str, str], str]] = {
            "prefix": self._modifier_prefix,
        }
        self._block_handlers: dict[str, Callable[[str, tuple[tuple[str, str], ...]], str]] = {
            "line_limit": self._block_line_limit,
        }

    def render(self, template: str, text: str, context: Optional[Dict] = None) -> str:
        """Подставить значения макросов в шаблон."""
        if context is None:
            context = {}

        compiled = self._compiled_cache.get(template)
        if compiled is None:
            compiled = self._compile_template(template)
            self._compiled_cache[template] = compiled

        return self._render_tokens(compiled, text, context)

    def _render_tokens(self, tokens: tuple[_Token, ...], text: str, context: dict) -> str:
        parts = []
        for token in tokens:
            if isinstance(token, self._LiteralToken):
                parts.append(token.text)
                continue

            if isinstance(token, self._MacroToken):
                value = self._resolve_macro(token, text, context)
                parts.append(value)
                continue

            block_value = self._render_tokens(token.children, text, context)
            handler = self._block_handlers.get(token.name)
            if handler is None:
                parts.append(token.raw_start)
                parts.append(block_value)
                continue
            try:
                parts.append(handler(block_value, token.args))
            except Exception:
                logger.exception("Ошибка применения блочной директивы '%s'", token.name)
                parts.append(token.raw_start)
                parts.append(block_value)

        return "".join(parts)

    def _resolve_macro(self, token: _MacroToken, text: str, context: dict) -> str:
        handler = self._macro_handlers.get(token.name)
        if handler is None:
            return token.raw

        try:
            value = str(handler(text, context))
        except Exception:
            logger.exception("Ошибка вычисления макроса '%s'", token.name)
            return token.raw

        for modifier_name, modifier_arg in token.modifiers:
            modifier = self._modifier_handlers.get(modifier_name)
            if modifier is None:
                logger.warning("Неизвестный модификатор '%s' в макросе '%s'", modifier_name, token.raw)
                continue
            try:
                value = modifier(value, modifier_arg, token.name)
            except Exception:
                logger.exception("Ошибка применения модификатора '%s' к макросу '%s'", modifier_name, token.raw)
                return token.raw

        return value

    def _compile_template(self, template: str) -> tuple[_Token, ...]:
        root_tokens: list[TemplateEngine._Token] = []
        token_lists: list[list[TemplateEngine._Token]] = [root_tokens]
        open_blocks: list[TemplateEngine._OpenBlock] = []
        idx = 0
        length = len(template)

        while idx < length:
            start = template.find("{", idx)
            if start == -1:
                if idx < length:
                    token_lists[-1].append(self._LiteralToken(template[idx:]))
                break

            if start > idx:
                token_lists[-1].append(self._LiteralToken(template[idx:start]))

            end = template.find("}", start + 1)
            if end == -1:
                token_lists[-1].append(self._LiteralToken(template[start:]))
                break

            raw = template[start:end + 1]
            directive = self._parse_directive(raw)
            if directive is not None:
                directive_name, directive_args = directive
                if directive_name == "end":
                    if not open_blocks:
                        token_lists[-1].append(self._LiteralToken(raw))
                    else:
                        finished = open_blocks.pop()
                        token_lists.pop()
                        token_lists[-1].append(
                            self._BlockToken(
                                raw_start=finished.raw_start,
                                name=finished.name,
                                args=finished.args,
                                children=tuple(finished.children),
                            )
                        )
                else:
                    if directive_name not in self._block_handlers:
                        token_lists[-1].append(self._LiteralToken(raw))
                    else:
                        block = self._OpenBlock(
                            raw_start=raw,
                            name=directive_name,
                            args=directive_args,
                            children=[],
                        )
                        open_blocks.append(block)
                        token_lists.append(block.children)
                idx = end + 1
                continue

            parsed_macro = self._parse_macro(raw)
            if parsed_macro is None:
                token_lists[-1].append(self._LiteralToken(raw))
            else:
                token_lists[-1].append(parsed_macro)

            idx = end + 1

        while open_blocks:
            orphan = open_blocks.pop()
            token_lists.pop()
            token_lists[-1].append(self._LiteralToken(orphan.raw_start))
            token_lists[-1].extend(orphan.children)

        return tuple(root_tokens)

    def _parse_macro(self, raw: str) -> Optional[_MacroToken]:
        if not (raw.startswith("{") and raw.endswith("}")):
            return None

        inner = raw[1:-1].strip()
        if not inner:
            return None

        parts = self._split_unquoted(inner, "|")
        if not parts:
            return None

        name = parts[0].strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            return None

        modifiers: list[tuple[str, str]] = []
        for modifier_raw in parts[1:]:
            pair = self._parse_modifier(modifier_raw)
            if pair is None:
                return None
            modifiers.append(pair)

        return self._MacroToken(raw=raw, name=name, modifiers=tuple(modifiers))

    def _parse_modifier(self, modifier_raw: str) -> Optional[tuple[str, str]]:
        part = modifier_raw.strip()
        if not part:
            return None

        key_value = self._split_unquoted(part, "=")
        if len(key_value) != 2:
            return None

        key = key_value[0].strip()
        value_raw = key_value[1].strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", key):
            return None

        value = self._parse_modifier_value(value_raw)
        if value is None:
            return None

        return key, value

    @staticmethod
    def _parse_modifier_value(value_raw: str) -> Optional[str]:
        if not value_raw:
            return ""

        if value_raw[0] in ("'", '"'):
            quote = value_raw[0]
            if len(value_raw) < 2 or value_raw[-1] != quote:
                return None
            body = value_raw[1:-1]
            return (
                body
                .replace("\\\\", "\\")
                .replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\'", "'")
            )

        return value_raw

    @staticmethod
    def _split_unquoted(value: str, separator: str) -> list[str]:
        result = []
        current = []
        quote = ""
        escaped = False

        for ch in value:
            if escaped:
                current.append(ch)
                escaped = False
                continue
            if ch == "\\":
                current.append(ch)
                escaped = True
                continue
            if quote:
                current.append(ch)
                if ch == quote:
                    quote = ""
                continue
            if ch in ("'", '"'):
                quote = ch
                current.append(ch)
                continue
            if ch == separator:
                result.append("".join(current))
                current = []
                continue
            current.append(ch)

        result.append("".join(current))
        return result

    def _parse_directive(self, raw: str) -> Optional[tuple[str, tuple[tuple[str, str], ...]]]:
        if not (raw.startswith("{@") and raw.endswith("}")):
            return None

        content = raw[2:-1].strip()
        if not content:
            return None

        if content == "end":
            return "end", ()

        try:
            parts = shlex.split(content)
        except ValueError:
            return None
        if not parts:
            return None

        name = parts[0].strip().lower()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            return None

        args: list[tuple[str, str]] = []
        for part in parts[1:]:
            if "=" not in part:
                return None
            key, value = part.split("=", 1)
            key = key.strip().lower()
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", key):
                return None
            args.append((key, value))

        return name, tuple(args)

    @staticmethod
    def _macro_date(_text: str, _context: dict) -> str:
        return datetime.now().strftime("%d.%m.%Y")

    @staticmethod
    def _macro_time(_text: str, _context: dict) -> str:
        return datetime.now().strftime("%H:%M")

    @staticmethod
    def _macro_datetime(_text: str, _context: dict) -> str:
        return datetime.now().strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _modifier_prefix(value: str, arg: str, macro_name: str) -> str:
        if macro_name != "text" or not arg:
            return value

        lines = value.splitlines(keepends=True)
        if not lines:
            return value

        result = []
        for line in lines:
            line_ending = ""
            if line.endswith("\r\n"):
                line_ending = "\r\n"
                line = line[:-2]
            elif line.endswith("\n"):
                line_ending = "\n"
                line = line[:-1]
            elif line.endswith("\r"):
                line_ending = "\r"
                line = line[:-1]

            if not line:
                result.append(line_ending)
                continue

            match = re.match(r"^[ \t]*", line)
            leading = match.group(0) if match else ""
            rest = line[len(leading):]
            result.append(f"{leading}{arg}{rest}{line_ending}")

        return "".join(result)

    @staticmethod
    def _extract_line_ending(line: str) -> tuple[str, str]:
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith("\n"):
            return line[:-1], "\n"
        if line.endswith("\r"):
            return line[:-1], "\r"
        return line, ""

    def _block_line_limit(self, value: str, args: tuple[tuple[str, str], ...]) -> str:
        options = {k: v for k, v in args}
        raw_max = options.get("max", "").strip()
        if not raw_max:
            return value
        try:
            max_len = int(raw_max)
        except ValueError:
            logger.warning("Некорректное значение max для директивы line_limit: %s", raw_max)
            return value
        if max_len <= 0:
            return value

        mode = options.get("mode", "wrap").strip().lower()
        if mode != "wrap":
            logger.warning("Неподдерживаемый mode для line_limit: %s", mode)
            return value

        suffix = options.get("suffix", "")
        wrapped_lines = []
        for original_line in value.splitlines(keepends=True):
            line, line_ending = self._extract_line_ending(original_line)
            if len(line) <= max_len:
                wrapped_lines.append(original_line)
                continue

            leading_ws_match = re.match(r"^[ \t]*", line)
            leading_ws = leading_ws_match.group(0) if leading_ws_match else ""
            content = line[len(leading_ws):]
            if not content:
                wrapped_lines.append(original_line)
                continue

            parts = textwrap.wrap(
                content,
                width=max_len,
                break_long_words=False,
                break_on_hyphens=False,
                initial_indent=leading_ws,
                subsequent_indent=f"{leading_ws}{suffix}",
            )
            if not parts:
                wrapped_lines.append(original_line)
                continue

            wrapped_lines.append("\n".join(parts) + line_ending)

        return "".join(wrapped_lines)

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
            '{author}': 'Автор комментария из настроек приложения',
            '{issue_key}': 'Ключ задачи Jira',
            '{issue_summary}': 'Краткое описание задачи Jira',
        }
