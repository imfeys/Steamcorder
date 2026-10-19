"""Steamcorder: automatically post new Steam screenshots to a Discord webhook."""

import getpass
import json
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from PyQt6.QtCore import QObject, QPointF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSlider, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

APP_NAME = "Steamcorder"
APP_VERSION = "1.0.0"
DISCORD_INVITE = "https://discord.gg/dMvCH93sYX"
IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
WEBHOOK_PATTERN = re.compile(
    r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+/?(?:\?.*)?$"
)
MAX_DELAY_SECONDS = 30
MAX_LOG_ENTRIES = 200


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
def resource_path(name):
    """Absolute path to a bundled resource, both from source and inside the PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def data_dir():
    """Per-user folder for settings. Must not live next to the exe: a one-file build unpacks to a temp dir."""
    if IS_WINDOWS and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


CONFIG_PATH = data_dir() / "config.json"
# Versions before 1.0 kept config.json next to the script; it is migrated on first launch.
LEGACY_CONFIG_PATH = Path(sys.executable if IS_FROZEN else __file__).resolve().parent / "config.json"


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
@dataclass
class Settings:
    watch_directory: str = ""
    webhook_url: str = ""
    upload_delay: int = 2
    delete_after_upload: bool = False
    minimize_on_exit: bool = False
    start_on_startup: bool = False
    monitoring_active: bool = False

    @classmethod
    def load(cls):
        for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                known = {f.name for f in fields(cls)}
                settings = cls(**{k: v for k, v in data.items() if k in known})
                try:
                    settings.upload_delay = max(0, min(MAX_DELAY_SECONDS, int(settings.upload_delay)))
                except (TypeError, ValueError):
                    settings.upload_delay = cls.upload_delay
                return settings
        return cls()

    def save(self):
        """Write settings atomically. Returns an error message, or None on success."""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
        except OSError as e:
            return str(e)
        return None

    @property
    def folder_ok(self):
        return bool(self.watch_directory) and os.path.isdir(self.watch_directory)

    @property
    def webhook_ok(self):
        return bool(WEBHOOK_PATTERN.match(self.webhook_url.strip()))


# ----------------------------------------------------------------------
# Windows integration
# ----------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command():
    """Command line Windows runs at sign-in; starts hidden in the tray."""
    if IS_FROZEN:
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{os.path.abspath(__file__)}" --minimized'


def set_start_with_windows(enabled):
    """Add or remove the sign-in entry. Returns an error message, or None on success."""
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        return str(e)
    return None


# ----------------------------------------------------------------------
# Uploading
# ----------------------------------------------------------------------
def wait_until_stable(path, timeout=60.0, interval=0.5):
    """Wait until a file stops growing so a half-written screenshot is never uploaded."""
    deadline = time.monotonic() + timeout
    last_size, stable_checks = -1, 0
    while time.monotonic() < deadline:
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size > 0 and size == last_size:
            stable_checks += 1
            if stable_checks >= 2:
                return True
        else:
            stable_checks = 0
        last_size = size
        time.sleep(interval)
    return False


def retry_after(response):
    """Seconds Discord asks us to wait after a 429, capped so a worker never stalls for long."""
    try:
        seconds = float(response.json().get("retry_after", 2))
    except ValueError:
        seconds = float(response.headers.get("Retry-After", 2))
    return min(max(seconds, 0.5), 30)


def post_file(url, path, attempts=3):
    """POST a file to a webhook, retrying on rate limits and brief network failures."""
    for attempt in range(1, attempts + 1):
        try:
            with open(path, "rb") as f:
                response = requests.post(url, files={"file": (os.path.basename(path), f)}, timeout=(10, 120))
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
            continue
        if response.status_code == 429 and attempt < attempts:
            time.sleep(retry_after(response))
            continue
        return response


class _NewFileHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self._callback(os.fsdecode(event.src_path))

    def on_moved(self, event):
        # Some tools write to a temp name and rename it into place when done.
        if not event.is_directory:
            self._callback(os.fsdecode(event.dest_path))


class Uploader(QObject):
    """Watches a folder and posts new screenshots to Discord on background threads."""
    log = pyqtSignal(str, str)  # level, message
    uploaded = pyqtSignal()
    test_finished = pyqtSignal(bool, str)  # ok, message

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._observer = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upload")
        self._in_progress = set()
        self._lock = threading.Lock()

    @property
    def running(self):
        return self._observer is not None

    def start(self, folder):
        """Start watching `folder`. Raises OSError if it can't be watched."""
        self.stop()
        observer = Observer()
        observer.schedule(_NewFileHandler(self._queue), folder, recursive=False)
        observer.start()
        self._observer = observer

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def shutdown(self):
        self.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def test_webhook(self, url):
        self._pool.submit(self._test_webhook, url)

    def _test_webhook(self, url):
        try:
            response = requests.post(url, json={"content": "✅ Steamcorder is connected."}, timeout=10)
            if response.ok:
                self.test_finished.emit(True, "Test message sent. Check your Discord channel.")
            else:
                self.test_finished.emit(False, f"Discord rejected the webhook (HTTP {response.status_code}).")
        except requests.RequestException:
            self.test_finished.emit(False, "Couldn't reach Discord. Check your connection.")

    def _queue(self, path):
        if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
            return
        with self._lock:
            if path in self._in_progress:
                return
            self._in_progress.add(path)
        self._pool.submit(self._process, path)

    def _process(self, path):
        name = os.path.basename(path)
        try:
            if not wait_until_stable(path):
                self.log.emit("warning", f"Skipped {name}: the file disappeared or never finished saving")
                return
            if self.settings.upload_delay:
                time.sleep(self.settings.upload_delay)
            if self.running:
                self._upload(path, name)
        except requests.RequestException:
            self.log.emit("error", f"Couldn't upload {name}: Discord is unreachable")
        except Exception as e:  # a worker must never die silently
            self.log.emit("error", f"Couldn't upload {name}: {e}")
        finally:
            with self._lock:
                self._in_progress.discard(path)

    def _upload(self, path, name):
        url = self.settings.webhook_url.strip()
        if not WEBHOOK_PATTERN.match(url):
            self.log.emit("error", f"Couldn't upload {name}: no valid webhook URL in Settings")
            return

        size_mb = os.path.getsize(path) / 1_048_576
        started = time.perf_counter()
        response = post_file(url, path)
        elapsed = time.perf_counter() - started

        if response.status_code == 413:
            self.log.emit("error", f"{name} is too large for this Discord server ({size_mb:.1f} MB)")
            return
        if not response.ok:
            self.log.emit("error", f"Discord rejected {name} (HTTP {response.status_code})")
            return

        self.log.emit("success", f"Uploaded {name} · {size_mb:.1f} MB in {elapsed:.1f}s")
        self.uploaded.emit()

        if self.settings.delete_after_upload:
            try:
                os.remove(path)
            except OSError as e:
                self.log.emit("warning", f"Uploaded {name} but couldn't delete it: {e.strerror}")


