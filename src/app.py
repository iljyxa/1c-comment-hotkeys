"""Точка входа приложения."""

import sys
import logging
import platform
import time
import threading
import ctypes
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, QTimer, Signal, Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QPixmap
import resources_rc

from core.comments_repository import CommentsRepository
from core.template_engine import TemplateEngine
from core.clipboard_service import ClipboardService
from core.hotkeys import HotkeyManager
from core.settings_repository import SettingsRepository
from core.jira_sources_repository import JiraSourcesRepository
from core.jira_issues_cache import JiraIssuesCache
from core.jira_issues_service import JiraIssuesService, JiraIssuesError
from core.jira_last_issue_repository import JiraLastIssueRepository
from ui.main_window import MainWindow
from ui.comment_dialog import CommentDialog
from ui.issue_dialog import IssueDialog

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AppSignals(QObject):
    """Сигналы для безопасного взаимодействия с главным потоком Qt."""

    show_comment_dialog = Signal()
    hotkey_triggered = Signal()
    quick_comment_hotkey_triggered = Signal(object)


class Application:
    """Основной класс приложения."""
    
    def __init__(self):
        """Инициализировать приложение."""
        logger.info("Инициализация приложения")
        
        # Инициализация Qt
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName("1C Comment Hotkeys")
        self.qt_app.setOrganizationName("IlyaFedorov")
        self.qt_app.setQuitOnLastWindowClosed(False)
        self._setup_application_icon()
        self.signals = AppSignals()
        self.signals.show_comment_dialog.connect(self._show_comment_dialog)
        self.signals.hotkey_triggered.connect(self._on_hotkey_triggered_main_thread)
        self.signals.quick_comment_hotkey_triggered.connect(
            self._on_quick_comment_hotkey_main_thread
        )
        
        # Инициализация сервисов
        self.repository = CommentsRepository()
        self.repository.load()
        
        self.template_engine = TemplateEngine()
        self.clipboard_service = ClipboardService(self.template_engine)
        self.hotkey_manager = HotkeyManager()
        self.settings_repository = SettingsRepository(
            default_hotkey=self.hotkey_manager.default_hotkey,
        )
        self.settings_repository.load()
        self._file_log_handler = None
        self._apply_file_logging(self.settings_repository.get_log_to_file())
        self.jira_sources_repository = JiraSourcesRepository(
            config_dir=self.repository.config_dir
        )
        self.jira_sources_repository.load()
        
        # Инициализация интеграции Jira
        config_dir = self.repository.config_dir
        self.jira_issues_cache = JiraIssuesCache(config_dir)
        self.jira_issues_cache.load()
        self.jira_issues_service = JiraIssuesService(self.jira_issues_cache)
        self.jira_last_issue_repository = JiraLastIssueRepository(config_dir)
        self.jira_last_issue_repository.load()
        
        # Инициализация UI
        self.main_window = MainWindow(
            repository=self.repository,
            settings_repository=self.settings_repository,
            jira_sources_repository=self.jira_sources_repository,
            hotkey_change_handler=self._apply_hotkey,
            log_to_file_change_handler=self._apply_file_logging,
            refresh_sources_handler=self._refresh_all_sources,
            exit_handler=self.request_exit,
        )
        self.main_window.setWindowIcon(self.qt_app.windowIcon())
        self.comment_dialog = None
        self._captured_text = None
        self._hotkey_capture_in_progress = False
        self._hotkey_cooldown_until = 0.0
        self._target_window_handle = None
        
        # Регистрация горячих клавиш
        self._setup_hotkeys()
        
        logger.info("Приложение инициализировано")

    def _apply_file_logging(self, enabled: bool) -> None:
        """Включить/выключить запись логов в файл."""
        root_logger = logging.getLogger()
        if enabled:
            if self._file_log_handler is not None:
                return
            log_path = Path(self.settings_repository.get_config_dir()) / "app.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            ))
            root_logger.addHandler(handler)
            self._file_log_handler = handler
            logger.info("Логирование в файл включено: %s", log_path)
            return

        if self._file_log_handler is None:
            return
        root_logger.removeHandler(self._file_log_handler)
        self._file_log_handler.close()
        self._file_log_handler = None
        logger.info("Логирование в файл отключено")

    def _refresh_all_sources(self) -> None:
        """Обновить все источники Jira в фоне."""
        sources = self.jira_sources_repository.get_all()
        if not sources:
            QMessageBox.information(
                self.main_window,
                "Jira",
                "Список источников пуст.",
            )
            return

        def worker() -> None:
            errors: list[str] = []
            for source in sources:
                try:
                    self.jira_issues_service.refresh_source(source)
                except Exception as exc:
                    errors.append(f"{source.name}: {exc}")

            def notify() -> None:
                if errors:
                    QMessageBox.warning(
                        self.main_window,
                        "Jira",
                        "Не удалось обновить некоторые источники:\n" + "\n".join(errors),
                    )
                else:
                    QMessageBox.information(
                        self.main_window,
                        "Jira",
                        "Источники успешно обновлены.",
                    )

            QTimer.singleShot(0, notify)

        threading.Thread(target=worker, daemon=True).start()

    def _setup_application_icon(self) -> None:
        """Установить иконку приложения из ресурсов."""
        icon_path = ":/icons/icon.svg"
        icon = QIcon(icon_path)
        if icon.isNull():
            icon = self._render_svg_icon(icon_path)
        if icon.isNull():
            logger.warning("Иконка приложения не загружена: %s", icon_path)
            return

        self.qt_app.setWindowIcon(icon)
        logger.info("Иконка приложения загружена: %s", icon_path)

    @staticmethod
    def _render_svg_icon(icon_path: str) -> QIcon:
        """Рендер SVG в растровые размеры, если прямой загрузки недостаточно."""
        try:
            from PySide6.QtSvg import QSvgRenderer
        except Exception as exc:
            logger.warning("QtSvg недоступен, не удалось отрисовать SVG-иконку: %s", exc)
            return QIcon()

        renderer = QSvgRenderer(icon_path)
        if not renderer.isValid():
            logger.warning("Средство рендеринга SVG не смогло загрузить иконку: %s", icon_path)
            return QIcon()

        icon = QIcon()
        for size in (16, 24, 32, 48, 64, 128, 256):
            pixmap = QPixmap(QSize(size, size))
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(pixmap)
        return icon

    def _setup_hotkeys(self) -> None:
        """Зарегистрировать глобальные горячие клавиши."""
        hotkey = self.settings_repository.get_hotkey()
        success, message = self._apply_hotkey(hotkey)
        
        if success:
            if hotkey.strip():
                logger.info("Зарегистрирована горячая клавиша: %s", hotkey)
            else:
                logger.info("Глобальная горячая клавиша отключена")
        else:
            logger.error("Не удалось зарегистрировать горячую клавишу %s: %s", hotkey, message)
            fallback_hotkey = self.hotkey_manager.default_hotkey
            fallback_success, _ = self._apply_hotkey(fallback_hotkey)
            if hotkey != fallback_hotkey and fallback_success:
                logger.warning(
                    "Зарегистрирована резервная горячая клавиша: %s",
                    fallback_hotkey,
                )
                self.settings_repository.set_hotkey(fallback_hotkey)

    def _apply_hotkey(self, combination: str) -> tuple[bool, str]:
        """Пере-регистрировать основную и быстрые клавиши комментариев."""
        previous_hotkey = self.settings_repository.get_hotkey()
        success, message = self._register_all_hotkeys(combination)
        if success:
            self.settings_repository.set_hotkey(combination)
            return True, "ok"

        if previous_hotkey and previous_hotkey != combination:
            restored, _ = self._register_all_hotkeys(previous_hotkey)
            if restored:
                logger.warning("После неудачного обновления восстановлена предыдущая клавиша: %s", previous_hotkey)
                self.settings_repository.set_hotkey(previous_hotkey)
        return False, message

    def _register_all_hotkeys(self, global_hotkey: str) -> tuple[bool, str]:
        """Зарегистрировать основную и быстрые клавиши комментариев."""
        comments = self.repository.get_all()
        quick_hotkeys = []
        for comment in comments:
            combo = (comment.hotkey or "").strip()
            if combo:
                quick_hotkeys.append((combo, comment))

        normalized_seen = {}
        for combo, owner in [(global_hotkey, None)] + quick_hotkeys:
            normalized = self._normalize_hotkey_for_compare(combo)
            if not normalized:
                # Пустая глобальная комбинация разрешена (режим без общего хоткея).
                if owner is None:
                    continue
                return False, "Пустая комбинация горячей клавиши."
            if normalized in normalized_seen:
                return False, f"Конфликт комбинаций: '{combo}' уже используется."
            normalized_seen[normalized] = True

        self.hotkey_manager.unregister_all()
        normalized_global = self._normalize_hotkey_for_compare(global_hotkey)
        if normalized_global and not self.hotkey_manager.register(global_hotkey, self._on_hotkey_pressed):
            return False, "Не удалось зарегистрировать основную горячую клавишу."

        for combo, comment in quick_hotkeys:
            if not self.hotkey_manager.register(
                combo,
                lambda c=comment: self._on_quick_comment_hotkey_pressed(c),
            ):
                return False, f"Не удалось зарегистрировать быструю клавишу: {combo}"

        return True, "ok"

    @staticmethod
    def _normalize_hotkey_for_compare(combination: str) -> str:
        """Нормализовать строку комбинации для проверки дубликатов."""
        normalized = (combination or "").strip().lower()
        return normalized
    
    def _on_hotkey_pressed(self) -> None:
        """Обработать нажатие основной горячей клавиши."""
        self.signals.hotkey_triggered.emit()

    def _on_quick_comment_hotkey_pressed(self, comment) -> None:
        """Обработать нажатие быстрой клавиши комментария."""
        self.signals.quick_comment_hotkey_triggered.emit(comment)

    def _on_hotkey_triggered_main_thread(self) -> None:
        """Обработать основную клавишу в главном потоке Qt."""
        self._start_capture_flow()

    def _on_quick_comment_hotkey_main_thread(self, comment) -> None:
        """Обработать быструю клавишу комментария в главном потоке Qt."""
        self._start_capture_flow(direct_comment=comment)

    def _start_capture_flow(self, direct_comment=None) -> None:
        """Запустить сценарий захвата текста для диалога или быстрой вставки."""
        now = time.monotonic()
        if now < self._hotkey_cooldown_until:
            logger.debug("Горячая клавиша проигнорирована из-за задержки между срабатываниями")
            return
        if self._hotkey_capture_in_progress:
            logger.debug("Горячая клавиша проигнорирована: захват уже выполняется")
            return
        if self._captured_text is not None:
            logger.debug("Горячая клавиша проигнорирована: захваченный текст ожидает выбора шаблона")
            return
        if self.comment_dialog is not None and self.comment_dialog.isVisible():
            logger.debug("Горячая клавиша проигнорирована: диалог комментариев уже открыт")
            return

        self._target_window_handle = self._capture_foreground_window_handle()
        logger.info("Целевое окно для последующей вставки: %s", self._target_window_handle)
        self._hotkey_capture_in_progress = True
        delay_ms = 50
        QTimer.singleShot(delay_ms, lambda: self._capture_and_continue(direct_comment))

    def _capture_and_continue(self, direct_comment=None) -> None:
        """Скопировать выделение и продолжить сценарий вставки."""
        try:
            is_quick_comment = direct_comment is not None
            captured = self.clipboard_service.capture_selected_text(
                copy_delay=0.03 if is_quick_comment else None,
                max_wait=0.2,
                poll_interval=0.01,
            )
            if captured is None:
                logger.warning("Горячая клавиша нажата, но захват выделенного текста не удался")
                self._hotkey_cooldown_until = time.monotonic() + 0.2
                self.clipboard_service.restore_original_clipboard()
                return

            self._hotkey_cooldown_until = time.monotonic() + 0.1
            if not is_quick_comment:
                self._captured_text = captured
                logger.info("Горячая клавиша нажата, открывается диалог комментариев")
                self.signals.show_comment_dialog.emit()
            else:
                logger.info("Нажата быстрая клавиша комментария: %s", direct_comment.name)
                self._process_text(direct_comment, captured)
        finally:
            self._hotkey_capture_in_progress = False
    
    def _show_comment_dialog(self) -> None:
        """Показать диалог выбора комментария."""
        try:
            logger.info("Открытие диалога выбора комментария")
            # Получаем актуальный список комментариев
            comments = [
                comment
                for comment in self.repository.get_all()
                if not bool(getattr(comment, "hidden", False))
            ]
            
            if not comments:
                logger.warning("Нет доступных комментариев")
                self._captured_text = None
                self.clipboard_service.restore_original_clipboard()
                return
            
            # Создаем и показываем диалог выбора комментария
            self.comment_dialog = CommentDialog(comments)

            result = self.comment_dialog.exec()
            selected_comment = self.comment_dialog.get_selected_comment()
            self.comment_dialog = None

            if result == CommentDialog.Rejected:
                logger.info("Диалог выбора комментария отменен")
                self._captured_text = None
                self.clipboard_service.restore_original_clipboard()
                return

            if selected_comment is None:
                logger.warning("Диалог закрыт без выбранного комментария")
                self._captured_text = None
                self.clipboard_service.restore_original_clipboard()
                return

            # Важно: запускаем обработку после полного закрытия модального диалога.
            QTimer.singleShot(0, lambda c=selected_comment: self._on_comment_selected(c))
            
        except Exception as e:
            self._captured_text = None
            self.comment_dialog = None
            self.clipboard_service.restore_original_clipboard()
            logger.error("Ошибка при показе диалога комментариев: %s", e, exc_info=True)
    
    def _on_comment_selected(self, comment) -> None:
        """Обработать выбор комментария.
        
        Args:
            comment: Выбранный комментарий.
        """
        logger.info("Выбран комментарий: %s", comment.name)
        if self._captured_text is None:
            logger.warning("Для выбранного комментария отсутствует захваченный текст")
            self.clipboard_service.restore_original_clipboard()
            return
        
        captured_text = self._captured_text
        self._captured_text = None
        logger.info("Длина захваченного текста для обработки: %d", len(captured_text))
        QTimer.singleShot(0, lambda: self._process_text(comment, captured_text))
    
    def _process_text(self, comment, selected_text: str) -> None:
        """Обработать выделенный текст выбранным комментарием.
        
        Args:
            comment: Применяемый комментарий.
            selected_text: Текст, захваченный по горячей клавише.
        """
        try:
            context = self._resolve_comment_context(comment)
            if context is None:
                logger.info("Обработка комментария отменена пользователем при выборе задачи Jira")
                self.clipboard_service.restore_original_clipboard()
                return
            context["author"] = self.settings_repository.get_author()

            # Восстановить окно назначения непосредственно перед вставкой.
            self._restore_foreground_window_handle(self._target_window_handle)
            
            success = self.clipboard_service.process_captured_text(
                selected_text,
                comment,
                context,
            )
            
            if success:
                logger.info("Обработка текста завершена успешно")
            else:
                logger.warning("Не удалось обработать текст")
                
        except Exception as e:
            logger.error("Ошибка при обработке текста: %s", e, exc_info=True)
        finally:
            self.clipboard_service.restore_original_clipboard()
            self._target_window_handle = None

    @staticmethod
    def _capture_foreground_window_handle():
        """Получить хэндл активного окна (только Windows)."""
        if platform.system() != "Windows":
            return None
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            logger.info("Получен дескриптор активного окна: %s", hwnd)
            return hwnd if hwnd else None
        except Exception as exc:
            logger.debug("Не удалось получить дескриптор активного окна: %s", exc)
            return None

    @staticmethod
    def _restore_foreground_window_handle(hwnd) -> None:
        """Попытаться вернуть фокус в окно назначения (только Windows)."""
        if platform.system() != "Windows" or not hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            # Восстанавливаем окно только если оно свернуто.
            # Без этой проверки ShowWindow(..., SW_RESTORE) может снять максимизацию.
            is_iconic = bool(user32.IsIconic(hwnd))
            if is_iconic:
                user32.ShowWindow(hwnd, 9)
            set_result = bool(user32.SetForegroundWindow(hwnd))
            time.sleep(0.04)
            current = user32.GetForegroundWindow()
            logger.info(
                "Попытка восстановления фокуса: target=%s, was_minimized=%s, set_result=%s, current=%s",
                hwnd,
                is_iconic,
                set_result,
                current,
            )
        except Exception as exc:
            logger.debug("Не удалось восстановить фокус окна %s: %s", hwnd, exc)

    def _resolve_comment_context(self, comment) -> dict | None:
        """Подготовить контекст Jira для комментария с источником."""
        source_name = (getattr(comment, "source", "") or "").strip()
        if not source_name:
            return {}

        source = self.jira_sources_repository.get_by_name(source_name)
        if source is None:
            QMessageBox.warning(
                self.main_window,
                "Источник не найден",
                f"Для комментария указан несуществующий источник: {source_name}",
            )
            return {}

        dialog_state: dict[str, object] = {"dialog": None, "refreshed": False}

        def on_refresh_success() -> None:
            dialog_state["refreshed"] = True
            dialog = dialog_state.get("dialog")
            if dialog is None:
                return
            if not dialog.isVisible():
                return
            QTimer.singleShot(0, dialog.clear_cache_notice)

        try:
            issues, used_cache_after_timeout = self.jira_issues_service.get_issues_for_source(
                source,
                on_refresh_success=on_refresh_success,
            )
        except TimeoutError:
            return None
        except JiraIssuesError as exc:
            QMessageBox.warning(self.main_window, "Ошибка Jira", str(exc))
            return None
        except Exception as exc:
            QMessageBox.warning(self.main_window, "Ошибка Jira", str(exc))
            return None

        if not issues:
            QMessageBox.warning(
                self.main_window,
                "Jira",
                "По выбранному источнику не найдено задач.",
            )
            return None

        issues = self._promote_last_used_issue(source_name=source.name, issues=issues)

        if len(issues) == 1:
            selected = issues[0]
        else:
            cache_notice = ""
            if used_cache_after_timeout and not dialog_state["refreshed"]:
                cache_notice = (
                    "Данные получены из кэша из-за превышения таймаута. "
                    "Выполняется повторная попытка обновления источника."
                )
            dialog = IssueDialog(issues, parent=self.main_window, cache_notice=cache_notice)
            dialog_state["dialog"] = dialog
            if dialog.exec() != IssueDialog.Accepted:
                return None
            selected = dialog.get_selected_issue()
            if not selected:
                return None

        selected_key = selected.get("key", "")
        if selected_key:
            self.jira_last_issue_repository.set_last_issue_key(source.name, selected_key)

        return {
            "issue_key": selected_key,
            "issue_summary": selected.get("summary", ""),
        }

    def _promote_last_used_issue(self, source_name: str, issues: list[dict[str, str]]) -> list[dict[str, str]]:
        """Поднять последнюю использованную задачу источника на первую позицию."""
        if not issues:
            return issues

        last_key = self.jira_last_issue_repository.get_last_issue_key(source_name)
        if not last_key:
            return issues

        for index, issue in enumerate(issues):
            if (issue.get("key", "") or "").strip() != last_key:
                continue
            if index == 0:
                return issues
            return [issues[index], *issues[:index], *issues[index + 1 :]]
        return issues
    
    def run(self) -> int:
        """Запустить приложение.
        
        Returns:
            Код завершения.
        """
        logger.info("Запуск приложения")
        
        # Показываем окно, если не выбран запуск сразу в трей.
        if not (self.settings_repository.get_start_minimized() and self.main_window.is_tray_ready()):
            self.main_window.show()
        
        # Запуск цикла обработки событий Qt
        return self.qt_app.exec()

    def request_exit(self) -> None:
        """Запросить завершение приложения из UI."""
        self.main_window.request_exit()
        self.qt_app.quit()
    
    def cleanup(self) -> None:
        """Освободить ресурсы перед завершением."""
        logger.info("Очистка ресурсов")
        
        # Снимаем регистрацию горячих клавиш
        self.hotkey_manager.unregister_all()
        
        # Сохраняем пользовательские данные
        self.repository.save()
        try:
            self.settings_repository.save()
        except Exception:
            logger.exception("Не удалось сохранить настройки во время завершения")

        self._apply_file_logging(False)
        
        logger.info("Очистка завершена")


def main():
    """Точка запуска."""
    app = None
    
    try:
        app = Application()
        exit_code = app.run()
        
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
        exit_code = 0
        
    except Exception as e:
        logger.error("Критическая ошибка: %s", e, exc_info=True)
        exit_code = 1
        
    finally:
        if app:
            app.cleanup()
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
