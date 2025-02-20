import os
import sys
import time
import requests
import sys
import json

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from PyQt6.QtWidgets import (
    QSizePolicy,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QTextEdit, QLineEdit, QMessageBox,
    QSystemTrayIcon, QMenu, QSpinBox, QDialog, QDialogButtonBox, QCheckBox
)
from PyQt6.QtGui import (
    QFont, QIcon, QAction, QCursor,
    QDesktopServices
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QEvent, QUrl
)

# Default Configuration
CONFIG_FILE = "config.json"
WATCH_DIRECTORY = r''
DELETE_AFTER_UPLOAD = False
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

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
        pass  # Key doesn't exist yet
    except Exception as e:
        print(f"Error updating startup registry: {e}")

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)

class FileHandler(FileSystemEventHandler):
    def __init__(self, log_callback, webhook_url, delay_seconds):
        self.log_callback = log_callback
        self.webhook_url = webhook_url
        self.delay_seconds = delay_seconds

    def on_created(self, event):
        if event.is_directory:
            return
        file_path = event.src_path
        self.log_callback(f"New file detected: {file_path}")

        # Wait for the user-specified delay to ensure file is fully written
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if self.is_allowed_file(file_path):
            self.upload_file(file_path)
        else:
            self.log_callback(f"Ignored file (wrong extension): {file_path}")

    def is_allowed_file(self, file_path):
        return os.path.splitext(file_path)[1].lower() in ALLOWED_EXTENSIONS

    def upload_file(self, file_path):
        if not self.webhook_url:
            self.log_callback("No webhook URL set! Please enter one.")
            return
        
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            files = {'file': (os.path.basename(file_path), file_data)}
            response = requests.post(self.webhook_url, files=files)

            if response.status_code == 200:
                self.log_callback(f"Uploaded {file_path} to Discord.")
                if DELETE_AFTER_UPLOAD:
                    os.remove(file_path)
                    self.log_callback(f"Deleted {file_path} after upload.")
            else:
                self.log_callback(f"Upload failed. Status code: {response.status_code}")
        except Exception as e:
            self.log_callback(f"Error uploading {file_path}: {e}")

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
        handler = FileHandler(self.log_signal.emit, self.webhook_url, self.delay_seconds)
        self.observer = Observer()
        self.observer.schedule(handler, path=self.watch_directory, recursive=False)
        self.observer.start()
        self.observer.join()

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.log_signal.emit("Monitoring stopped.")

import winreg

class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setWindowIcon(QIcon(resource_path("icon.png")))
        self.config = config

        layout = QVBoxLayout()

        # Delay row
        delay_layout = QHBoxLayout()
        delay_label = QLabel("Upload Delay (seconds):")
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setValue(self.config.get("upload_delay", 2))
        delay_layout.addWidget(delay_label)
        delay_layout.addWidget(self.delay_spin)
        layout.addLayout(delay_layout)

        # Minimize to tray on exit
        self.minimize_cb = QCheckBox("Minimize to tray on exit")
        self.minimize_cb.setChecked(self.config.get("minimize_on_exit", False))
        layout.addWidget(self.minimize_cb)
        
        # Start on Windows Startup Checkbox
        self.startup_cb = QCheckBox("Start on Windows startup")
        self.startup_cb.setChecked(self.config.get("start_on_startup", False))
        layout.addWidget(self.startup_cb)

        # How to button
        self.how_to_button = QPushButton("How to?")
        self.how_to_button.clicked.connect(self.show_how_to)
        layout.addWidget(self.how_to_button)

        # OK/Cancel - must use StandardButton for PyQt6
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.on_ok)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

    def on_ok(self):
        # Save startup setting
        self.config["start_on_startup"] = self.startup_cb.isChecked()
        update_startup_registry(self.startup_cb.isChecked())
        save_config(self.config)
        self.accept()
        # Save config changes
        self.config["upload_delay"] = self.delay_spin.value()
        self.config["minimize_on_exit"] = self.minimize_cb.isChecked()
        save_config(self.config)
        self.accept()

    def show_how_to(self):
        instructions = (
            "1. Select your Steam screenshot folder (enable 'Save an uncompressed copy of screenshot' under Steam > Settings > In-Game).\n"
            "2. Obtain a Discord webhook from the server/channel you wish to upload.\n"
            "3. Paste the webhook into the field above.\n"
            "4. Adjust the optional delay if needed, then press 'Start Monitoring' to begin."
        )
        QMessageBox.information(
            self,
            "How to Use Steamcorder",
            instructions
        )

