import os
import sys
import time
import json
import ctypes
import winreg
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QTextEdit, QMessageBox, QTabWidget,
    QGroupBox, QSpinBox, QCheckBox, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import QFont, QIcon, QCursor, QDesktopServices
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl

# ----------------------------------------------------------------------
# Helper function to get resource paths
# ----------------------------------------------------------------------
def resource_path(relative_path):
    """Get the absolute path to a resource, works in development and with PyInstaller"""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

# ----------------------------------------------------------------------
# Config file handling using a fixed path relative to this script
# ----------------------------------------------------------------------
CONFIG_FILE = "config.json"

def get_config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE)

def load_config():
    config_path = get_config_path()
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(config):
    config_path = get_config_path()
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def update_startup_registry(enable):
    app_path = os.path.abspath(sys.argv[0])
    reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, "Steamcorder", 0, winreg.REG_SZ, app_path)
            else:
                winreg.DeleteValue(key, "Steamcorder")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Error updating startup registry: {e}")

# ----------------------------------------------------------------------
# Global variables
# ----------------------------------------------------------------------
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}
DELETE_AFTER_UPLOAD = False  # will be updated at startup

# ----------------------------------------------------------------------
# File monitoring classes
# ----------------------------------------------------------------------
class FileHandler(FileSystemEventHandler):
    def __init__(self, log_callback, webhook_url, delay_seconds):
        super().__init__()
        self.log_callback = log_callback
        self.webhook_url = webhook_url
        self.delay_seconds = delay_seconds

    def on_created(self, event):
        if event.is_directory:
            return
        creation_time = time.time()  # record creation time immediately
        self.log_callback("New screenshot detected.")
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if self.is_allowed_file(event.src_path):
            self.upload_file(event.src_path, creation_time)
        else:
            self.log_callback("Ignored screenshot (unsupported file type).")

    def is_allowed_file(self, file_path):
        return os.path.splitext(file_path)[1].lower() in ALLOWED_EXTENSIONS

    def upload_file(self, file_path, creation_time):
        if not self.webhook_url:
            self.log_callback("No webhook URL set! Please enter one.")
            return
        # Retry mechanism: wait up to 1.5 sec for the file to exist.
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts and not os.path.exists(file_path):
            time.sleep(0.5)
            attempts += 1

        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            files = {'file': (os.path.basename(file_path), file_data)}
            start_request = time.perf_counter()
            response = requests.post(self.webhook_url, files=files)
            upload_duration = time.perf_counter() - start_request
            total_duration = time.time() - creation_time
            if response.status_code == 200:
                self.log_callback(
                    f"Uploaded screenshot in {upload_duration:.2f} sec (total: {total_duration:.2f} sec)."
                )
                if DELETE_AFTER_UPLOAD:
                    os.remove(file_path)
                    self.log_callback("Deleted screenshot after upload.")
            else:
                self.log_callback(f"Upload failed. Status code: {response.status_code}")
        except Exception as e:
            self.log_callback(f"Error uploading screenshot: {e}")

class MonitoringThread(QThread):
    log_signal = pyqtSignal(str)
    def __init__(self, watch_directory, webhook_url, delay_seconds):
        super().__init__()
        self.watch_directory = watch_directory
        self.webhook_url = webhook_url
        self.delay_seconds = delay_seconds
        self.observer = None

    def run(self):
        self.log_signal.emit(f"Monitoring started: {self.watch_directory}")
        self.file_handler = FileHandler(self.log_signal.emit, self.webhook_url, self.delay_seconds)
        self.observer = Observer()
        self.observer.schedule(self.file_handler, path=self.watch_directory, recursive=False)
        self.observer.start()
        self.observer.join()

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.log_signal.emit("Monitoring stopped.")

    def update_delay(self, new_delay):
        if hasattr(self, 'file_handler'):
            self.file_handler.delay_seconds = new_delay
            self.log_signal.emit(f"Updated upload delay to {new_delay} seconds.")

