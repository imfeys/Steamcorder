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
    QGroupBox, QSpinBox, QCheckBox, QSystemTrayIcon, QMenu, QStyle
)
from PyQt6.QtGui import QFont, QIcon, QCursor, QDesktopServices
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer

# ----------------------------------------------------------------------
# Style Sheet for Dark Theme
# ----------------------------------------------------------------------
STYLE_SHEET = """
QWidget {
    background-color: #282a36;
    color: #f8f8f2;
    font-family: Segoe UI;
    font-size: 10pt;
}
QMainWindow {
    border-image: none;
}
QTabWidget::pane {
    border: 1px solid #44475a;
    border-radius: 4px;
}
QTabBar::tab {
    background: #44475a;
    color: #f8f8f2;
    border: 1px solid #282a36;
    border-bottom: none;
    padding: 8px 20px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #6272a4;
    font-weight: bold;
}
QTabBar::tab:hover {
    background: #7081b5;
}
QGroupBox {
    background-color: #383a59;
    border: 1px solid #44475a;
    border-radius: 5px;
    margin-top: 1ex;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    color: #bd93f9;
}
QLineEdit, QTextEdit, QSpinBox {
    background-color: #44475a;
    color: #f8f8f2;
    border: 1px solid #6272a4;
    border-radius: 4px;
    padding: 5px;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus {
    border: 1px solid #bd93f9;
}
QPushButton {
    background-color: #6272a4;
    color: #f8f8f2;
    border: none;
    border-radius: 4px;
    padding: 8px 12px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #7081b5;
}
QPushButton:pressed {
    background-color: #53618c;
}
QCheckBox {
    spacing: 8px;
    padding: 2px 0;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #f8f8f2;
    background-color: transparent;
}
QCheckBox::indicator:hover {
    border: 1px solid #bd93f9;
}
QCheckBox::indicator:checked {
    background-color: #50fa7b;
    border: 1px solid #50fa7b;
}
QMessageBox {
    background-color: #383a59;
}
"""

# ----------------------------------------------------------------------
# Helper function to get resource paths
# ----------------------------------------------------------------------
def resource_path(relative_path):
    """Get the absolute path to a resource, works in development and with PyInstaller"""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

# ----------------------------------------------------------------------
# Config file handling
# ----------------------------------------------------------------------
CONFIG_FILE = "config.json"