# ----------------------------------------------------------------------
# Look and feel
# ----------------------------------------------------------------------
COLORS = {
    "bg": "#0e1014",
    "surface": "#161920",
    "raised": "#1e222b",
    "border": "#2a2f3a",
    "text": "#e7e9ee",
    "muted": "#8a919e",
    "accent": "#5b8cff",
    "accent_hover": "#7aa2ff",
    "success": "#3fcf8e",
    "warning": "#f2b84b",
    "error": "#f06b6b",
}

STYLE_TEMPLATE = """
* {
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
    color: @text;
}
QMainWindow, #page { background: @bg; }
QLabel { background: transparent; }
#title { font-size: 15pt; font-weight: 600; }
#muted { color: @muted; }
#small { color: @muted; font-size: 9pt; }
#section { color: @muted; font-size: 8pt; font-weight: 600; }
#hint { font-size: 9pt; color: @muted; }
#hint[tone="ok"] { color: @success; }
#hint[tone="bad"] { color: @error; }

#card { background: @surface; border: 1px solid @border; border-radius: 12px; }
#statusTitle { font-size: 12pt; font-weight: 600; }
#statusDot { background: @muted; border-radius: 5px; }
#statusDot[on="true"] { background: @success; }

QPushButton {
    background: @raised; border: 1px solid @border; border-radius: 8px; padding: 7px 14px;
}
QPushButton:hover { border-color: @muted; }
QPushButton:pressed { background: @border; }
QPushButton:disabled { color: @muted; border-color: @border; }
QPushButton:checked { border-color: @accent; }
QPushButton#primary {
    background: @accent; border: none; color: #ffffff; font-size: 11pt; font-weight: 600; padding: 11px;
}
QPushButton#primary:hover { background: @accent_hover; }
QPushButton#primary[running="true"] { background: @raised; border: 1px solid @border; color: @text; }
QPushButton#primary[running="true"]:hover { border-color: @error; color: @error; }
QPushButton#link { background: transparent; border: none; color: @muted; padding: 4px 6px; }
QPushButton#link:hover { color: @text; }

QLineEdit {
    background: @raised; border: 1px solid @border; border-radius: 8px; padding: 7px 10px;
    selection-background-color: @accent;
}
QLineEdit:focus { border-color: @accent; }
QLineEdit:read-only { color: @muted; }

QSlider::groove:horizontal { height: 4px; background: @border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 2px; }
QSlider::handle:horizontal {
    background: @text; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}

QCheckBox { spacing: 10px; padding: 5px 0; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid @muted; background: @raised;
}
QCheckBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent; image: url(@check); }

QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border: none; }
QListWidget::item:selected, QListWidget::item:hover { background: transparent; }

QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: @border; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QMenu { background: @surface; border: 1px solid @border; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 20px; border-radius: 6px; }
QMenu::item:selected { background: @raised; }
QMenu::separator { height: 1px; background: @border; margin: 4px 6px; }
QMessageBox { background: @surface; }
QToolTip { background: @raised; border: 1px solid @border; color: @text; padding: 4px; }
"""


