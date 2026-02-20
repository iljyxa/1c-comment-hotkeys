"""Диалог выбора шаблона комментария."""

import logging
from typing import Optional, List

from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QLabel
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QCursor, QKeyEvent

from core.comments_repository import Comment

logger = logging.getLogger(__name__)


class CommentDialog(QDialog):
    """Диалог выбора шаблона комментария."""
    
    # Сигнал выбора комментария
    comment_selected = Signal(Comment)
    
    def __init__(self, comments: List[Comment], parent=None):
        """Инициализировать диалог.
        
        Args:
            comments: Список доступных комментариев.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        
        self.comments = comments
        self.filtered_comments = comments.copy()
        self.selected_comment: Optional[Comment] = None
        
        self._setup_ui()
        self._populate_list()
        
        # Модальный диалог поверх других окон
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Dialog
        )
    
    def _setup_ui(self) -> None:
        """Построить интерфейс диалога."""
        self.setWindowTitle("Выбор комментария")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        layout = QVBoxLayout()
        
        # Поле поиска
        search_layout = QHBoxLayout()
        search_label = QLabel("Поиск:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Введите текст для поиска...")
        self.search_input.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)
        
        # Список комментариев
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget)
        
        # Кнопки
        button_layout = QHBoxLayout()
        
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self._on_ok_clicked)
        self.ok_button.setDefault(True)
        
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
        # Сразу ставим фокус в список для навигации стрелками
        self.list_widget.setFocus()

    def showEvent(self, event) -> None:
        """Расположить окно рядом с курсором и усилить захват фокуса."""
        super().showEvent(event)
        self._position_near_cursor()
        QTimer.singleShot(0, self._activate_dialog_focus)

    def _position_near_cursor(self) -> None:
        """Сместить диалог к курсору с учетом границ экрана."""
        cursor_pos = QCursor.pos()
        app = QApplication.instance()
        if app is None:
            return
        screen = app.screenAt(cursor_pos) or app.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(cursor_pos)

        if frame.left() < available.left():
            frame.moveLeft(available.left())
        if frame.top() < available.top():
            frame.moveTop(available.top())
        if frame.right() > available.right():
            frame.moveRight(available.right())
        if frame.bottom() > available.bottom():
            frame.moveBottom(available.bottom())

        self.move(frame.topLeft())

    def _activate_dialog_focus(self) -> None:
        """Поднять окно и передать фокус списку комментариев."""
        self.raise_()
        self.activateWindow()
        if self.list_widget.count() > 0 and self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.setFocus(Qt.ActiveWindowFocusReason)
    
    def _populate_list(self, comments: Optional[List[Comment]] = None) -> None:
        """Заполнить список комментариями.
        
        Args:
            comments: Список для отображения. Если `None`, используется отфильтрованный список.
        """
        if comments is None:
            comments = self.filtered_comments
        
        self.list_widget.clear()
        
        for comment in comments:
            item = QListWidgetItem(comment.name)
            item.setData(Qt.UserRole, comment)
            self.list_widget.addItem(item)
        
        # Выбираем первый элемент, если список не пуст
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
    
    def _on_search_changed(self, text: str) -> None:
        """Обработать изменение текста поиска.
        
        Args:
            text: Строка поиска.
        """
        if not text:
            self.filtered_comments = self.comments.copy()
        else:
            text_lower = text.lower()
            self.filtered_comments = [
                c for c in self.comments
                if text_lower in c.name.lower() or text_lower in c.template.lower()
            ]
        
        self._populate_list()
    
    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Обработать двойной клик по элементу списка.
        
        Args:
            item: Выбранный элемент.
        """
        comment = item.data(Qt.UserRole)
        if comment:
            self.selected_comment = comment
            self.comment_selected.emit(comment)
            self.accept()
    
    def _on_ok_clicked(self) -> None:
        """Обработать нажатие кнопки OK."""
        current_item = self.list_widget.currentItem()
        if current_item:
            comment = current_item.data(Qt.UserRole)
            if comment:
                self.selected_comment = comment
                self.comment_selected.emit(comment)
                self.accept()
    
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Обработать нажатия клавиш.
        
        Args:
            event: Событие клавиатуры.
        """
        if event.key() == Qt.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            # Если фокус в поиске, переносим его в список
            if self.search_input.hasFocus() and self.list_widget.count() > 0:
                self.list_widget.setFocus()
                event.accept()
                return
            # Иначе подтверждаем текущий выбор
            self._on_ok_clicked()
        else:
            super().keyPressEvent(event)
    
    def get_selected_comment(self) -> Optional[Comment]:
        """Вернуть выбранный комментарий.
        
        Returns:
            Выбранный комментарий или `None`, если диалог отменен.
        """
        return self.selected_comment