def get_config_path():
    """Gets the full path to the configuration file."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE)

def load_config():
    """Loads the configuration from config.json."""
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_config(config):
    """Saves the configuration to config.json."""
    config_path = get_config_path()
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def update_startup_registry(enable):
    """Adds or removes the application from the Windows startup registry."""
    app_name = "Steamcorder"
    app_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
    reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{app_path}"')
            else:
                winreg.DeleteValue(key, app_name)
    except FileNotFoundError:
        if enable:
            print(f"Could not find registry key: HKEY_CURRENT_USER\\{reg_key}")
    except Exception as e:
        print(f"Error updating startup registry: {e}")

def show_silent_message(parent, icon, title, text):
    """Creates and shows a QMessageBox without playing the system sound."""
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle(title)
    msg_box.setText(text)
    msg_box.setTextFormat(Qt.TextFormat.MarkdownText)

    std_icon = None
    if icon == QMessageBox.Icon.Information:
        std_icon = QStyle.StandardPixmap.SP_MessageBoxInformation
    elif icon == QMessageBox.Icon.Warning:
        std_icon = QStyle.StandardPixmap.SP_MessageBoxWarning
    elif icon == QMessageBox.Icon.Critical:
        std_icon = QStyle.StandardPixmap.SP_MessageBoxCritical
    
    if std_icon:
        pixmap = QApplication.style().standardIcon(std_icon).pixmap(32, 32)
        msg_box.setIconPixmap(pixmap)

    msg_box.exec()

# ----------------------------------------------------------------------
# File monitoring classes
# ----------------------------------------------------------------------
class FileHandler(FileSystemEventHandler):
    """Handles file system events from watchdog."""
    def __init__(self, log_callback, webhook_url, delay_seconds, delete_after_upload):
        super().__init__()
        self.log_callback = log_callback
        self.webhook_url = webhook_url
        self.delay_seconds = delay_seconds
        self.delete_after_upload = delete_after_upload

    def on_created(self, event):
        if event.is_directory:
            return
        
        file_path = event.src_path
        
        try:
            last_size = -1
            stable_checks = 0
            while stable_checks < 3:
                if not os.path.exists(file_path):
                    self.log_callback.emit(f"File '{os.path.basename(file_path)}' was removed before processing.", "warning")
                    return
                current_size = os.path.getsize(file_path)
                if current_size == last_size and current_size > 0:
                    stable_checks += 1
                else:
                    stable_checks = 0
                last_size = current_size
                time.sleep(0.5)
        except FileNotFoundError:
             self.log_callback.emit(f"File '{os.path.basename(file_path)}' vanished during stability check.", "warning")
             return

        creation_time = time.time()
        self.log_callback.emit(f"Detected: {os.path.basename(file_path)}", "info")

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
            
        if self.is_allowed_file(file_path):
            self.upload_file(file_path, creation_time)
        else:
            self.log_callback.emit(f"Ignored '{os.path.basename(file_path)}' (unsupported type).", "warning")

    def is_allowed_file(self, file_path):
        return os.path.splitext(file_path)[1].lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}

    def upload_file(self, file_path, creation_time):
        if not self.webhook_url:
            self.log_callback.emit("No webhook URL set in Settings!", "error")
            return
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()

            files = {'file': (os.path.basename(file_path), file_data)}
            start_request = time.perf_counter()
            response = requests.post(self.webhook_url, files=files)
            upload_duration = time.perf_counter() - start_request
            total_duration = time.time() - creation_time
            
            response.raise_for_status()

            self.log_callback.emit(f"Uploaded in {upload_duration:.2f}s (total: {total_duration:.2f}s).", "success")
            
            if self.delete_after_upload:
                try:
                    os.remove(file_path)
                    self.log_callback.emit("Deleted screenshot after upload.", "info")
                except OSError as e:
                    self.log_callback.emit(f"Error deleting file: {e}", "error")
        except requests.exceptions.RequestException as e:
            self.log_callback.emit(f"Upload failed: {e}", "error")
        except FileNotFoundError:
             self.log_callback.emit(f"Could not find {file_path} for upload.", "error")
        except Exception as e:
            self.log_callback.emit(f"An unexpected error occurred during upload: {e}", "error")


class MonitoringThread(QThread):
    log_signal = pyqtSignal(str, str) # message, level

    def __init__(self, watch_directory, webhook_url, delay_seconds, delete_after_upload):
        super().__init__()
        self.watch_directory = watch_directory
        self.webhook_url = webhook_url
        self.delay_seconds = delay_seconds
        self.delete_after_upload = delete_after_upload
        self.observer = None
        self.file_handler = None

    def run(self):
        self.log_signal.emit(f"Monitoring started for: {self.watch_directory}", "info")
        self.file_handler = FileHandler(self.log_signal, self.webhook_url, self.delay_seconds, self.delete_after_upload)
        self.observer = Observer()
        self.observer.schedule(self.file_handler, path=self.watch_directory, recursive=False)
        self.observer.start()
        self.observer.join()

    def stop(self):
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
            self.log_signal.emit("Monitoring stopped.", "info")

    def update_delay(self, new_delay):
        if self.file_handler:
            self.file_handler.delay_seconds = new_delay

    def update_delete_option(self, delete_enabled):
        if self.file_handler:
            self.file_handler.delete_after_upload = delete_enabled


# ----------------------------------------------------------------------
# Settings tab widget
# ----------------------------------------------------------------------
class SettingsTab(QWidget):
    settings_updated = pyqtSignal(int)
    delete_option_changed = pyqtSignal(bool)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # --- Setup Group ---
        setup_group = QGroupBox("Setup")
        setup_layout = QVBoxLayout(setup_group)
        main_layout.addWidget(setup_group)
        
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("Folder Path:"))
        self.folder_edit = QLineEdit(self.config.get("watch_directory", ""))
        self.folder_edit.setReadOnly(True)
        folder_browse_btn = QPushButton("Browse")
        folder_browse_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        folder_browse_btn.clicked.connect(self.select_folder)
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(folder_browse_btn)
        setup_layout.addLayout(folder_layout)

        webhook_layout = QHBoxLayout()
        webhook_layout.addWidget(QLabel("Webhook URL:"))
        self.webhook_edit = QLineEdit(self.config.get("webhook_url", ""))
        if self.config.get("webhook_hidden", False):
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle_webhook_btn = QPushButton("Show" if self.config.get("webhook_hidden", False) else "Hide")
        self.toggle_webhook_btn.clicked.connect(self.toggle_webhook_visibility)
        webhook_test_btn = QPushButton("Test")
        webhook_test_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        webhook_test_btn.clicked.connect(self.test_webhook)
        webhook_layout.addWidget(self.webhook_edit, 1)
        webhook_layout.addWidget(self.toggle_webhook_btn)
        webhook_layout.addWidget(webhook_test_btn)
        setup_layout.addLayout(webhook_layout)

        # --- Options Group ---
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)
        main_layout.addWidget(options_group)

        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel("Upload Delay (seconds):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setValue(self.config.get("upload_delay", 2))
        delay_layout.addStretch(1)
        delay_layout.addWidget(self.delay_spin)
        options_layout.addLayout(delay_layout)

        self.delete_cb = QCheckBox("Delete files after successful upload")
        self.delete_cb.setChecked(self.config.get("delete_after_upload", False))
        options_layout.addWidget(self.delete_cb)
        self.minimize_cb = QCheckBox("Minimize to system tray on close")
        self.minimize_cb.setChecked(self.config.get("minimize_on_exit", False))
        options_layout.addWidget(self.minimize_cb)
        self.startup_cb = QCheckBox("Start with Windows")
        self.startup_cb.setChecked(self.config.get("start_on_startup", False))
        options_layout.addWidget(self.startup_cb)

        # --- Actions & Links ---
        actions_layout = QHBoxLayout()
        self.how_to_btn = QPushButton("How to Use")
        self.how_to_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogHelpButton))
        self.how_to_btn.clicked.connect(self.show_how_to)
        self.discord_btn = QPushButton("Join Discord for Support")
        self.discord_btn.clicked.connect(self.join_discord)
        actions_layout.addWidget(self.how_to_btn)
        actions_layout.addWidget(self.discord_btn)
        main_layout.addLayout(actions_layout)

        main_layout.addStretch(1)

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.clicked.connect(self.save_all_settings)
        main_layout.addWidget(self.save_btn)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Screenshot Folder")
        if folder:
            self.folder_edit.setText(folder)

    def toggle_webhook_visibility(self):
        if self.webhook_edit.echoMode() == QLineEdit.EchoMode.Normal:
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_webhook_btn.setText("Show")
        else:
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_webhook_btn.setText("Hide")

    def test_webhook(self):
        url = self.webhook_edit.text()
        if not url.startswith("https://discord.com/api/webhooks/"):
            show_silent_message(self, QMessageBox.Icon.Warning, "Invalid URL", "Please enter a valid Discord webhook URL.")
            return
        
        original_text = self.save_btn.text()
        self.save_btn.setText("Testing...")
        self.save_btn.setEnabled(False)
        QApplication.processEvents()

        payload = {"content": "✅ **Steamcorder: Webhook test successful!**"}
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            show_silent_message(self, QMessageBox.Icon.Information, "Success", "Test message sent to your Discord channel.")
        except requests.exceptions.RequestException as e:
            show_silent_message(self, QMessageBox.Icon.Critical, "Webhook Test Failed", f"Failed to send test message.\n\nError: {e}")
        finally:
            self.save_btn.setText(original_text)
            self.save_btn.setEnabled(True)

    def show_how_to(self):
        instructions = (
            "### How to Use\n\n"
            "**1. Enable Uncompressed Screenshots in Steam**\n"
            "Go to `Steam > Settings > In-Game` and check `Save an uncompressed copy of my screenshots`.\n\n"
            "**2. Select the Screenshot Folder**\n"
            "Use the `Browse` button in this app to select the folder where Steam saves these screenshots.\n\n"
            "**3. Get a Discord Webhook URL**\n"
            "In your Discord server, go to `Server Settings > Integrations > Webhooks`, create one, and copy its URL.\n\n"
            "**4. Final Steps**\n"
            "Paste the URL in this app, click `Save Settings`, and then `Start Monitoring` from the Dashboard."
        )
        show_silent_message(self, None, "How to Use Steamcorder", instructions)

    def save_all_settings(self):
        self.config["watch_directory"] = self.folder_edit.text()
        self.config["webhook_url"] = self.webhook_edit.text()
        self.config["webhook_hidden"] = (self.webhook_edit.echoMode() == QLineEdit.EchoMode.Password)
        self.config["upload_delay"] = self.delay_spin.value()
        self.config["minimize_on_exit"] = self.minimize_cb.isChecked()
        self.config["start_on_startup"] = self.startup_cb.isChecked()
        self.config["delete_after_upload"] = self.delete_cb.isChecked()
        
        save_config(self.config)
        update_startup_registry(self.startup_cb.isChecked())
        
        self.save_btn.setText("✓ Settings Saved!")
        self.save_btn.setEnabled(False)
        QTimer.singleShot(2000, lambda: (self.save_btn.setText("Save Settings"), self.save_btn.setEnabled(True)))
        
        self.settings_updated.emit(self.delay_spin.value())
        self.delete_option_changed.emit(self.delete_cb.isChecked())

    def join_discord(self):
        QDesktopServices.openUrl(QUrl("https://discord.gg/dMvCH93sYX"))


# ----------------------------------------------------------------------
# Main application window
# ----------------------------------------------------------------------
class SteamcorderMainWindow(QMainWindow):
    APP_VERSION = "v0.8.6"

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.monitoring_thread = None
        
        self.init_tray_icon()
        self.init_ui()
        
        self.settings_tab.settings_updated.connect(self.update_monitoring_delay)
        self.settings_tab.delete_option_changed.connect(self.update_monitoring_delete_option)

        # Automatically start monitoring on launch if it was active before closing
        if self.config.get("monitoring_active", False):
            if self.config.get("webhook_url") and self.config.get("watch_directory"):
                self.toggle_monitoring()

    def init_tray_icon(self):
        icon_path = resource_path("icon.ico")
        self.tray_icon = QSystemTrayIcon(QIcon(icon_path), self)
        self.tray_icon.setToolTip(f"Steamcorder {self.APP_VERSION}")
        
        self.tray_menu = QMenu(self)
        restore_action = self.tray_menu.addAction("Restore Window")
        restore_action.triggered.connect(self.showNormal)
        quit_action = self.tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_clicked)
        self.tray_icon.show()

    def init_ui(self):
        self.setWindowTitle(f"Steamcorder {self.APP_VERSION}")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.setGeometry(100, 100, 550, 580)
        self.setMinimumSize(500, 540)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        central_layout = QVBoxLayout(central_widget)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_dashboard_tab(), "Dashboard")
        self.settings_tab = SettingsTab(self.config)
        self.tabs.addTab(self.settings_tab, "Settings")
        central_layout.addWidget(self.tabs)

        # --- Credits Label ---
        credits_label = QLabel("Created by @imfeys")
        credits_label.setFont(QFont("Segoe UI", 8))
        credits_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        credits_label.setStyleSheet("color: #6272a4; padding-top: 5px;") 
        central_layout.addWidget(credits_label)

    def create_dashboard_tab(self):
        dashboard_widget = QWidget()
        layout = QVBoxLayout(dashboard_widget)
        
        monitor_group = QGroupBox("Monitoring Control")
        monitor_layout = QVBoxLayout(monitor_group)
        self.monitor_btn = QPushButton("Start Monitoring")
        self.monitor_btn.setFixedHeight(50)
        self.monitor_btn.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        monitor_layout.addWidget(self.monitor_btn)
        
        self.status_label = QLabel()
        self.set_status_label(False) # Set initial status to idle/stopped
        self.status_label.setFont(QFont("Segoe UI", 12))
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        monitor_layout.addWidget(self.status_label)
        layout.addWidget(monitor_group)

        log_group = QGroupBox("Logs")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group, 1)

        return dashboard_widget

    def update_monitoring_delay(self, new_delay):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.update_delay(new_delay)

    def update_monitoring_delete_option(self, delete_enabled):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.update_delete_option(delete_enabled)

    def set_status_label(self, is_running):
        """Updates the status label with colored dot indicator."""
        if is_running:
            text = 'Status: <span style="color: #50fa7b;">●</span> Running'
        else:
            text = 'Status: <span style="color: #ff5555;">●</span> Stopped'
        self.status_label.setText(text)
        
    def toggle_monitoring(self):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.stop()
            self.monitoring_thread = None
            self.monitor_btn.setText("Start Monitoring")
            self.set_status_label(False)
            self.config["monitoring_active"] = False
        else:
            self.config = load_config()
            watch_dir = self.config.get("watch_directory")
            webhook_url = self.config.get("webhook_url")

            if not watch_dir or not os.path.isdir(watch_dir):
                show_silent_message(self, QMessageBox.Icon.Warning, "Error", "Please select a valid folder in Settings.")
                return
            if not webhook_url:
                show_silent_message(self, QMessageBox.Icon.Warning, "Error", "Please enter a webhook URL in Settings.")
                return

            delay = self.config.get("upload_delay", 2)
            delete_opt = self.config.get("delete_after_upload", False)
            
            self.monitoring_thread = MonitoringThread(watch_dir, webhook_url, delay, delete_opt)
            self.monitoring_thread.log_signal.connect(self.log)
            self.monitoring_thread.start()
            
            self.monitor_btn.setText("Stop Monitoring")
            self.set_status_label(True)
            self.config["monitoring_active"] = True

        # Save the updated monitoring state to the config file
        save_config(self.config)

    def log(self, message, level="info"):
        color_map = {
            "info": "#8be9fd",
            "success": "#50fa7b",
            "warning": "#f1fa8c",
            "error": "#ff5555"
        }
        color = color_map.get(level, "#f8f8f2")
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.append(f'<span style="color: #bd93f9;">[{timestamp}]</span> <span style="color: {color};">{message}</span>')

    def tray_icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.activateWindow()

    def closeEvent(self, event):
        """
        Handles the window close event.
        If 'minimize_on_exit' is true, it hides the window.
        Otherwise, it quits the application without changing the 'monitoring_active' setting.
        """
        if self.config.get("minimize_on_exit", False):
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Steamcorder is still running",
                "Right-click the tray icon to quit.",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            event.accept()
            self.quit_app()

    def quit_app(self):
        """
        Gracefully stops the monitoring thread and exits the application.
        This is the central point for quitting the app, ensuring cleanup happens correctly.
        """
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.monitoring_thread.stop()
        self.tray_icon.hide()
        QApplication.quit()

# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
def main():
    """The main function where the application starts."""
    # Enable high-DPI scaling for better multi-monitor support
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    myappid = f'imfeys.Steamcorder.{SteamcorderMainWindow.APP_VERSION}'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    
    window = SteamcorderMainWindow()

    # Hide window on startup if it's supposed to start minimized and monitoring
    if window.config.get("minimize_on_exit", False) and window.config.get("monitoring_active", False):
         window.hide()
    else:
        window.show()
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