def make_check_image():
    """Render the checkbox tick to a PNG, since Qt style sheets can only reference image files."""
    path = Path(tempfile.gettempdir()) / "steamcorder-check.png"
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"), 4, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline([QPointF(8, 17), QPointF(14, 23), QPointF(25, 10)])
    painter.end()
    pixmap.save(str(path), "PNG")
    return path.as_posix()


def build_stylesheet():
    tokens = dict(COLORS, check=make_check_image())
    qss = STYLE_TEMPLATE
    # Longest names first so "@accent" never clobbers "@accent_hover".
    for name in sorted(tokens, key=len, reverse=True):
        qss = qss.replace(f"@{name}", tokens[name])
    return qss


def dot_pixmap(color, size=8):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


def repolish(widget):
    """Re-apply the style sheet after changing a dynamic property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def short_path(path, parts=2):
    """Last few components of a path, e.g. '…/Steam/screenshots'."""
    pieces = Path(path).parts
    return path if len(pieces) <= parts + 1 else "…/" + "/".join(pieces[-parts:])


# ----------------------------------------------------------------------
# Home page
# ----------------------------------------------------------------------
LEVEL_COLORS = {"info": COLORS["muted"], "success": COLORS["success"],
                "warning": COLORS["warning"], "error": COLORS["error"]}


class ActivityRow(QWidget):
    def __init__(self, level, message):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(10)

        dot = QLabel()
        dot.setPixmap(dot_pixmap(LEVEL_COLORS.get(level, COLORS["muted"])))
        text = QLabel(message)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        if level == "error":
            text.setStyleSheet(f"color: {COLORS['error']};")
        stamp = label(time.strftime("%H:%M"), "small")

        top = Qt.AlignmentFlag.AlignTop
        dot.setFixedHeight(text.fontMetrics().height())
        layout.addWidget(dot, 0, top)
        layout.addWidget(text, 1, top)
        layout.addWidget(stamp, 0, top)


class ActivityList(QListWidget):
    """List of ActivityRows whose heights follow the list width, so wrapped text never gets clipped."""

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def prepend(self, row):
        item = QListWidgetItem()
        self.insertItem(0, item)
        self.setItemWidget(item, row)
        self._fit(item)
        while self.count() > MAX_LOG_ENTRIES:
            self.takeItem(self.count() - 1)
        self.scrollToTop()

    def _fit(self, item):
        width = self.viewport().width()
        row = self.itemWidget(item)
        height = row.layout().heightForWidth(width) if width > 0 else -1
        item.setSizeHint(QSize(width, height if height > 0 else row.sizeHint().height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for i in range(self.count()):
            self._fit(self.item(i))


class HomePage(QWidget):
    toggle_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(16)

        # Header
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(resource_path("icon.png")).pixmap(QSize(30, 30)))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(APP_NAME, "title"))
        titles.addWidget(label("Steam screenshots → Discord", "small"))
        settings_btn = QPushButton("Settings")
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.clicked.connect(self.settings_requested)
        header.addWidget(logo)
        header.addSpacing(6)
        header.addLayout(titles, 1)
        header.addWidget(settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # Status card
        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 18)
        card_layout.setSpacing(4)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status_dot = QFrame(objectName="statusDot")
        self.status_dot.setFixedSize(10, 10)
        self.status_title = label("", "statusTitle")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_title, 1)
        self.status_detail = label("", "muted", wrap=True)
        self.toggle_btn = QPushButton(objectName="primary")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle_requested)
        card_layout.addLayout(status_row)
        card_layout.addWidget(self.status_detail)
        card_layout.addSpacing(12)
        card_layout.addWidget(self.toggle_btn)
        layout.addWidget(card)

        # Activity
        activity_section = QVBoxLayout()
        activity_section.setSpacing(6)
        activity_header = QHBoxLayout()
        activity_header.addWidget(label("ACTIVITY", "section"), 1)
        clear_btn = QPushButton("Clear", objectName="link")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self.clear_activity)
        activity_header.addWidget(clear_btn)
        activity_section.addLayout(activity_header)

        activity_card = QFrame(objectName="card")
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(12, 8, 8, 8)
        self.activity = ActivityList()
        self.empty_label = label("Screenshots you take in Steam will show up here.", "muted", wrap=True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        activity_layout.addWidget(self.activity)
        activity_layout.addWidget(self.empty_label, 1)
        activity_section.addWidget(activity_card, 1)
        layout.addLayout(activity_section, 1)
        self._update_empty_state()

    def set_status(self, running, settings, uploads):
        self.status_dot.setProperty("on", running)
        self.toggle_btn.setProperty("running", running)
        repolish(self.status_dot)
        repolish(self.toggle_btn)
        if running:
            self.status_title.setText("Watching for screenshots")
            count = f"{uploads} uploaded this session" if uploads else "Nothing uploaded yet"
            self.status_detail.setText(f"{short_path(settings.watch_directory)} · {count}")
            self.toggle_btn.setText("Stop")
        else:
            self.status_title.setText("Paused")
            if settings.folder_ok and settings.webhook_ok:
                self.status_detail.setText(f"Ready to watch {short_path(settings.watch_directory)}")
            else:
                self.status_detail.setText("Choose a screenshot folder and a Discord webhook in Settings.")
            self.toggle_btn.setText("Start watching")

    def add_activity(self, level, message):
        self.activity.prepend(ActivityRow(level, message))
        self._update_empty_state()

    def clear_activity(self):
        self.activity.clear()
        self._update_empty_state()

    def _update_empty_state(self):
        has_items = self.activity.count() > 0
        self.activity.setVisible(has_items)
        self.empty_label.setVisible(not has_items)


# ----------------------------------------------------------------------
# Settings page
# ----------------------------------------------------------------------
HOW_TO = (
    "**1. Turn on uncompressed screenshots in Steam**  \n"
    "Steam → Settings → In Game → *Save an uncompressed copy*, and pick a folder.\n\n"
    "**2. Choose that folder here**  \n"
    "Click *Browse* next to Screenshot folder.\n\n"
    "**3. Create a Discord webhook**  \n"
    "Server Settings → Integrations → Webhooks → *New Webhook* → *Copy Webhook URL*, "
    "then paste it here and click *Test*.\n\n"
    "**4. Start watching**  \n"
    "Go back and click *Start watching*. New screenshots are posted automatically."
)


class SettingsPage(QWidget):
    back_requested = pyqtSignal()
    changed = pyqtSignal(str)  # name of the setting that changed
    test_requested = pyqtSignal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(6)

        header = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.back_requested)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Settings", "title"))
        titles.addWidget(label("Changes are saved automatically", "small"))
        header.addLayout(titles, 1)
        header.addWidget(back_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        layout.addSpacing(14)

        # Folder
        layout.addWidget(label("SCREENSHOT FOLDER", "section"))
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(settings.watch_directory)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("No folder selected")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)
        self.folder_hint = label("", "hint", wrap=True)
        layout.addWidget(self.folder_hint)
        layout.addSpacing(10)

        # Webhook
        layout.addWidget(label("DISCORD WEBHOOK", "section"))
        webhook_row = QHBoxLayout()
        self.webhook_edit = QLineEdit(settings.webhook_url)
        self.webhook_edit.setPlaceholderText("https://discord.com/api/webhooks/…")
        self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.webhook_edit.textChanged.connect(self._webhook_edited)
        self.show_btn = QPushButton("Show", checkable=True)
        self.show_btn.toggled.connect(self._toggle_webhook_visibility)
        self.test_btn = QPushButton("Test")
        self.test_btn.clicked.connect(self._test)
        webhook_row.addWidget(self.webhook_edit, 1)
        webhook_row.addWidget(self.show_btn)
        webhook_row.addWidget(self.test_btn)
        layout.addLayout(webhook_row)
        self.webhook_hint = label("", "hint", wrap=True)
        layout.addWidget(self.webhook_hint)
        layout.addSpacing(10)

        # Delay
        layout.addWidget(label("UPLOAD DELAY", "section"))
        delay_row = QHBoxLayout()
        self.delay_slider = QSlider(Qt.Orientation.Horizontal)
        self.delay_slider.setRange(0, MAX_DELAY_SECONDS)
        self.delay_slider.setValue(settings.upload_delay)
        self.delay_slider.valueChanged.connect(self._delay_changed)
        self.delay_value = label("", "muted")
        self.delay_value.setMinimumWidth(36)
        self.delay_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        delay_row.addWidget(self.delay_slider, 1)
        delay_row.addWidget(self.delay_value)
        layout.addLayout(delay_row)
        layout.addWidget(label("Extra wait after a screenshot is saved, before it's posted.", "hint"))
        layout.addSpacing(10)

        # Options
        layout.addWidget(label("OPTIONS", "section"))
        self.delete_cb = self._checkbox("Delete screenshots after uploading", "delete_after_upload")
        self.tray_cb = self._checkbox("Keep running in the tray when closed", "minimize_on_exit")
        self.startup_cb = self._checkbox("Start with Windows", "start_on_startup")
        self.startup_cb.setVisible(IS_WINDOWS)
        for cb in (self.delete_cb, self.tray_cb, self.startup_cb):
            layout.addWidget(cb)

        layout.addStretch(1)

        footer = QHBoxLayout()
        help_btn = QPushButton("How to set up", objectName="link")
        help_btn.clicked.connect(self._show_help)
        support_btn = QPushButton("Get support", objectName="link")
        support_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DISCORD_INVITE)))
        for btn in (help_btn, support_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            footer.addWidget(btn)
        footer.addStretch(1)
        footer.addWidget(label(f"v{APP_VERSION} · by @imfeys", "small"))
        layout.addLayout(footer)

        # Webhook typing is saved after a short pause rather than on every keystroke.
        self._webhook_timer = QTimer(self, singleShot=True, interval=400)
        self._webhook_timer.timeout.connect(lambda: self.changed.emit("webhook_url"))

        self._delay_changed(settings.upload_delay, emit=False)
        self._refresh_hints()

    def _checkbox(self, text, attr):
        cb = QCheckBox(text)
        cb.setChecked(bool(getattr(self.settings, attr)))
        cb.setCursor(Qt.CursorShape.PointingHandCursor)

        def on_toggled(checked):
            setattr(self.settings, attr, checked)
            self.changed.emit(attr)
        cb.toggled.connect(on_toggled)
        return cb

    def _browse(self):
        start = self.settings.watch_directory or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose your Steam screenshot folder", start)
        if folder:
            folder = os.path.normpath(folder)
            self.folder_edit.setText(folder)
            self.settings.watch_directory = folder
            self._refresh_hints()
            self.changed.emit("watch_directory")

    def _webhook_edited(self, text):
        self.settings.webhook_url = text.strip()
        self._refresh_hints()
        self._webhook_timer.start()

    def _toggle_webhook_visibility(self, visible):
        self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_btn.setText("Hide" if visible else "Show")

    def _delay_changed(self, value, emit=True):
        self.delay_value.setText(f"{value} s")
        if emit:
            self.settings.upload_delay = value
            self.changed.emit("upload_delay")

    def _test(self):
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        self._set_hint(self.webhook_hint, "Sending a test message…")
        self.test_requested.emit(self.settings.webhook_url)

    def show_test_result(self, ok, message):
        self.test_btn.setEnabled(self.settings.webhook_ok)
        self.test_btn.setText("Test")
        self._set_hint(self.webhook_hint, ("✓ " if ok else "") + message, "ok" if ok else "bad")

    def flag_missing(self):
        """Point out what still needs filling in before watching can start."""
        self._refresh_hints(strict=True)

    def _refresh_hints(self, strict=False):
        s = self.settings
        if s.watch_directory and not s.folder_ok:
            self._set_hint(self.folder_hint, "This folder no longer exists.", "bad")
        elif not s.watch_directory and strict:
            self._set_hint(self.folder_hint, "Choose the folder Steam saves uncompressed screenshots to.", "bad")
        else:
            self._set_hint(self.folder_hint, "")

        if s.webhook_url and not s.webhook_ok:
            self._set_hint(self.webhook_hint, "That doesn't look like a Discord webhook URL.", "bad")
        elif not s.webhook_url and strict:
            self._set_hint(self.webhook_hint, "Paste a webhook URL from your Discord server.", "bad")
        else:
            self._set_hint(self.webhook_hint, "")
        self.test_btn.setEnabled(s.webhook_ok)

    @staticmethod
    def _set_hint(widget, text, tone=""):
        widget.setText(text)
        widget.setVisible(bool(text))
        widget.setProperty("tone", tone)
        repolish(widget)

    def _show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("How to set up Steamcorder")
        box.setTextFormat(Qt.TextFormat.MarkdownText)
        box.setText(HOW_TO)
        box.exec()


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.uploads = 0
        self._tray_tip_shown = False
        self._quitting = False

        self.uploader = Uploader(settings)
        self.uploader.log.connect(self.log)
        self.uploader.uploaded.connect(self._on_uploaded)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.resize(460, 640)
        self.setMinimumSize(400, 540)

        self.home = HomePage()
        self.settings_page = SettingsPage(settings)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.home)
        self.pages.addWidget(self.settings_page)
        self.setCentralWidget(self.pages)

        self.home.toggle_requested.connect(self.toggle_watching)
        self.home.settings_requested.connect(lambda: self.pages.setCurrentWidget(self.settings_page))
        self.settings_page.back_requested.connect(lambda: self.pages.setCurrentWidget(self.home))
        self.settings_page.changed.connect(self._setting_changed)
        self.settings_page.test_requested.connect(self.uploader.test_webhook)
        self.uploader.test_finished.connect(self.settings_page.show_test_result)

        self._init_tray()
        self._refresh()

        if settings.start_on_startup:
            set_start_with_windows(True)  # keep the entry pointing at wherever the app lives now
        if settings.monitoring_active and settings.folder_ok and settings.webhook_ok:
            self.start_watching()

    # --- tray -----------------------------------------------------------
    def _init_tray(self):
        self.tray = QSystemTrayIcon(QIcon(resource_path("icon.ico")), self)
        menu = QMenu(self)
        menu.addAction("Open Steamcorder", self.bring_to_front)
        self.tray_toggle = menu.addAction("Start watching", self.toggle_watching)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.bring_to_front()

    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # --- watching -------------------------------------------------------
    def toggle_watching(self):
        if self.uploader.running:
            self.stop_watching()
        else:
            self.start_watching()

    def start_watching(self):
        if not (self.settings.folder_ok and self.settings.webhook_ok):
            self.settings_page.flag_missing()
            self.pages.setCurrentWidget(self.settings_page)
            self.bring_to_front()
            return
        try:
            self.uploader.start(self.settings.watch_directory)
        except OSError as e:
            self.log("error", f"Couldn't watch {self.settings.watch_directory}: {e.strerror or e}")
            return
        self.log("info", f"Watching {self.settings.watch_directory}")
        self._set_active(True)

    def stop_watching(self):
        self.uploader.stop()
        self.log("info", "Stopped watching")
        self._set_active(False)

    def _set_active(self, active):
        self.settings.monitoring_active = active
        self._save()
        self._refresh()

    def _on_uploaded(self):
        self.uploads += 1
        self._refresh()

    def _setting_changed(self, name):
        if name == "start_on_startup":
            error = set_start_with_windows(self.settings.start_on_startup)
            if error:
                self.log("error", f"Couldn't update Windows startup: {error}")
        if name == "watch_directory" and self.uploader.running:
            self.stop_watching()
            self.start_watching()  # pick up the new folder
        self._save()
        self._refresh()

    def _save(self):
        error = self.settings.save()
        if error:
            self.log("error", f"Couldn't save settings: {error}")

    def _refresh(self):
        running = self.uploader.running
        self.home.set_status(running, self.settings, self.uploads)
        self.tray_toggle.setText("Stop watching" if running else "Start watching")
        self.tray.setToolTip(f"{APP_NAME}: {'watching' if running else 'paused'}")

    def log(self, level, message):
        self.home.add_activity(level, message)

    # --- closing --------------------------------------------------------
    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        if self.settings.minimize_on_exit and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            if not self._tray_tip_shown:
                self.tray.showMessage(APP_NAME, "Still running in the tray. Right-click the icon to quit.",
                                      QSystemTrayIcon.MessageIcon.Information, 3000)
                self._tray_tip_shown = True
        else:
            event.accept()
            self.quit()

    def quit(self):
        self._quitting = True
        self.uploader.shutdown()
        self.tray.hide()
        QApplication.quit()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
INSTANCE_KEY = f"{APP_NAME}-{getpass.getuser()}"


def signal_running_instance():
    """If Steamcorder is already open, ask it to show itself and return True."""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_KEY)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def listen_for_instances(window):
    server = QLocalServer(window)
    QLocalServer.removeServer(INSTANCE_KEY)  # clear a stale socket left by a crash
    server.newConnection.connect(lambda: (server.nextPendingConnection(), window.bring_to_front()))
    server.listen(INSTANCE_KEY)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"imfeys.{APP_NAME}")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps us alive; quitting is explicit

    # Running twice would upload every screenshot twice.
    if signal_running_instance():
        return 0

    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet())

    settings = Settings.load()
    window = MainWindow(settings)
    listen_for_instances(window)

    wants_hidden = "--minimized" in sys.argv or (settings.minimize_on_exit and settings.monitoring_active)
    if not (wants_hidden and QSystemTrayIcon.isSystemTrayAvailable()):
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
