"""Light and dark flat themes (Qt style sheet built from color tokens)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette, QColor

LIGHT = {
    "bg": "#f4f5f7", "card": "#ffffff", "border": "#e2e5ea", "text": "#1c2230", "muted": "#687082",
    "input": "#ffffff", "input_border": "#cfd4dc", "accent": "#2563eb", "accent_hover": "#1d4ed8",
    "accent_text": "#ffffff", "hover": "#eef1f5", "review": "#f59e0b", "review_bg": "#fff8eb",
    "ok": "#15803d", "ok_bg": "#e8f6ed", "warn_bg": "#fff4de", "warn": "#9a5b00", "drop_bg": "#f8faff",
    "regex": "#15803d", "ai": "#7c3aed", "default": "#6b7280", "derived": "#0369a1", "you": "#1c2230",
    "sel": "#dbe7ff",
}
DARK = {
    "bg": "#15171c", "card": "#1e2128", "border": "#2c313a", "text": "#e7e9ee", "muted": "#9aa2b1",
    "input": "#171a20", "input_border": "#3a404b", "accent": "#3b82f6", "accent_hover": "#2563eb",
    "accent_text": "#ffffff", "hover": "#262a33", "review": "#f5a524", "review_bg": "#2b2416",
    "ok": "#4ade80", "ok_bg": "#15291d", "warn_bg": "#2e2513", "warn": "#f5c56b", "drop_bg": "#1a1e27",
    "regex": "#4ade80", "ai": "#c4a5ff", "default": "#9aa2b1", "derived": "#7dd3fc", "you": "#e7e9ee",
    "sel": "#23324d",
}

QSS = """
* { font-family: "Segoe UI", "Segoe UI Variable", sans-serif; font-size: 10pt; color: {text}; }
QMainWindow, QDialog { background: {bg}; }
QWidget#central, QScrollArea, QScrollArea > QWidget > QWidget { background: {bg}; }
QFrame#card { background: {card}; border: 1px solid {border}; border-radius: 10px; }
QLabel#title { font-size: 16pt; font-weight: 600; }
QLabel#subtitle, QLabel#muted { color: {muted}; }
QLabel#section { font-size: 11pt; font-weight: 600; }
QLabel#fieldLabel { color: {muted}; }

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
  background: {input}; border: 1px solid {input_border}; border-radius: 6px; padding: 5px 8px;
  selection-background-color: {accent}; selection-color: {accent_text};
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid {accent}; }
QLineEdit[review="true"], QPlainTextEdit[review="true"] { border: 1px solid {review}; background: {review_bg}; }
QLineEdit[missing="true"], QPlainTextEdit[missing="true"] { border: 1px dashed {review}; }
QComboBox::drop-down { border: none; width: 22px; }
QSpinBox { padding-right: 4px; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: none; }
QComboBox QAbstractItemView { background: {card}; border: 1px solid {border}; selection-background-color: {sel}; }

QPushButton {
  background: {card}; border: 1px solid {input_border}; border-radius: 6px; padding: 6px 14px;
}
QPushButton:hover { background: {hover}; }
QPushButton:disabled { color: {muted}; }
QPushButton#primary {
  background: {accent}; color: {accent_text}; border: none; font-weight: 600; padding: 9px 22px; font-size: 11pt;
}
QPushButton#primary:hover { background: {accent_hover}; }
QPushButton#primary:disabled { background: {border}; color: {muted}; }
QToolButton { border: 1px solid transparent; border-radius: 6px; padding: 3px 6px; background: transparent; }
QToolButton:hover { background: {hover}; border: 1px solid {border}; }
QToolButton::menu-indicator { image: none; }

QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid {input_border}; border-radius: 4px; background: {input}; }
QCheckBox::indicator:checked { background: {accent}; border: 1px solid {accent}; image: url(CHECK_ICON); }
QCheckBox::indicator:hover { border: 1px solid {accent}; }

QFrame#drop {
  background: {drop_bg}; border: 2px dashed {input_border}; border-radius: 12px;
}
QFrame#drop[hover="true"] { border: 2px dashed {accent}; background: {sel}; }
QLabel#dropIcon { font-size: 30pt; color: {accent}; }
QLabel#dropText { font-size: 11pt; font-weight: 600; }

QLabel#status { border-radius: 12px; padding: 4px 12px; font-weight: 600; background: {hover}; color: {muted}; }
QLabel#status[state="ok"] { background: {ok_bg}; color: {ok}; }
QLabel#status[state="warn"] { background: {warn_bg}; color: {warn}; }
QLabel#status[state="busy"] { background: {sel}; color: {accent}; }

QLabel#badge { border-radius: 8px; padding: 1px 7px; font-size: 8pt; font-weight: 600; border: 1px solid {border}; }
QLabel#badge[src="regex"] { color: {regex}; }
QLabel#badge[src="AI"] { color: {ai}; }
QLabel#badge[src="default"] { color: {default}; }
QLabel#badge[src="derived"] { color: {derived}; }
QLabel#badge[src="you"] { color: {you}; }
QLabel#badge[src=""] { color: transparent; border: 1px solid transparent; }

QListWidget, QTableWidget {
  background: {card}; border: 1px solid {border}; border-radius: 8px; gridline-color: {border};
  alternate-background-color: {bg};
}
QListWidget::item { padding: 5px; }
QListWidget::item:selected, QTableWidget::item:selected { background: {sel}; color: {text}; }
QHeaderView::section { background: {bg}; border: none; border-bottom: 1px solid {border}; padding: 5px; color: {muted}; font-weight: 600; }
QTabWidget::pane { border: 1px solid {border}; border-radius: 8px; background: {card}; top: -1px; }
QTabBar::tab { padding: 7px 16px; border: none; color: {muted}; }
QTabBar::tab:selected { color: {accent}; border-bottom: 2px solid {accent}; }
QProgressBar { border: none; background: {hover}; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: {accent}; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: {input_border}; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenuBar { background: {bg}; padding: 2px 10px; }
QMenuBar::item { padding: 4px 10px; border-radius: 4px; background: transparent; }
QMenuBar::item:selected { background: {hover}; }
QMenu { background: {card}; border: 1px solid {border}; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: {sel}; }
QToolTip { background: {card}; color: {text}; border: 1px solid {border}; padding: 4px; }
"""

_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
              'fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _check_icon_path() -> str:
    from ..settings import settings_dir
    p = settings_dir() / "check.svg"
    if not p.exists():
        p.write_text(_CHECK_SVG, encoding="utf-8")
    return p.as_posix()


def is_dark(theme: str) -> bool:
    if theme in ("light", "dark"):
        return theme == "dark"
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply_theme(app, theme: str) -> dict:
    tokens = DARK if is_dark(theme) else LIGHT
    qss = QSS
    for k, v in tokens.items():
        qss = qss.replace("{" + k + "}", v)
    qss = qss.replace("CHECK_ICON", _check_icon_path())
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(tokens["bg"]))
    pal.setColor(QPalette.Base, QColor(tokens["input"]))
    pal.setColor(QPalette.Text, QColor(tokens["text"]))
    pal.setColor(QPalette.WindowText, QColor(tokens["text"]))
    pal.setColor(QPalette.Button, QColor(tokens["card"]))
    pal.setColor(QPalette.ButtonText, QColor(tokens["text"]))
    pal.setColor(QPalette.Highlight, QColor(tokens["accent"]))
    pal.setColor(QPalette.PlaceholderText, QColor(tokens["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(qss)
    return tokens
