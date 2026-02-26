"""Главное окно приложения и окно настроек."""

import logging
import os
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QTextEdit, QComboBox,
    QLabel, QSystemTrayIcon, QMenu, QApplication, QStyle,
    QGroupBox, QCheckBox, QHeaderView
)
from PySide6.QtCore import Qt, Signal, QEvent, QUrl
from PySide6.QtGui import QAction, QIcon, QKeyEvent, QDesktopServices

from core.comments_repository import Comment, CommentsRepository
from core.jira_sources_repository import JiraSource, JiraSourcesRepository
from core.settings_repository import SettingsRepository

logger = logging.getLogger(__name__)

_VK_TO_TOKEN = {
    0xBB: "=",
    0xBD: "-",
    0xBE: ".",
    0xBC: ",",
    0xBF: "/",
    0xDC: "\\",
    0xBA: ";",
    0xDE: "'",
    0xDB: "[",
    0xDD: "]",
    0xC0: "`",
}


def _token_from_native_vk(vk: int) -> str:
    """Преобразовать nativeVirtualKey в стабильный токен для хоткея."""
    if not vk:
        return ""
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    return _VK_TO_TOKEN.get(vk, "")


def _qt_event_to_hotkey(event: QKeyEvent) -> str:
    """Преобразовать событие клавиатуры Qt в строку комбинации."""
    key = event.key()
    if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
        return ""

    modifiers = []
    mods = event.modifiers()
    if mods & Qt.ControlModifier:
        modifiers.append("ctrl")
    if mods & Qt.AltModifier:
        modifiers.append("alt")
    if mods & Qt.ShiftModifier:
        modifiers.append("shift")
    if mods & Qt.MetaModifier:
        modifiers.append("meta")

    key_text = _token_from_native_vk(event.nativeVirtualKey())
    if not key_text:
        key_text = event.text().lower().strip()
    if not key_text:
        special = {
            Qt.Key_Space: "space",
            Qt.Key_Tab: "tab",
            Qt.Key_Return: "enter",
            Qt.Key_Enter: "enter",
            Qt.Key_Escape: "esc",
            Qt.Key_Backspace: "backspace",
            Qt.Key_Delete: "delete",
            Qt.Key_Up: "up",
            Qt.Key_Down: "down",
            Qt.Key_Left: "left",
            Qt.Key_Right: "right",
        }
        if Qt.Key_F1 <= key <= Qt.Key_F35:
            key_text = f"f{key - Qt.Key_F1 + 1}"
        else:
            key_text = special.get(key, "")

    if not key_text:
        return ""

    return "+".join(modifiers + [key_text])