# ----------------------------------------------------------------------
# Settings tab widget (includes Setup section)
# ----------------------------------------------------------------------
class SettingsTab(QWidget):
    # Signal to notify that settings were updated (with the new delay value)
    settings_updated = pyqtSignal(int)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # --- Setup Section ---
        setup_group = QGroupBox("Setup")
        setup_layout = QVBoxLayout()
        # Folder row
        folder_layout = QHBoxLayout()
        folder_label = QLabel("Folder Path:")
        self.folder_edit = QLineEdit()
        self.folder_edit.setText(self.config.get("watch_directory", ""))
        folder_btn = QPushButton("Browse")
        folder_btn.clicked.connect(self.select_folder)
        folder_layout.addWidget(folder_label)
        folder_layout.addWidget(self.folder_edit, stretch=1)
        folder_layout.addWidget(folder_btn)
        setup_layout.addLayout(folder_layout)
        # Webhook row
        webhook_layout = QHBoxLayout()
        webhook_label = QLabel("Webhook URL:")
        self.webhook_edit = QLineEdit()
        self.webhook_edit.setText(self.config.get("webhook_url", ""))
        save_webhook_btn = QPushButton("Save")
        save_webhook_btn.clicked.connect(self.save_webhook)
        self.toggle_webhook_btn = QPushButton("Hide" if not self.config.get("webhook_hidden", False) else "Show")
        self.toggle_webhook_btn.clicked.connect(self.toggle_webhook_visibility)
        webhook_layout.addWidget(webhook_label)
        webhook_layout.addWidget(self.webhook_edit, stretch=1)
        webhook_layout.addWidget(save_webhook_btn)
        webhook_layout.addWidget(self.toggle_webhook_btn)
        setup_layout.addLayout(webhook_layout)
        setup_group.setLayout(setup_layout)
        layout.addWidget(setup_group)

        # --- Other Settings ---
        # Upload delay
        delay_layout = QHBoxLayout()
        delay_label = QLabel("Upload Delay (sec):")
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setValue(self.config.get("upload_delay", 2))
        delay_layout.addWidget(delay_label)
        delay_layout.addWidget(self.delay_spin)
        layout.addLayout(delay_layout)

        # Checkboxes
        self.minimize_cb = QCheckBox("Minimize to tray on exit")
        self.minimize_cb.setChecked(self.config.get("minimize_on_exit", False))
        layout.addWidget(self.minimize_cb)

        self.startup_cb = QCheckBox("Start on Windows startup")
        self.startup_cb.setChecked(self.config.get("start_on_startup", False))
        layout.addWidget(self.startup_cb)

        self.delete_cb = QCheckBox("Delete files after upload")
        self.delete_cb.setChecked(self.config.get("delete_after_upload", False))
        layout.addWidget(self.delete_cb)

        # How-to button
        self.how_to_btn = QPushButton("How to Use")
        self.how_to_btn.clicked.connect(self.show_how_to)
        layout.addWidget(self.how_to_btn)

        # Save settings button
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.clicked.connect(self.save_settings)
        layout.addWidget(self.save_btn)

        # Discord (Community & Support) group
        community_box = QGroupBox("Community & Support")
        community_layout = QHBoxLayout()
        self.discord_btn = QPushButton("Join Discord")
        self.discord_btn.clicked.connect(self.join_discord)
        community_layout.addStretch(1)
        community_layout.addWidget(self.discord_btn)
        community_box.setLayout(community_layout)
        layout.addWidget(community_box)

        layout.addStretch(1)
        self.setLayout(layout)

    # Functions for Setup section
    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.folder_edit.setText(folder)
            self.config["watch_directory"] = folder
            save_config(self.config)

    def save_webhook(self):
        self.config["webhook_url"] = self.webhook_edit.text()
        save_config(self.config)
        QMessageBox.information(self, "Settings", "Webhook URL saved.")

    def toggle_webhook_visibility(self):
        if self.webhook_edit.echoMode() == QLineEdit.EchoMode.Normal:
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_webhook_btn.setText("Show")
            self.config["webhook_hidden"] = True
        else:
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_webhook_btn.setText("Hide")
            self.config["webhook_hidden"] = False
        save_config(self.config)

    # Functions for the other settings
    def show_how_to(self):
        instructions = (
            "1. Select your Steam screenshot folder (enable 'Save an uncompressed copy of screenshot' in Steam).\n"
            "2. Get a Discord webhook URL from your channel/server.\n"
            "3. Enter the webhook URL and adjust the delay if needed.\n"
            "4. Press Start to begin monitoring."
        )
        QMessageBox.information(self, "How to Use Steamcorder", instructions)

    def save_settings(self):
        self.config["upload_delay"] = self.delay_spin.value()
        self.config["minimize_on_exit"] = self.minimize_cb.isChecked()
        self.config["start_on_startup"] = self.startup_cb.isChecked()
        self.config["delete_after_upload"] = self.delete_cb.isChecked()
        global DELETE_AFTER_UPLOAD
        DELETE_AFTER_UPLOAD = self.delete_cb.isChecked()
        update_startup_registry(self.startup_cb.isChecked())
        save_config(self.config)
        QMessageBox.information(self, "Settings", "Settings saved successfully.")
        # Emit the new delay so the main window can update the monitoring thread.
        self.settings_updated.emit(self.delay_spin.value())

    def join_discord(self):
        QDesktopServices.openUrl(QUrl("https://discord.gg/dMvCH93sYX"))

