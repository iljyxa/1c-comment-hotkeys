"""Диалог ожидания ответа LLM с возможностью отмены."""

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class LlmProgressDialog(QDialog):
    """Небольшое окно поверх остальных на время запроса к LLM.

    Окно немодальное для кода (`show()`, без вложенного event loop), но
    application-modal для пользователя: пока идет запрос, взаимодействовать с
    другими окнами приложения нельзя. Закрытие любым способом (кнопка, Esc,
    крестик) считается отменой и эмитит `cancelled`.
    """

    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._started_at = time.monotonic()
        self._status = ""
        self._finished = False

        self.setWindowTitle("Запрос к LLM")
        self.setMinimumWidth(360)
        # Окно одноразовое: после закрытия (завершение или отмена) удаляется,
        # иначе каждый запрос оставлял бы скрытый виджет у главного окна.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Dialog)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.elapsed_label = QLabel()
        layout.addWidget(self.elapsed_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_elapsed)
        self._timer.start()
        self._update_elapsed()

    def set_status(self, text: str) -> None:
        """Обновить строку состояния (например, номер запроса и профиль)."""
        self._status = text
        self.status_label.setText(text)

    def finish(self) -> None:
        """Закрыть окно по завершении запроса, не считая это отменой."""
        self._finished = True
        self._timer.stop()
        self.close()

    def _update_elapsed(self) -> None:
        elapsed = int(time.monotonic() - self._started_at)
        self.elapsed_label.setText(f"Прошло: {elapsed} с")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_near_cursor()
        self.raise_()
        self.activateWindow()

    def reject(self) -> None:
        # `done()` внутри учитывает WA_DeleteOnClose, отдельный close() не нужен.
        if not self._finished:
            self._finished = True
            self._timer.stop()
            self.cancelled.emit()
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._finished:
            self._finished = True
            self._timer.stop()
            self.cancelled.emit()
        super().closeEvent(event)

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
