"""Регистрация и обработка глобальных горячих клавиш (backend: pynput)."""

import logging
import os
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    pynput_keyboard = None

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Менеджер регистрации и обработки глобальных горячих клавиш."""

    def __init__(self):
        self._registered_hotkeys: List[str] = []
        self._handlers: Dict[str, Callable] = {}
        self._specs: Dict[str, Tuple[Set[str], str]] = {}
        self._listener = None
        self._pressed_modifiers: Set[str] = set()
        self._pressed_keys: Set[str] = set()
        self._fired_hotkeys: Set[str] = set()

    @property
    def default_hotkey(self) -> str:
        return "ctrl+="

    def register(self, combination: str, handler: Callable) -> bool:
        """Зарегистрировать глобальную горячую клавишу."""
        if pynput_keyboard is None:
            logger.error(
                "Библиотека pynput не установлена, глобальные горячие клавиши недоступны. "
                "Установите зависимости из requirements.txt."
            )
            return False

        normalized = self._normalize_combination(combination)
        parsed = self._parse_combination(normalized)
        if not parsed:
            logger.error("Неподдерживаемый формат горячей клавиши: %s", combination)
            return False
        modifiers, key_id = parsed

        previous_handler = self._handlers.get(normalized)
        previous_spec = self._specs.get(normalized)
        was_registered = normalized in self._registered_hotkeys

        self._handlers[normalized] = handler
        self._specs[normalized] = (modifiers, key_id)
        if not was_registered:
            self._registered_hotkeys.append(normalized)

        if self._rebuild_listener():
            logger.info("Горячая клавиша зарегистрирована: %s", normalized)
            return True

        # Откат на случай ошибки при пересборке listener.
        if previous_handler is None:
            self._handlers.pop(normalized, None)
            self._specs.pop(normalized, None)
            if normalized in self._registered_hotkeys:
                self._registered_hotkeys.remove(normalized)
        else:
            self._handlers[normalized] = previous_handler
            if previous_spec is not None:
                self._specs[normalized] = previous_spec
            self._rebuild_listener()
        return False

    def unregister(self, combination: str) -> bool:
        """Снять регистрацию глобальной горячей клавиши."""
        normalized = self._normalize_combination(combination)
        if normalized not in self._registered_hotkeys:
            return True

        self._registered_hotkeys.remove(normalized)
        self._handlers.pop(normalized, None)
        self._specs.pop(normalized, None)
        if not self._rebuild_listener():
            logger.error("Не удалось снять регистрацию горячей клавиши: %s", normalized)
            return False
        logger.info("Регистрация горячей клавиши снята: %s", normalized)
        return True

    def unregister_all(self) -> None:
        """Снять регистрацию всех горячих клавиш."""
        self._registered_hotkeys.clear()
        self._handlers.clear()
        self._specs.clear()
        self._pressed_modifiers.clear()
        self._pressed_keys.clear()
        self._fired_hotkeys.clear()
        if self._listener:
            self._listener.stop()
            self._listener = None

    def get_registered(self) -> List[str]:
        """Вернуть список зарегистрированных комбинаций."""
        return self._registered_hotkeys.copy()

    def is_registered(self, combination: str) -> bool:
        """Проверить, зарегистрирована ли комбинация."""
        normalized = self._normalize_combination(combination)
        return normalized in self._registered_hotkeys

    def _rebuild_listener(self) -> bool:
        if pynput_keyboard is None:
            return False

        try:
            if self._listener:
                self._listener.stop()
                self._listener = None

            self._pressed_modifiers.clear()
            self._pressed_keys.clear()
            self._fired_hotkeys.clear()

            if not self._registered_hotkeys:
                return True

            self._listener = pynput_keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self._listener.start()
            return True
        except Exception as exc:
            logger.error("Не удалось пересобрать listener глобальных горячих клавиш: %s", exc)
            return False

    @staticmethod
    def _normalize_combination(combination: str) -> str:
        normalized = (combination or "").strip().lower()
        normalized = normalized.replace("control+", "ctrl+")
        normalized = normalized.replace("command+", "cmd+")
        normalized = normalized.replace("option+", "alt+")
        return normalized

    def _parse_combination(self, combination: str) -> Optional[Tuple[Set[str], str]]:
        """Разобрать строку комбинации в набор модификаторов и key-id."""
        parts = [part.strip().lower() for part in combination.split("+") if part.strip()]
        if not parts:
            return None

        modifiers: Set[str] = set()
        key_id: Optional[str] = None
        for part in parts:
            if part in {"ctrl", "control"}:
                modifiers.add("ctrl")
            elif part in {"alt", "option"}:
                modifiers.add("alt")
            elif part in {"shift"}:
                modifiers.add("shift")
            elif part in {"cmd", "command", "meta", "win", "super"}:
                modifiers.add("cmd")
            elif part.startswith("f") and part[1:].isdigit():
                key_id = f"key:{part}"
            elif part in {"space", "tab", "enter", "esc", "up", "down", "left", "right"}:
                key_id = f"key:{part}"
            elif part in {"backspace", "delete", "home", "end", "pageup", "pagedown", "insert"}:
                key_id = f"key:{part}"
            elif part.startswith("vk:") and part[3:].isdigit():
                key_id = f"vk:{part[3:]}"
            else:
                vk = self._token_to_windows_vk(part)
                if vk is not None:
                    key_id = f"vk:{vk}"
                else:
                    if len(part) != 1:
                        return None
                    key_id = f"char:{part}"

        if not key_id:
            return None
        return modifiers, key_id

    def _on_key_press(self, key) -> None:
        modifier = self._modifier_from_key(key)
        if modifier:
            self._pressed_modifiers.add(modifier)
        for key_id in self._key_to_ids(key):
            if not key_id.startswith("mod:"):
                self._pressed_keys.add(key_id)
        self._check_triggers()

    def _on_key_release(self, key) -> None:
        modifier = self._modifier_from_key(key)
        if modifier:
            self._pressed_modifiers.discard(modifier)
        for key_id in self._key_to_ids(key):
            if not key_id.startswith("mod:"):
                self._pressed_keys.discard(key_id)

        for combo in list(self._fired_hotkeys):
            spec = self._specs.get(combo)
            if not spec:
                self._fired_hotkeys.discard(combo)
                continue
            modifiers, key_id = spec
            if not (modifiers.issubset(self._pressed_modifiers) and key_id in self._pressed_keys):
                self._fired_hotkeys.discard(combo)

    def _check_triggers(self) -> None:
        for combo in self._registered_hotkeys:
            if combo in self._fired_hotkeys:
                continue
            spec = self._specs.get(combo)
            if not spec:
                continue
            modifiers, key_id = spec
            if modifiers.issubset(self._pressed_modifiers) and key_id in self._pressed_keys:
                self._fired_hotkeys.add(combo)
                handler = self._handlers.get(combo)
                if handler:
                    try:
                        handler()
                    except Exception as exc:
                        logger.error("Ошибка обработчика горячей клавиши %s: %s", combo, exc)

    @staticmethod
    def _modifier_from_key(key) -> Optional[str]:
        if pynput_keyboard is None:
            return None
        if key in {pynput_keyboard.Key.ctrl, pynput_keyboard.Key.ctrl_l, pynput_keyboard.Key.ctrl_r}:
            return "ctrl"
        if key in {pynput_keyboard.Key.alt, pynput_keyboard.Key.alt_l, pynput_keyboard.Key.alt_r}:
            return "alt"
        if key in {pynput_keyboard.Key.shift, pynput_keyboard.Key.shift_l, pynput_keyboard.Key.shift_r}:
            return "shift"
        if key in {
            pynput_keyboard.Key.cmd,
            getattr(pynput_keyboard.Key, "cmd_l", pynput_keyboard.Key.cmd),
            getattr(pynput_keyboard.Key, "cmd_r", pynput_keyboard.Key.cmd),
        }:
            return "cmd"
        return None

    @staticmethod
    def _key_to_ids(key) -> Set[str]:
        if pynput_keyboard is None:
            return set()

        special_map = {
            pynput_keyboard.Key.space: "key:space",
            pynput_keyboard.Key.tab: "key:tab",
            pynput_keyboard.Key.enter: "key:enter",
            pynput_keyboard.Key.esc: "key:esc",
            pynput_keyboard.Key.up: "key:up",
            pynput_keyboard.Key.down: "key:down",
            pynput_keyboard.Key.left: "key:left",
            pynput_keyboard.Key.right: "key:right",
            pynput_keyboard.Key.backspace: "key:backspace",
            pynput_keyboard.Key.delete: "key:delete",
            pynput_keyboard.Key.home: "key:home",
            pynput_keyboard.Key.end: "key:end",
            pynput_keyboard.Key.page_up: "key:pageup",
            pynput_keyboard.Key.page_down: "key:pagedown",
            pynput_keyboard.Key.insert: "key:insert",
        }
        if key in special_map:
            return {special_map[key]}
        for index in range(1, 25):
            f_key = getattr(pynput_keyboard.Key, f"f{index}", None)
            if f_key is not None and key == f_key:
                return {f"key:f{index}"}

        ids: Set[str] = set()
        vk = getattr(key, "vk", None)
        if vk is not None:
            ids.add(f"vk:{vk}")
        char = getattr(key, "char", None)
        if isinstance(char, str) and char:
            ids.add(f"char:{char.lower()}")
        return ids

    @staticmethod
    def _token_to_windows_vk(token: str) -> Optional[int]:
        """Преобразовать текстовый токен в Windows VK-код для layout-независимой регистрации."""
        if os.name != "nt":
            return None

        if len(token) == 1 and "a" <= token <= "z":
            return ord(token.upper())
        if len(token) == 1 and token.isdigit():
            return ord(token)

        mapping = {
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
        named = {
            "plus": 0xBB,
            "minus": 0xBD,
            "period": 0xBE,
            "comma": 0xBC,
            "slash": 0xBF,
            "backslash": 0xDC,
            "semicolon": 0xBA,
            "apostrophe": 0xDE,
            "lbracket": 0xDB,
            "rbracket": 0xDD,
            "grave": 0xC0,
        }
        if token in mapping:
            return mapping[token]
        return named.get(token)