def resource_path(relative_path):
    """ Get the absolute path to the resource (needed for PyInstaller onefile mode) """
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return relative_path

class ScreenshotUploaderApp(QWidget):
    def __init__(self):
        super().__init__()
        # Bump version to 0.4.1
        self.setWindowTitle("Steamcorder v0.4.1")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.setGeometry(100, 100, 500, 400)
        
        self.config = load_config()
        self.webhook_url = self.config.get("webhook_url", "")
        self.watch_directory = self.config.get("watch_directory", "") or ""
        self.monitoring_thread = None

        # Track if the webhook was hidden
        self.webhook_hidden = bool(self.config.get("webhook_hidden", False))

        # System tray icon
        self.tray_icon = QSystemTrayIcon(QIcon(resource_path("icon.ico")), self)
        self.tray_icon.setToolTip("Steamcorder v0.4.1")

        self.tray_menu = QMenu(self)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        self.tray_menu.addAction(quit_action)
        
        
        
        
        
        
        restore_action = QAction("Restore Window")
        restore_action.triggered.connect(self.restore_window)
        self.tray_menu.addAction(restore_action)
        
        
        
        self.tray_icon.setContextMenu(self.tray_menu)

        self.tray_icon.activated.connect(self.tray_icon_clicked)
        self.tray_icon.show()

        main_layout = QVBoxLayout()

        # top row
        top_row = QHBoxLayout()
        self.dir_label = QLabel(f"Watching: {self.watch_directory}")
        top_row.addWidget(self.dir_label)

        # Discord link icon
        self.discord_button = QPushButton()
        self.discord_button.setIcon(QIcon(resource_path("discord_icon.png")))
        self.discord_button.setFixedSize(28, 28)
        self.discord_button.setToolTip("Join our Discord server")
        self.discord_button.clicked.connect(self.open_discord_link)
        top_row.addWidget(self.discord_button)

        # Settings button
        self.settings_button = QPushButton("⚙️")
        self.settings_button.setFixedSize(28, 28)  # Ensure matching size with discord_button
        
        
        self.settings_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        self.settings_button.setFixedSize(30, 30)
        self.settings_button.clicked.connect(self.open_settings)
        top_row.addWidget(self.settings_button)

        main_layout.addLayout(top_row)

        # select folder
        self.select_button = QPushButton("Select Folder")
        self.select_button.clicked.connect(self.select_folder)
        main_layout.addWidget(self.select_button)

        # webhook row
        webhook_row = QHBoxLayout()
        self.webhook_input = QLineEdit()
        self.webhook_input.setPlaceholderText("Enter Discord Webhook URL")
        self.webhook_input.setText(self.webhook_url)
        webhook_row.addWidget(self.webhook_input)

        self.toggle_webhook_button = QPushButton("Hide")
        self.toggle_webhook_button.setFixedSize(50, 30)
        self.toggle_webhook_button.clicked.connect(self.toggle_webhook_visibility)
        webhook_row.addWidget(self.toggle_webhook_button)

        main_layout.addLayout(webhook_row)

        if self.webhook_hidden:
            self.webhook_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_webhook_button.setText("Show")
        else:
            self.toggle_webhook_button.setText("Hide")

        self.save_webhook_button = QPushButton("Save Webhook")
        self.save_webhook_button.clicked.connect(self.save_webhook)
        main_layout.addWidget(self.save_webhook_button)

        self.monitor_button = QPushButton("Start Monitoring")
        self.monitor_button.clicked.connect(self.toggle_monitoring)
        main_layout.addWidget(self.monitor_button)

        
        
        
        

        for btn in [
        self.select_button,
        self.save_webhook_button,
        self.monitor_button
        ]:
            btn.setMinimumHeight(40)
            font = btn.font()
            font.setBold(True)
            btn.setFont(font)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        main_layout.addWidget(self.log_text)
        
        self.setLayout(main_layout)

    def open_settings(self):
        try:
            dlg = SettingsDialog(self.config)  # no parent to avoid odd crashes
            result = dlg.exec()
            if result == QDialog.DialogCode.Accepted:
                # reload config in case user changed things
                self.config = load_config()
        except Exception as e:
            print("[ERROR] Opening settings:", e)

    def open_discord_link(self):
        QDesktopServices.openUrl(QUrl("https://discord.gg/dMvCH93sYX"))

    def toggle_webhook_visibility(self):
        if self.webhook_hidden:
            self.webhook_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_webhook_button.setText("Hide")
            self.webhook_hidden = False
        else:
            self.webhook_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_webhook_button.setText("Show")
            self.webhook_hidden = True
        self.config["webhook_hidden"] = self.webhook_hidden
        save_config(self.config)

    def select_folder(self):
        folder_selected = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder_selected:
            self.watch_directory = folder_selected
            self.config["watch_directory"] = self.watch_directory
            save_config(self.config)
            self.dir_label.setText(f"Watching: {self.watch_directory}")

    def save_webhook(self):
        self.webhook_url = self.webhook_input.text()
        self.config["webhook_url"] = self.webhook_url
        save_config(self.config)
        self.log("Webhook saved.")

    def toggle_monitoring(self):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            # Stop monitoring properly before deleting thread
            self.monitoring_thread.stop()
            self.monitoring_thread.wait()
            self.monitoring_thread = None
            self.monitor_button.setText("Start Monitoring")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            delay_seconds = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_button.setText("Stop Monitoring")

    def closeEvent(self, event):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.stop()
            self.monitoring_thread.wait()
        if self.config.get("minimize_on_exit", False):
            self.hide()
            self.tray_icon.showMessage(
                "Steamcorder",
                "Steamcorder is still running in the background.",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
            event.ignore()
        else:
            super().closeEvent(event)
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            # Stop monitoring
            self.monitoring_thread.stop()
            self.monitoring_thread = None
            self.monitor_button.setText("Start Monitoring")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            delay_seconds = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_button.setText("Stop Monitoring")
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            # Stop monitoring
            self.monitoring_thread.stop()
            self.monitoring_thread = None
            self.monitor_button.setText("Start Monitoring")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            delay_seconds = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_button.setText("Stop Monitoring")
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            # Stop monitoring
            self.monitoring_thread.stop()
            self.monitoring_thread = None
            self.monitor_button.setText("Start Monitoring")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            delay_seconds = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_button.setText("Stop Monitoring")
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            # Stop monitoring
            self.monitoring_thread.stop()
            self.monitoring_thread = None
            self.monitor_button.setText("Start Monitoring")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            delay_seconds = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_button.setText("Stop Monitoring")
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.stop()
            self.monitoring_thread = None
            self.monitor_button.setText("Start Monitoring")
        else:
            if not self.webhook_url:
                QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
                return
            delay_seconds = self.config.get("upload_delay", 2)
            self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            self.monitor_button.setText("Stop Monitoring")
        if not self.webhook_url:
            QMessageBox.warning(self, "Error", "Please enter a webhook URL.")
            return
        delay_seconds = self.config.get("upload_delay", 2)
        self.monitoring_thread = MonitoringThread(self.watch_directory, self.webhook_url, delay_seconds)
        self.monitoring_thread.log_signal.connect(self.log)
        self.monitoring_thread.start()
        self.monitor_button.setText("Stop Monitoring")
        

    
        if self.monitoring_thread:
            self.monitoring_thread.stop()
        self.monitor_button.setText("Start Monitoring")
        self.stop_button.setEnabled(False)

    def log(self, message):
        self.log_text.append(message)
        print(message)

    def tray_icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.tray_menu.exec(QCursor.pos())
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.restore_window()

    def restore_window(self):
        self.showNormal()
        self.activateWindow()

    def closeEvent(self, event):
        if self.config.get("minimize_on_exit", False):
            self.hide()
            self.tray_icon.showMessage(
                "Steamcorder",
                "Steamcorder is still running in the background.",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
            event.ignore()
        else:
            super().closeEvent(event)

    def quit_app(self):
        self.tray_icon.hide()
        QApplication.quit()

import ctypes

if __name__ == "__main__":
    myappid = 'steamcorder.v0.4.1'  # Unique identifier for Windows taskbar
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("icon.ico")))

    if not QApplication.instance():
        app = QApplication(sys.argv)

    window = ScreenshotUploaderApp()
    window.show()
    sys.exit(app.exec())


#Hello Feys