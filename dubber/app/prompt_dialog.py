"""Edit one chatbot prompt (⚙ Cài đặt → Chatbot): save it, or put the default back."""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from dubber.core.translate import prompts

TITLES = {
    "review": "Prompt sửa bản dịch",
    "tiktok": "Prompt TikTok (tiêu đề, mô tả, hashtag)",
}
HELP = {
    "review": ("Đặt ở đầu khi bấm <b>📋 Copy cho AI</b> trong cửa sổ <b>Duyệt bản "
               "dịch</b>. Sau đoạn này app tự thêm các câu dạng "
               "<code>12. [1,5s] 中文 → tiếng Việt</code>.<br>Giữ yêu cầu chatbot trả lời dạng "
               "<code>[12] câu tiếng Việt</code> để <b>Dán kết quả</b> đọc được."),
    "tiktok": ("Ghi vào file <b>&lt;tên video&gt;_vi.txt</b> cạnh video xuất ra và được chép khi "
               "bấm <b>📋 TikTok</b> trên thẻ video.<br>Dòng <code>"
               + prompts.SCRIPT_PLACEHOLDER.replace("<", "&lt;").replace(">", "&gt;")
               + "</code> là chỗ app chèn lời thoại; nếu xóa dòng này, lời thoại được thêm ở cuối."),
}


class PromptDialog(QDialog):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setWindowTitle(TITLES[kind])
        self.resize(760, 620)
        lay = QVBoxLayout(self)

        info = QLabel(HELP[kind], self)
        info.setWordWrap(True)
        lay.addWidget(info)
        self._state = QLabel("", self)
        lay.addWidget(self._state)

        self.edit = QPlainTextEdit(self)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)   # Menlo / Consolas
        mono.setPointSize(12)
        self.edit.setFont(mono)
        self.edit.setPlainText(prompts.get_prompt(kind))
        self.edit.textChanged.connect(self._refresh)
        lay.addWidget(self.edit, 1)

        btns = QHBoxLayout()
        self.btn_default = QPushButton("↺ Khôi phục mặc định", self)
        self.btn_default.setToolTip("Đưa nội dung về bản mặc định (bấm Lưu để áp dụng)")
        self.btn_default.clicked.connect(self._to_default)
        btns.addWidget(self.btn_default)
        btns.addStretch(1)
        cancel = QPushButton("Hủy", self)
        cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Lưu", self)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(self.btn_save)
        lay.addLayout(btns)
        self._refresh()

    def _is_default(self) -> bool:
        return self.edit.toPlainText().strip() == prompts.default_prompt(self._kind).strip()

    def _refresh(self):
        if self._is_default():
            self._state.setText("Đang là <b>mặc định</b>.")
            self._state.setStyleSheet("color: #888;")
        else:
            self._state.setText("<b>Đã tùy chỉnh</b> — khác mặc định.")
            self._state.setStyleSheet("color: #ffb74d;")
        self.btn_default.setEnabled(not self._is_default())

    def _to_default(self):
        if QMessageBox.question(self, "Khôi phục mặc định",
                                "Thay nội dung hiện tại bằng prompt mặc định?") \
                != QMessageBox.Yes:
            return
        self.edit.setPlainText(prompts.default_prompt(self._kind))

    def _save(self):
        prompts.save_prompt(self._kind, self.edit.toPlainText())
        self.accept()


def edit_prompt(kind: str, parent=None) -> bool:
    """Open the editor; True when the user saved."""
    return PromptDialog(kind, parent).exec() == QDialog.Accepted