class CommentEditDialog(QDialog):
    """Диалог создания/редактирования шаблона комментария."""
    
    def __init__(
        self,
        source_names: list[str],
        comment: Optional[Comment] = None,
        parent=None,
    ):
        """Инициализировать диалог.
        
        Args:
            comment: Редактируемый комментарий, либо `None` для нового.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        
        self.comment = comment
        self.is_new = comment is None
        self.source_names = source_names
        self._capturing_hotkey = False
        
        self._setup_ui()
        
        if comment:
            self._load_comment(comment)
    
    def _setup_ui(self) -> None:
        """Построить интерфейс диалога."""
        title = "Новый комментарий" if self.is_new else "Редактирование комментария"
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        
        layout = QFormLayout()
        
        # Поле названия
        self.name_input = QLineEdit()
        layout.addRow("Название:", self.name_input)
        
        # Поле шаблона
        self.template_input = QTextEdit()
        self.template_input.setMinimumHeight(150)
        layout.addRow("Шаблон:", self.template_input)

        # Необязательная быстрая клавиша
        hotkey_row = QHBoxLayout()
        self.hotkey_input = QLineEdit()
        self.hotkey_input.setPlaceholderText("Например: ctrl+alt+1")
        self.assign_hotkey_button = QPushButton("Захватить")
        self.assign_hotkey_button.clicked.connect(self._on_assign_hotkey_clicked)
        hotkey_row.addWidget(self.hotkey_input)
        hotkey_row.addWidget(self.assign_hotkey_button)
        layout.addRow("Быстрая клавиша:", hotkey_row)

        # Необязательный источник Jira
        self.source_combo = QComboBox()
        self.source_combo.addItem("")
        self.source_combo.addItems(self.source_names)
        layout.addRow("Источник:", self.source_combo)

        # Подсказка по макросам
        macros_label = QLabel(
            "Доступные макросы: {text}, {date}, {time}, {datetime}, "
            "{issue_key}, {issue_summary}, {author}"
        )
        macros_label.setWordWrap(True)
        macros_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addRow(macros_label)
        
        # Кнопки
        button_layout = QHBoxLayout()
        
        self.save_button = QPushButton("ОК")
        self.save_button.clicked.connect(self.accept)
        
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        
        layout.addRow(button_layout)
        
        self.setLayout(layout)

    def _on_assign_hotkey_clicked(self) -> None:
        """Переключить режим захвата быстрой клавиши комментария."""
        if self._capturing_hotkey:
            self._stop_hotkey_capture()
            return
        self._start_hotkey_capture()

    def _start_hotkey_capture(self) -> None:
        """Запустить захват комбинации клавиш."""
        self._capturing_hotkey = True
        self.assign_hotkey_button.setText("Сохранить")
        self.hotkey_input.setReadOnly(True)
        self.hotkey_input.setFocus()
        QApplication.instance().installEventFilter(self)

    def _stop_hotkey_capture(self) -> None:
        """Остановить захват комбинации клавиш."""
        if not self._capturing_hotkey:
            return
        self._capturing_hotkey = False
        self.assign_hotkey_button.setText("Захватить")
        self.hotkey_input.setReadOnly(False)
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        """Перехватывать нажатия клавиш в режиме назначения комбинации."""
        if (
            self._capturing_hotkey
            and event.type() == QEvent.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            combo = self._event_to_hotkey(event)
            if combo:
                self.hotkey_input.setText(combo)
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _event_to_hotkey(event: QKeyEvent) -> str:
        """Преобразовать событие клавиатуры Qt в строку комбинации."""
        return _qt_event_to_hotkey(event)

    def closeEvent(self, event) -> None:
        """Остановить захват перед закрытием диалога."""
        self._stop_hotkey_capture()
        super().closeEvent(event)
    
    def _load_comment(self, comment: Comment) -> None:
        """Загрузить данные комментария в форму.
        
        Args:
            comment: Загружаемый комментарий.
        """
        self.name_input.setText(comment.name)
        self.template_input.setPlainText(comment.template)
        self.hotkey_input.setText(comment.hotkey)
        index = self.source_combo.findText(comment.source)
        if index >= 0:
            self.source_combo.setCurrentIndex(index)
    
    def get_comment(self) -> Comment:
        """Считать комментарий из формы.
        
        Returns:
            Объект комментария.
        """
        return Comment(
            name=self.name_input.text().strip(),
            template=self.template_input.toPlainText(),
            hotkey=self.hotkey_input.text().strip(),
            source=self.source_combo.currentText().strip(),
        )


class JiraSourcesDialog(QDialog):
    """Диалог редактирования источников Jira в таблице."""

    def __init__(self, sources: list[JiraSource], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Источники Jira")
        self.setMinimumWidth(900)
        self.setMinimumHeight(420)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Название", "Ссылка", "Токен"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.AllEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table)

        button_layout = QHBoxLayout()
        self.add_button = QPushButton("Добавить")
        self.add_button.clicked.connect(self._on_add_clicked)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.delete_button)
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self._load_sources(sources)

    def _load_sources(self, sources: list[JiraSource]) -> None:
        self.table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            self.table.setItem(row, 0, QTableWidgetItem(source.name))
            self.table.setItem(row, 1, QTableWidgetItem(source.url))
            self.table.setItem(row, 2, QTableWidgetItem(source.token))

    def _on_add_clicked(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(""))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def _on_delete_clicked(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def get_sources(self) -> list[JiraSource]:
        """Считать и провалидировать данные таблицы источников."""
        sources: list[JiraSource] = []
        names_seen: set[str] = set()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            url_item = self.table.item(row, 1)
            token_item = self.table.item(row, 2)

            name = (name_item.text() if name_item else "").strip()
            url = (url_item.text() if url_item else "").strip()
            token = (token_item.text() if token_item else "").strip()

            if not any([name, url, token]):
                continue
            if not name or not url or not token:
                raise ValueError(f"Строка {row + 1}: заполните название, ссылку и токен")
            if name in names_seen:
                raise ValueError(f"Дубликат названия источника: {name}")
            names_seen.add(name)
            sources.append(JiraSource(name=name, url=url, token=token))
        return sources

class MainWindow(QMainWindow):
    """Главное окно приложения."""
    
    # Сигнал показа диалога выбора комментария
    show_comment_dialog_signal = Signal()
    
    def __init__(
        self,
        repository: CommentsRepository,
        settings_repository: SettingsRepository,
        jira_sources_repository: JiraSourcesRepository,
        hotkey_change_handler: Callable[[str], tuple[bool, str]],
        log_to_file_change_handler: Callable[[bool], None],
        exit_handler: Callable[[], None],
    ):
        """Инициализировать главное окно.
        
        Args:
            repository: Репозиторий комментариев.
        """
        super().__init__()
        
        self.repository = repository
        self.settings_repository = settings_repository
        self.jira_sources_repository = jira_sources_repository
        self.hotkey_change_handler = hotkey_change_handler
        self.log_to_file_change_handler = log_to_file_change_handler
        self.exit_handler = exit_handler
        self._allow_close = False
        self._capturing_hotkey = False
        
        self._setup_ui()
        self._setup_tray_icon()
        self._load_comments()
        self._load_settings()
    
    def _setup_ui(self) -> None:
        """Построить интерфейс главного окна."""
        self.setWindowTitle("1C Comment Hotkeys")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)
        
        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        settings_box = QGroupBox("Настройки приложения")
        settings_layout = QFormLayout()

        hotkey_row = QHBoxLayout()
        self.hotkey_input = QLineEdit()
        self.hotkey_input.setPlaceholderText("ctrl+=")
        self.assign_hotkey_button = QPushButton("Захватить")
        self.assign_hotkey_button.clicked.connect(self._on_assign_hotkey_clicked)
        hotkey_row.addWidget(self.hotkey_input)
        hotkey_row.addWidget(self.assign_hotkey_button)
        settings_layout.addRow("Глобальная горячая клавиша:", hotkey_row)

        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("AUTHOR")
        settings_layout.addRow("Автор:", self.author_input)

        self.start_minimized_checkbox = QCheckBox("Запускать в системном трее")
        self.log_to_file_checkbox = QCheckBox("Лог")
        self.log_to_file_checkbox.toggled.connect(self._on_log_to_file_toggled)
        checkboxes_row = QHBoxLayout()
        checkboxes_row.addWidget(self.start_minimized_checkbox)
        checkboxes_row.addWidget(self.log_to_file_checkbox)
        checkboxes_row.addStretch()
        settings_layout.addRow("", checkboxes_row)

        settings_box.setLayout(settings_layout)
        layout.addWidget(settings_box)
        
        # Таблица комментариев
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Шаблон", "Быстрая клавиша", "Источник"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.cellDoubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table)
        
        # Кнопки действий
        button_layout = QHBoxLayout()
        
        self.add_button = QPushButton("Добавить")
        self.add_button.clicked.connect(self._on_add_clicked)

        self.copy_button = QPushButton("Копировать")
        self.copy_button.clicked.connect(self._on_copy_clicked)
        
        self.edit_button = QPushButton("Редактировать")
        self.edit_button.clicked.connect(self._on_edit_clicked)
        
        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self._on_delete_clicked)

        self.open_config_button = QPushButton("Каталог настроек")
        self.open_config_button.clicked.connect(self._on_open_config_dir_clicked)

        self.sources_button = QPushButton("Источники")
        self.sources_button.clicked.connect(self._on_sources_clicked)
        
        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save_clicked)
        
        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addWidget(self.edit_button)
        button_layout.addWidget(self.delete_button)
        button_layout.addWidget(self.sources_button)
        button_layout.addWidget(self.open_config_button)
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        
        layout.addLayout(button_layout)

    def _setup_tray_icon(self) -> None:
        """Настроить иконку в системном трее."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error("Системный трей недоступен в этой системе")
            return

        self.tray_icon = QSystemTrayIcon(self)
        icon = self.windowIcon()
        if icon.isNull():
            app_icon = QApplication.instance().windowIcon()
            if isinstance(app_icon, QIcon):
                icon = app_icon
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        if icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)
        
        # Контекстное меню трея
        tray_menu = QMenu()
        
        show_action = QAction("Открыть", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)
        
        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self.exit_handler)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        
        self.tray_icon.show()
        if not self.tray_icon.isVisible():
            logger.error("Не удалось отобразить иконку в системном трее")
    
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Обработать активацию иконки в трее.
        
        Args:
            reason: Причина активации.
        """
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def show_window(self) -> None:
        """Показать и активировать главное окно."""
        self.show()
        self.raise_()
        self.activateWindow()
    
    def _load_comments(self) -> None:
        """Загрузить комментарии в таблицу."""
        comments = self.repository.get_all()
        
        self.table.setRowCount(len(comments))
        
        for i, comment in enumerate(comments):
            self.table.setItem(i, 0, QTableWidgetItem(comment.name))
            # Сокращаем длинный шаблон для компактного отображения
            template_preview = comment.template[:50] + "..." if len(comment.template) > 50 else comment.template
            self.table.setItem(i, 1, QTableWidgetItem(template_preview))
            self.table.setItem(i, 2, QTableWidgetItem(comment.hotkey))
            self.table.setItem(i, 3, QTableWidgetItem(comment.source))

    def _get_source_names(self) -> list[str]:
        """Вернуть список имен доступных Jira-источников."""
        return [source.name for source in self.jira_sources_repository.get_all()]
    
    def _on_add_clicked(self) -> None:
        """Обработать нажатие кнопки «Добавить»."""
        dialog = CommentEditDialog(source_names=self._get_source_names(), parent=self)
        
        if dialog.exec() == QDialog.Accepted:
            try:
                comment = dialog.get_comment()
                self.repository.add(comment)
                self._load_comments()
                logger.info("Добавлен комментарий: %s", comment.name)
            except ValueError as e:
                QMessageBox.warning(self, "Ошибка", str(e))

    def _on_copy_clicked(self) -> None:
        """Обработать нажатие кнопки «Копировать»."""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "Информация", "Выберите комментарий для копирования")
            return

        original = self.repository.get_by_index(current_row)
        if original is None:
            QMessageBox.warning(self, "Ошибка", "Не удалось получить выбранный комментарий")
            return

        prefilled = Comment(
            name=original.name,
            template=original.template,
            hotkey=original.hotkey,
            source=original.source,
        )
        dialog = CommentEditDialog(
            source_names=self._get_source_names(),
            comment=prefilled,
            parent=self,
        )

        if dialog.exec() == QDialog.Accepted:
            try:
                comment = dialog.get_comment()
                self.repository.add(comment)
                self._load_comments()
                logger.info("Скопирован шаблон комментария: %s", comment.name)
            except ValueError as e:
                QMessageBox.warning(self, "Ошибка", str(e))
    
    def _on_edit_clicked(self) -> None:
        """Обработать нажатие кнопки «Редактировать»."""
        current_row = self.table.currentRow()
        
        if current_row < 0:
            QMessageBox.information(self, "Информация", "Выберите комментарий для редактирования")
            return
        
        comment = self.repository.get_by_index(current_row)
        
        if comment:
            dialog = CommentEditDialog(
                source_names=self._get_source_names(),
                comment=comment,
                parent=self,
            )
            
            if dialog.exec() == QDialog.Accepted:
                try:
                    updated_comment = dialog.get_comment()
                    self.repository.update(current_row, updated_comment)
                    self._load_comments()
                    logger.info("Обновлен комментарий: %s", updated_comment.name)
                except ValueError as e:
                    QMessageBox.warning(self, "Ошибка", str(e))

    def _on_sources_clicked(self) -> None:
        """Открыть диалог редактирования Jira-источников."""
        dialog = JiraSourcesDialog(self.jira_sources_repository.get_all(), parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        try:
            sources = dialog.get_sources()
            self.jira_sources_repository.set_all(sources)
            self.jira_sources_repository.save()

            valid_sources = set(self._get_source_names())
            changed = False
            for idx, comment in enumerate(self.repository.get_all()):
                if comment.source and comment.source not in valid_sources:
                    comment.source = ""
                    self.repository.update(idx, comment)
                    changed = True
            if changed:
                self._load_comments()
            logger.info("Обновлены источники Jira: %d", len(sources))
        except ValueError as exc:
            QMessageBox.warning(self, "Ошибка", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить источники: {exc}")
            logger.error("Не удалось сохранить источники Jira: %s", exc)

    def _on_table_double_clicked(self, row: int, _column: int) -> None:
        """Открыть редактирование по двойному клику строки."""
        self.table.selectRow(row)
        self._on_edit_clicked()
    
    def _on_delete_clicked(self) -> None:
        """Обработать нажатие кнопки «Удалить»."""
        current_row = self.table.currentRow()
        
        if current_row < 0:
            QMessageBox.information(self, "Информация", "Выберите комментарий для удаления")
            return
        
        comment_name = self.table.item(current_row, 0).text()
        
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            f"Удалить комментарий '{comment_name}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.repository.delete(current_row)
            self._load_comments()
            logger.info("Удален комментарий: %s", comment_name)

    def _on_open_config_dir_clicked(self) -> None:
        """Открыть каталог конфигурации в файловом менеджере."""
        config_dir = self.settings_repository.get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":
            try:
                os.startfile(str(config_dir))
                opened = True
            except OSError:
                opened = False
        else:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir)))

        if not opened:
            QMessageBox.warning(
                self,
                "Ошибка",
                f"Не удалось открыть каталог: {config_dir}",
            )

    def _on_save_clicked(self) -> None:
        """Обработать нажатие кнопки «Сохранить»."""
        try:
            self._stop_hotkey_capture()
            hotkey = self.hotkey_input.text().strip()
            success, message = self.hotkey_change_handler(hotkey)
            if not success:
                raise ValueError(message)

            self.settings_repository.set_hotkey(hotkey)
            self.settings_repository.set_start_minimized(
                self.start_minimized_checkbox.isChecked()
            )
            self.settings_repository.set_log_to_file(
                self.log_to_file_checkbox.isChecked()
            )
            self.settings_repository.set_author(self.author_input.text())
            self.settings_repository.save()
            self.repository.save()
            QMessageBox.information(self, "Успех", "Настройки и комментарии сохранены")
            logger.info("Настройки и комментарии сохранены")
        except ValueError as e:
            QMessageBox.warning(self, "Ошибка", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {e}")
            logger.error("Не удалось сохранить комментарии: %s", e)

    def _load_settings(self) -> None:
        """Загрузить настройки в форму."""
        self.hotkey_input.setText(self.settings_repository.get_hotkey())
        self.start_minimized_checkbox.setChecked(
            self.settings_repository.get_start_minimized()
        )
        self.log_to_file_checkbox.setChecked(
            self.settings_repository.get_log_to_file()
        )
        self.author_input.setText(self.settings_repository.get_author())

    def _on_log_to_file_toggled(self, checked: bool) -> None:
        """Включить/выключить запись логов в файл сразу при переключении флага."""
        self.log_to_file_change_handler(bool(checked))

    def _on_assign_hotkey_clicked(self) -> None:
        """Переключить режим захвата горячей клавиши."""
        if self._capturing_hotkey:
            self._stop_hotkey_capture()
            return

        self._start_hotkey_capture()

    def _start_hotkey_capture(self) -> None:
        """Запустить захват комбинации клавиш."""
        self._capturing_hotkey = True
        self.assign_hotkey_button.setText("Сохранить")
        self.hotkey_input.setReadOnly(True)
        self.hotkey_input.setFocus()
        QApplication.instance().installEventFilter(self)

    def _stop_hotkey_capture(self) -> None:
        """Остановить захват комбинации клавиш."""
        if not self._capturing_hotkey:
            return
        self._capturing_hotkey = False
        self.assign_hotkey_button.setText("Захватить")
        self.hotkey_input.setReadOnly(False)
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        """Перехватывать нажатия клавиш в режиме назначения комбинации."""
        if (
            self._capturing_hotkey
            and event.type() == QEvent.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            combo = self._event_to_hotkey(event)
            if combo:
                self.hotkey_input.setText(combo)
            return True
        return super().eventFilter(watched, event)

    def _event_to_hotkey(self, event: QKeyEvent) -> str:
        """Преобразовать событие клавиатуры Qt в строку комбинации."""
        return _qt_event_to_hotkey(event)

    def is_tray_ready(self) -> bool:
        """Вернуть `True`, если иконка в трее доступна и видима."""
        return hasattr(self, "tray_icon") and self.tray_icon.isVisible()

    def request_exit(self) -> None:
        """Разрешить закрытие окна и завершить приложение."""
        self._stop_hotkey_capture()
        self._allow_close = True
        self.close()
    
    def closeEvent(self, event) -> None:
        """Обработать закрытие окна.
        
        Args:
            event: Событие закрытия.
        """
        if self._allow_close:
            self._stop_hotkey_capture()
            event.accept()
            return

        if not hasattr(self, "tray_icon") or not self.tray_icon.isVisible():
            self._stop_hotkey_capture()
            event.accept()
            return

        # Вместо закрытия прячем окно в трей
        self._stop_hotkey_capture()
        event.ignore()
        self.hide()
        
        # Одноразовое уведомление при первом скрытии
        if not hasattr(self, '_first_hide_shown'):
            self.tray_icon.showMessage(
                "1C Comment Hotkeys",
                "Программа продолжает работать в фоне",
                QSystemTrayIcon.Information,
                2000
            )
            self._first_hide_shown = True