# ----------------------------------------------------------------------
# Main window (Dashboard tab now only has Monitoring Control and logs)
# ----------------------------------------------------------------------
class SteamcorderMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.webhook_url = self.config.get("webhook_url", "")
        self.watch_directory = self.config.get("watch_directory", "") or ""
        self.monitoring_thread = None
        self.webhook_hidden = bool(self.config.get("webhook_hidden", False))
        global DELETE_AFTER_UPLOAD
        DELETE_AFTER_UPLOAD = self.config.get("delete_after_upload", False)
        self.init_tray_icon()
        self.init_ui()
        # Connect the settings_updated signal from the Settings tab.
        self.settings_tab.settings_updated.connect(self.update_monitoring_delay)
        if self.config.get("monitoring_active", False):
            if self.webhook_url and self.watch_directory:
                self.toggle_monitoring()

    def init_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(QIcon(resource_path("icon.ico")), self)
        self.tray_icon.setToolTip("Steamcorder v0.6.1")
        self.tray_menu = QMenu(self)

        quit_action = self.tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)

        restore_action = self.tray_menu.addAction("Restore Window")
        restore_action.triggered.connect(self.showNormal)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_clicked)
        self.tray_icon.show()

    def init_ui(self):
        self.setWindowTitle("Steamcorder v0.6.1")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.setGeometry(100, 100, 500, 330)
        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_dashboard_tab(), "Dashboard")
        self.settings_tab = SettingsTab(self.config)
        self.tabs.addTab(self.settings_tab, "Settings")
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central_widget)

    def create_dashboard_tab(self):
        dashboard = QWidget()
        layout = QVBoxLayout()
        # Monitoring group
        monitor_group = QGroupBox("Monitoring Control")
        monitor_layout = QVBoxLayout()
        self.monitor_btn = QPushButton("Start Monitoring")
        self.monitor_btn.setFixedHeight(50)
        self.monitor_btn.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        monitor_layout.addWidget(self.monitor_btn)
        self.status_label = QLabel("Status: Idle")
        self.status_label.setFont(QFont("Segoe UI", 12))
        monitor_layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignCenter)
        monitor_group.setLayout(monitor_layout)
        layout.addWidget(monitor_group)
        # Log area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        layout.addStretch(1)
        dashboard.setLayout(layout)
        return dashboard

    def update_monitoring_delay(self, new_delay):
        # If monitoring is active, update the thread's delay immediately.
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.update_delay(new_delay)
            self.log(f"Delay updated to {new_delay} seconds.")

    def toggle_monitoring(self):
        # Reload config to get latest values.
        self.config = load_config()
        self.webhook_url = self.config.get("webhook_url", "")
        self.watch_directory = self.config.get("watch_directory", "")
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.stop()
            self.monitoring_thread.wait()
            self.monitoring_thread = None
            self.monitor_btn.setText("Start Monitoring")
            self.config["monitoring_active"] = False
            save_config(self.config)
            self.status_label.setText("Status: Stopped")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            if not self.watch_directory:
                QMessageBox.warning(self, "Error", "Please select a folder path.")
                return
            delay = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_btn.setText("Stop Monitoring")
            self.config["monitoring_active"] = True
            save_config(self.config)
            self.status_label.setText("Status: Running")

    def log(self, message):
        self.log_text.append(message)
        print(message)

    def tray_icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.tray_menu.exec(QCursor.pos())
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()

    def closeEvent(self, event):
        if self.config.get("minimize_on_exit", False):
            self.hide()
            self.tray_icon.showMessage("Steamcorder", "Running in the background.",
                                         QSystemTrayIcon.MessageIcon.Information, 2000)
            event.ignore()
        else:
            if self.monitoring_thread and self.monitoring_thread.isRunning():
                self.monitoring_thread.stop()
                self.monitoring_thread.wait()
                self.config["monitoring_active"] = False
                save_config(self.config)
            event.accept()

    def quit_app(self):
        self.tray_icon.hide()
        QApplication.quit()

# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
def main():
    global DELETE_AFTER_UPLOAD
    config = load_config()
    DELETE_AFTER_UPLOAD = config.get("delete_after_upload", False)
    myappid = 'steamcorder.v0.6.1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    app = QApplication(sys.argv)
    window = SteamcorderMainWindow()
    # Start minimized if configured.
    if window.config.get("minimize_on_exit", False):
        window.hide()
    else:
        window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
