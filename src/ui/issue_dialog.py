"""Диалог выбора задачи Jira."""

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class IssueDialog(QDialog):
    """Диалог выбора задачи Jira из списка."""

    def __init__(
        self,
        issues: List[Dict[str, str]],
        parent=None,
        cache_notice: Optional[str] = None,
    ):
        super().__init__(parent)
        self.issues = issues
        self.filtered = issues.copy()
        self.selected_issue: Optional[Dict[str, str]] = None
        self._cache_notice_text = cache_notice or ""
        self._setup_ui()
        self._populate()

    def showEvent(self, event) -> None:
        """Расположить окно рядом с курсором при показе."""
        super().showEvent(event)
        self._position_near_cursor()
        self._activate_focus()

    def _activate_focus(self) -> None:
        """Передать фокус списку задач для навигации клавиатурой."""
        self.raise_()
        self.activateWindow()
        if self.list_widget.count() > 0 and self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.setFocus(Qt.ActiveWindowFocusReason)

    def _position_near_cursor(self) -> None:
        """Сместить диалог к курсору с учетом доступной геометрии экрана."""
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

    def _setup_ui(self) -> None:
        self.setWindowTitle("Выбор задачи Jira")
        self.setMinimumWidth(700)
        self.setMinimumHeight(420)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Dialog)

        layout = QVBoxLayout()
        self.setLayout(layout)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Поиск:"))
        self.search = QLineEdit()
        self.search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self.list_widget)

        self.cache_notice = QLabel()
        self.cache_notice.setWordWrap(True)
        self.cache_notice.setVisible(False)
        layout.addWidget(self.cache_notice)

        buttons = QHBoxLayout()
        ok = QPushButton("OK")
        ok.clicked.connect(self._on_ok_clicked)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self.list_widget.setFocus()
        if self._cache_notice_text:
            self.show_cache_notice(self._cache_notice_text)

    def _populate(self) -> None:
        self.list_widget.clear()
        for issue in self.filtered:
            issue_key = issue.get("key") or issue.get("issue_key") or ""
            title = f"{issue_key} - {issue.get('summary', '')}".strip()
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, issue)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_search_changed(self, text: str) -> None:
        query = text.lower().strip()
        if not query:
            self.filtered = self.issues.copy()
        else:
            self.filtered = [
                issue
                for issue in self.issues
                if query in str(issue.get("key") or issue.get("issue_key") or "").lower()
                or query in issue.get("summary", "").lower()
            ]
        self._populate()

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        issue = item.data(Qt.UserRole)
        if issue:
            self.selected_issue = issue
            self.accept()

    def _on_ok_clicked(self) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            return
        issue = current.data(Qt.UserRole)
        if issue:
            self.selected_issue = issue
            self.accept()

    def get_selected_issue(self) -> Optional[Dict[str, str]]:
        return self.selected_issue

    def show_cache_notice(self, text: str) -> None:
        self.cache_notice.setText(text)
        self.cache_notice.setVisible(True)

    def clear_cache_notice(self) -> None:
        self.cache_notice.clear()
        self.cache_notice.setVisible(False)
