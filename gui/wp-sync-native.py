#!/usr/bin/python3
"""
WordPress Sync Native GUI
A native desktop application using PyQt5
"""

# Version - should match setup.py
APP_VERSION = "1.2.2"
GITHUB_REPO_OWNER = "extreemsnel"  # Change this to your GitHub username/org
GITHUB_REPO_NAME = "webmix-sync-starter"  # Change this to your repo name

import sys
import subprocess
import threading
import json
import base64
from pathlib import Path
import os
import time
import shutil
from datetime import datetime, timedelta
import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTextEdit, QCheckBox, QMessageBox,
    QGroupBox, QFrame, QDialog, QLineEdit, QFormLayout, QDialogButtonBox,
    QFileDialog, QPlainTextEdit, QMenuBar, QAction, QTabWidget, QSpinBox,
    QListWidget, QListWidgetItem, QProgressDialog, QSystemTrayIcon, QMenu
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess
from PyQt5.QtGui import QFont, QTextCursor, QIcon, QTextCharFormat, QColor

# macOS native menubar support
try:
    from AppKit import NSStatusBar, NSMenu, NSMenuItem, NSVariableStatusItemLength
    MACOS_STATUSBAR_AVAILABLE = True
except ImportError:
    MACOS_STATUSBAR_AVAILABLE = False

try:
    from update_checker import UpdateChecker
    UPDATE_CHECKER_AVAILABLE = True
except ImportError:
    UPDATE_CHECKER_AVAILABLE = False

class SettingsManager:
    """Manage application settings"""
    
    def __init__(self, project_root):
        self.project_root = Path(project_root)
        # Store settings in user's Application Support directory (persists across app updates)
        self.settings_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Starter"
        self.settings_file = self.settings_dir / "app-settings.json"
        self.settings = self.load_settings()
    
    def load_settings(self):
        """Load settings from file"""
        defaults = {
            "wp_username": "",
            "wp_app_password": "",
            "ssh_key_path": "~/.ssh/id_rsa",
            "default_local_root": "~/Sites",
            "default_sync_items": "themes\nplugins",
            "ssh_port": 22,
            "authenticated": False,
            "preferred_editor_path": "auto",  # "auto", "finder", or custom path
            "default_debounce_seconds": 3  # Default debounce time for watch mode
        }
        
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r') as f:
                    loaded = json.load(f)
                    defaults.update(loaded)
            except Exception as e:
                print(f"Error loading settings: {e}")
        
        return defaults
    
    def save_settings(self):
        """Save settings to file"""
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving settings: {e}")
            return False
    
    def get(self, key, default=None):
        """Get a setting value"""
        return self.settings.get(key, default)
    
    def set(self, key, value):
        """Set a setting value"""
        self.settings[key] = value
    
    def is_authenticated(self):
        """Check if WordPress credentials are set and authenticated"""
        return self.settings.get('authenticated', False)

class AuthThread(QThread):
    """Thread for WordPress authentication"""
    auth_result = pyqtSignal(bool, str)
    
    def __init__(self, wp_url, username, app_password):
        super().__init__()
        self.wp_url = wp_url
        self.username = username
        self.app_password = app_password
    
    def run(self):
        try:
            # Remove spaces from application password
            clean_password = self.app_password.replace(' ', '')
            
            # Create basic auth header
            credentials = f"{self.username}:{clean_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded}',
                'Content-Type': 'application/json'
            }
            
            # Test authentication with WordPress REST API
            url = f"{self.wp_url.rstrip('/')}/wp-json/wp/v2/users/me"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                self.auth_result.emit(True, f"Authenticated as {user_data.get('name', self.username)}")
            else:
                self.auth_result.emit(False, f"Authentication failed: {response.status_code}")
                
        except requests.exceptions.Timeout:
            self.auth_result.emit(False, "Connection timeout - check your URL")
        except requests.exceptions.ConnectionError:
            self.auth_result.emit(False, "Cannot connect to WordPress site")
        except Exception as e:
            self.auth_result.emit(False, f"Error: {str(e)}")

class FetchSitesThread(QThread):
    """Thread for fetching sites from WordPress API"""
    sites_result = pyqtSignal(bool, object, str)  # success, data, message
    
    def __init__(self, wp_url, username, app_password):
        super().__init__()
        self.wp_url = wp_url
        self.username = username
        self.app_password = app_password
    
    def run(self):
        try:
            # Remove spaces from application password
            clean_password = self.app_password.replace(' ', '')
            
            # Create basic auth header
            credentials = f"{self.username}:{clean_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded}',
                'Content-Type': 'application/json'
            }
            
            # Fetch cPanel sites from API
            url = f"{self.wp_url.rstrip('/')}/wp-json/webmix/v1/cpanels"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                sites_data = response.json()
                self.sites_result.emit(True, sites_data, f"Loaded {len(sites_data)} sites from API")
            else:
                self.sites_result.emit(False, None, f"API request failed: {response.status_code}")
                
        except requests.exceptions.Timeout:
            self.sites_result.emit(False, None, "Connection timeout")
        except requests.exceptions.ConnectionError:
            self.sites_result.emit(False, None, "Cannot connect to API")
        except Exception as e:
            self.sites_result.emit(False, None, f"Error: {str(e)}")

class PermissionsThread(QThread):
    """Thread for running permissions commands via SSH"""
    output_signal = pyqtSignal(str, str)  # message, level (info/success/error)
    finished_signal = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, ssh_command, operation_name):
        super().__init__()
        self.ssh_command = ssh_command
        self.operation_name = operation_name
        self.timeout = 600 if operation_name == "close" else 300
    
    def run(self):
        try:
            result = subprocess.run(
                self.ssh_command,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            if result.returncode == 0:
                self.output_signal.emit(f"✅ Rights {self.operation_name}ed successfully!\n", 'success')
                if result.stdout:
                    self.output_signal.emit(result.stdout, 'info')
                self.finished_signal.emit(True, "Success")
            else:
                if self.operation_name == "close":
                    self.output_signal.emit(f"⚠️ Completed with exit code {result.returncode}\n", 'info')
                    self.output_signal.emit("Some commands may have failed (normal if paths don't exist)\n", 'info')
                else:
                    self.output_signal.emit(f"❌ Error {self.operation_name}ing rights (exit code {result.returncode})\n", 'error')
                
                if result.stderr:
                    self.output_signal.emit(result.stderr, 'error')
                if result.stdout:
                    self.output_signal.emit(result.stdout, 'info')
                
                self.finished_signal.emit(result.returncode == 0, f"Exit code: {result.returncode}")
                
        except subprocess.TimeoutExpired:
            self.output_signal.emit(f"❌ Operation timed out ({self.timeout // 60} minutes)\n", 'error')
            self.finished_signal.emit(False, "Timeout")
        except FileNotFoundError:
            self.output_signal.emit("❌ SSH command not found. Please ensure SSH is installed.\n", 'error')
            self.finished_signal.emit(False, "SSH not found")
        except Exception as e:
            self.output_signal.emit(f"❌ Error: {str(e)}\n", 'error')
            self.finished_signal.emit(False, str(e))

class SSHTerminalDialog(QDialog):
    """Embedded SSH terminal dialog"""
    
    def __init__(self, ssh_command, site_name, parent=None):
        super().__init__(parent)
        self.ssh_command = ssh_command
        self.site_name = site_name
        self.process = None
        self.init_ui()
        self.start_ssh()
    
    def __del__(self):
        """Destructor - ensure process cleanup"""
        try:
            self.cleanup_process()
        except:
            pass  # Ignore errors during destruction
    
    def init_ui(self):
        """Initialize the UI"""
        self.setWindowTitle(f"SSH Terminal - {self.site_name}")
        self.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(self)
        
        # Terminal output
        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setFont(QFont("Monaco", 11))
        self.terminal_output.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
            }
        """)
        layout.addWidget(self.terminal_output)
        
        # Input area
        input_layout = QHBoxLayout()
        self.input_line = QLineEdit()
        self.input_line.setFont(QFont("Monaco", 11))
        self.input_line.setPlaceholderText("Type command and press Enter...")
        self.input_line.returnPressed.connect(self.send_command)
        input_layout.addWidget(self.input_line)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_command)
        input_layout.addWidget(self.send_btn)
        
        layout.addLayout(input_layout)
        
        # Status and close
        button_layout = QHBoxLayout()
        self.status_label = QLabel("Connecting...")
        button_layout.addWidget(self.status_label)
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close_terminal)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
    
    def start_ssh(self):
        """Start the SSH process"""
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        
        # Parse SSH command
        cmd_parts = self.ssh_command.split()
        program = cmd_parts[0]
        args = cmd_parts[1:]
        
        self.append_output(f"Connecting to {self.site_name}...\n")
        self.append_output(f"$ {self.ssh_command}\n\n")
        
        self.process.start(program, args)
        
        if self.process.waitForStarted():
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("color: green;")
            self.input_line.setFocus()
        else:
            self.status_label.setText("Failed to start")
            self.status_label.setStyleSheet("color: red;")
    
    def handle_output(self):
        """Handle output from SSH process"""
        data = self.process.readAllStandardOutput()
        text = bytes(data).decode('utf-8', errors='replace')
        self.append_output(text)
    
    def append_output(self, text):
        """Append text to terminal output"""
        self.terminal_output.moveCursor(QTextCursor.End)
        self.terminal_output.insertPlainText(text)
        self.terminal_output.moveCursor(QTextCursor.End)
    
    def send_command(self):
        """Send command to SSH process"""
        if not self.process or self.process.state() != QProcess.Running:
            QMessageBox.warning(self, "Not Connected", "SSH connection is not active.")
            return
        
        command = self.input_line.text()
        if command:
            # Echo the command locally
            self.append_output(f"$ {command}\n")
            
            # Send to SSH process
            self.process.write(f"{command}\n".encode())
            self.input_line.clear()
    
    def on_process_finished(self, exit_code, exit_status):
        """Handle process termination"""
        self.append_output(f"\n[Connection closed - exit code: {exit_code}]\n")
        self.status_label.setText("Disconnected")
        self.status_label.setStyleSheet("color: red;")
        self.input_line.setEnabled(False)
        self.send_btn.setEnabled(False)
    
    def on_process_error(self, error):
        """Handle process errors"""
        error_messages = {
            QProcess.FailedToStart: "Failed to start SSH",
            QProcess.Crashed: "SSH process crashed",
            QProcess.Timedout: "SSH process timed out",
            QProcess.WriteError: "Write error",
            QProcess.ReadError: "Read error",
            QProcess.UnknownError: "Unknown error"
        }
        msg = error_messages.get(error, "Unknown error")
        self.append_output(f"\n[ERROR: {msg}]\n")
        self.status_label.setText("Error")
        self.status_label.setStyleSheet("color: red;")
    
    def close_terminal(self):
        """Close the terminal and SSH connection"""
        self.cleanup_process()
        self.close()
    
    def cleanup_process(self):
        """Clean up the SSH process"""
        if not self.process:
            return
        
        # Block all signals to prevent interference during cleanup
        self.process.blockSignals(True)
        
        # Force kill the process if running
        if self.process.state() == QProcess.Running:
            try:
                # Try exit first
                self.process.write(b"exit\n")
                self.process.closeWriteChannel()
                
                # Wait briefly for graceful exit
                if not self.process.waitForFinished(1000):
                    # Force terminate
                    self.process.terminate()
                    
                    # Wait for termination
                    if not self.process.waitForFinished(2000):
                        # Force kill as last resort
                        self.process.kill()
                        # Must wait for kill to complete
                        self.process.waitForFinished(3000)
            except:
                # If anything fails, force kill
                try:
                    self.process.kill()
                    self.process.waitForFinished(3000)
                except:
                    pass
    
    def closeEvent(self, event):
        """Handle dialog close"""
        # Ensure process is fully stopped before closing
        self.cleanup_process()
        # Let the dialog close - process will be destroyed with it
        event.accept()

class RemoteFolderSelectorDialog(QDialog):
    """Dialog to fetch and select folders from remote server with navigation"""
    
    def __init__(self, ssh_host, ssh_port, ssh_user, remote_path, settings_manager, parent=None):
        super().__init__(parent)
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.base_remote_path = remote_path  # Base path (never changes)
        self.current_path = remote_path  # Current navigation path
        self.settings_manager = settings_manager
        self.selected_items = []
        
        self.setWindowTitle("Select Remote Folders/Files")
        self.setMinimumSize(600, 500)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Info label showing base path
        info_label = QLabel(
            f"Browsing: <b>{self.ssh_user}@{self.ssh_host}</b>"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Navigation bar with current path and up button
        nav_layout = QHBoxLayout()
        
        # Up button
        self.up_btn = QPushButton("⬆ Up")
        self.up_btn.setToolTip("Go to parent directory")
        self.up_btn.clicked.connect(self.go_up)
        self.up_btn.setEnabled(False)
        nav_layout.addWidget(self.up_btn)
        
        # Current path display
        self.path_label = QLabel(f"<b>{self.current_path}</b>")
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("padding: 5px; background-color: #f0f0f0; border-radius: 3px;")
        nav_layout.addWidget(self.path_label, 1)
        
        layout.addLayout(nav_layout)
        
        # List widget for folders/files
        self.items_list = QListWidget()
        self.items_list.setSelectionMode(QListWidget.MultiSelection)
        self.items_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.items_list)
        
        # Help text
        help_label = QLabel("💡 Double-click folders to navigate, select items and click OK to add them")
        help_label.setStyleSheet("color: #666; font-style: italic; font-size: 11px;")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        
        # Status label
        self.status_label = QLabel("Click 'Fetch' to load folders and files...")
        self.status_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.status_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.fetch_btn = QPushButton("Fetch Folders/Files")
        self.fetch_btn.clicked.connect(self.fetch_remote_items)
        button_layout.addWidget(self.fetch_btn)
        
        button_layout.addStretch()
        
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all_items)
        self.select_all_btn.setEnabled(False)
        button_layout.addWidget(self.select_all_btn)
        
        self.clear_btn = QPushButton("Clear Selection")
        self.clear_btn.clicked.connect(self.clear_selection)
        self.clear_btn.setEnabled(False)
        button_layout.addWidget(self.clear_btn)
        
        layout.addLayout(button_layout)
        
        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def go_up(self):
        """Navigate to parent directory"""
        # Get parent directory
        parent_path = str(Path(self.current_path).parent)
        
        # Don't go above the base path
        if parent_path == self.current_path or len(parent_path) < len(self.base_remote_path):
            return
        
        self.current_path = parent_path
        self.path_label.setText(f"<b>{self.current_path}</b>")
        
        # Enable/disable up button
        self.up_btn.setEnabled(self.current_path != self.base_remote_path)
        
        # Fetch items in new directory
        self.fetch_remote_items()
    
    def on_item_double_clicked(self, item):
        """Handle double-click on an item to navigate into folders"""
        item_name = item.data(Qt.UserRole)
        display_text = item.text()
        
        # Check if it's a folder (has folder icon)
        if display_text.startswith("📁"):
            # Navigate into this folder
            self.current_path = str(Path(self.current_path) / item_name)
            self.path_label.setText(f"<b>{self.current_path}</b>")
            
            # Enable up button since we're now inside a subfolder
            self.up_btn.setEnabled(True)
            
            # Fetch items in the new directory
            self.fetch_remote_items()
        
    def fetch_remote_items(self):
        """Fetch folders and files from remote server via SSH"""
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching...")
        self.status_label.setText("Connecting to server...")
        self.items_list.clear()
        
        # Build SSH command to list directories and files
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        # Command to list directories first, then files, with type indicator
        # Lists items in current_path: directories ending with /, regular files without
        list_cmd = f"cd '{self.current_path}' 2>/dev/null && (ls -1p 2>/dev/null || echo 'ERROR: Cannot access directory')"
        
        ssh_command = [
            'ssh',
            '-i', str(ssh_key_expanded),
            '-p', str(self.ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f'{self.ssh_user}@{self.ssh_host}',
            list_cmd
        ]
        
        try:
            result = subprocess.run(
                ssh_command,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                output = result.stdout.strip()
                
                if 'ERROR: Cannot access directory' in output:
                    self.status_label.setText("❌ Cannot access remote directory")
                    self.status_label.setStyleSheet("color: red;")
                    QMessageBox.warning(
                        self, "Access Error",
                        f"Cannot access directory:\n{self.current_path}\n\n"
                        "Please check the remote path and permissions."
                    )
                elif output:
                    items = [line.strip() for line in output.split('\n') if line.strip()]
                    
                    if items:
                        # Separate folders and files
                        folders = [item.rstrip('/') for item in items if item.endswith('/')]
                        files = [item for item in items if not item.endswith('/')]
                        
                        # Add folders first
                        for folder in sorted(folders):
                            item = QListWidgetItem(f"📁 {folder}")
                            item.setData(Qt.UserRole, folder)  # Store actual name without icon
                            self.items_list.addItem(item)
                        
                        # Add files
                        for file in sorted(files):
                            item = QListWidgetItem(f"📄 {file}")
                            item.setData(Qt.UserRole, file)  # Store actual name without icon
                            self.items_list.addItem(item)
                        
                        self.status_label.setText(
                            f"✓ Found {len(folders)} folder(s) and {len(files)} file(s). Select items and click OK."
                        )
                        self.status_label.setStyleSheet("color: green;")
                        self.select_all_btn.setEnabled(True)
                        self.clear_btn.setEnabled(True)
                    else:
                        self.status_label.setText("⚠️ Directory is empty")
                        self.status_label.setStyleSheet("color: orange;")
                else:
                    self.status_label.setText("⚠️ Directory is empty")
                    self.status_label.setStyleSheet("color: orange;")
            else:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                self.status_label.setText("❌ SSH connection failed")
                self.status_label.setStyleSheet("color: red;")
                QMessageBox.critical(
                    self, "Connection Error",
                    f"Failed to connect to server:\n{error_msg}"
                )
                
        except subprocess.TimeoutExpired:
            self.status_label.setText("❌ Connection timeout")
            self.status_label.setStyleSheet("color: red;")
            QMessageBox.critical(
                self, "Timeout",
                "Connection to server timed out.\n"
                "Please check your network and server settings."
            )
        except Exception as e:
            self.status_label.setText(f"❌ Error: {str(e)}")
            self.status_label.setStyleSheet("color: red;")
            QMessageBox.critical(
                self, "Error",
                f"An error occurred:\n{str(e)}"
            )
        finally:
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Refresh")
    
    def select_all_items(self):
        """Select all items in the list"""
        for i in range(self.items_list.count()):
            self.items_list.item(i).setSelected(True)
    
    def clear_selection(self):
        """Clear all selections"""
        self.items_list.clearSelection()
    
    def accept_selection(self):
        """Accept selected items with their relative paths"""
        selected_items = self.items_list.selectedItems()
        
        if not selected_items:
            QMessageBox.warning(
                self, "No Selection",
                "Please select at least one folder or file."
            )
            return
        
        # Get actual names (without icons) from UserRole data and calculate relative paths
        self.selected_items = []
        
        for item in selected_items:
            item_name = item.data(Qt.UserRole)
            
            # Calculate relative path from base_remote_path
            if self.current_path == self.base_remote_path:
                # We're at the base level, just use the item name
                relative_path = item_name
            else:
                # We're in a subdirectory, calculate relative path
                # Get the path relative to base
                current_relative = Path(self.current_path).relative_to(self.base_remote_path)
                relative_path = str(current_relative / item_name)
            
            self.selected_items.append(relative_path)
        
        self.accept()
    
    def get_selected_items(self):
        """Return list of selected folder/file names with relative paths"""
        return self.selected_items

class ConfigureSiteDialog(QDialog):
    """Dialog for configuring a site pulled from API"""
    
    def __init__(self, site_data, settings_manager, parent=None):
        super().__init__(parent)
        self.site_data = site_data
        self.settings_manager = settings_manager
        self.setWindowTitle(f"Configure Site: {site_data.get('title', 'Unknown')}")
        self.setMinimumWidth(600)
        self.init_ui()
        self.load_defaults()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Site info from API (read-only)
        info_group = QGroupBox("Site Information (from API)")
        info_layout = QFormLayout()
        
        site_title = self.site_data.get('title', 'Unknown')
        server_list = self.site_data.get('server', [])
        server_info = server_list[0] if server_list else {}
        server_name = server_info.get('title', 'Unknown')
        server_ip = server_info.get('ip', 'Unknown')
        
        info_layout.addRow("Site Name:", QLabel(f"<b>{site_title}</b>"))
        info_layout.addRow("Server:", QLabel(server_name))
        info_layout.addRow("Server IP:", QLabel(server_ip))
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # Configuration form
        config_group = QGroupBox("Sync Configuration")
        config_layout = QFormLayout()
        
        # SSH User
        self.ssh_user_input = QLineEdit()
        self.ssh_user_input.setPlaceholderText("e.g., cpanel-username")
        config_layout.addRow("SSH User:", self.ssh_user_input)
        
        # SSH Port
        self.ssh_port_input = QLineEdit()
        self.ssh_port_input.setPlaceholderText("22")
        config_layout.addRow("SSH Port:", self.ssh_port_input)
        
        # Local root with browse button
        local_layout = QHBoxLayout()
        self.local_root_input = QLineEdit()
        self.local_root_input.setPlaceholderText(f"~/Sites/{site_title}")
        local_layout.addWidget(self.local_root_input)
        browse_local_btn = QPushButton("Browse...")
        browse_local_btn.clicked.connect(self.browse_local_root)
        local_layout.addWidget(browse_local_btn)
        config_layout.addRow("Local Root:", local_layout)
        
        # Remote root
        self.remote_root_input = QLineEdit()
        self.remote_root_input.setPlaceholderText("e.g., /home/user/public_html/wp-content")
        config_layout.addRow("Remote Root:", self.remote_root_input)
        
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)
        
        # Sync items with browse button
        sync_items_header = QHBoxLayout()
        sync_items_header.addWidget(QLabel("Sync Items (one per line):"))
        sync_items_header.addStretch()
        browse_remote_btn = QPushButton("Browse Remote...")
        browse_remote_btn.setToolTip("Connect to server and select folders/files")
        browse_remote_btn.clicked.connect(self.browse_remote_folders)
        sync_items_header.addWidget(browse_remote_btn)
        layout.addLayout(sync_items_header)
        
        self.sync_items_input = QPlainTextEdit()
        self.sync_items_input.setPlaceholderText("themes\nplugins")
        self.sync_items_input.setMaximumHeight(100)
        layout.addWidget(self.sync_items_input)
        
        # Debounce seconds
        debounce_layout = QHBoxLayout()
        debounce_layout.addWidget(QLabel("Watch debounce (seconds):"))
        self.debounce_input = QSpinBox()
        self.debounce_input.setRange(1, 30)
        self.debounce_input.setValue(3)
        self.debounce_input.setToolTip("Time to wait before syncing after detecting changes")
        debounce_layout.addWidget(self.debounce_input)
        debounce_layout.addStretch()
        layout.addLayout(debounce_layout)
        
        # Delete option
        self.delete_check = QCheckBox("Enable delete sync (dangerous)")
        layout.addWidget(self.delete_check)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def load_defaults(self):
        """Load default values from settings"""
        site_title = self.site_data.get('title', '')
        server_list = self.site_data.get('server', [])
        server_ip = server_list[0].get('ip', '') if server_list else ''
        
        # Try to load existing config if it exists
        existing_config = self.load_existing_config(site_title)
        
        if existing_config:
            # Load from existing config
            self.ssh_user_input.setText(existing_config.get('SSH_USER', ''))
            self.ssh_port_input.setText(existing_config.get('SSH_PORT', '22'))
            self.local_root_input.setText(existing_config.get('LOCAL_ROOT', ''))
            self.remote_root_input.setText(existing_config.get('REMOTE_ROOT', ''))
            # Parse SYNC_ITEMS - handle both space-separated (old) and actual newlines (new)
            sync_items_raw = existing_config.get('SYNC_ITEMS', '')
            # If it contains spaces but no newlines, convert spaces to newlines (backwards compat)
            if ' ' in sync_items_raw and '\n' not in sync_items_raw:
                sync_items_raw = sync_items_raw.replace(' ', '\n')
            self.sync_items_input.setPlainText(sync_items_raw)
            self.delete_check.setChecked(existing_config.get('RSYNC_DELETE', '0') == '1')
            
            # Load debounce time
            debounce = existing_config.get('DEBOUNCE_SECONDS', '3')
            try:
                self.debounce_input.setValue(int(debounce))
            except ValueError:
                self.debounce_input.setValue(3)
        else:
            # Load defaults from settings
            # Auto-fill SSH user with site name
            self.ssh_user_input.setText(site_title)
            
            default_port = self.settings_manager.get('ssh_port', 22)
            self.ssh_port_input.setText(str(default_port))
            
            default_root = self.settings_manager.get('default_local_root', '~/Sites')
            suggested_local = f"{default_root}/{site_title}".replace('//', '/')
            self.local_root_input.setText(suggested_local)
            
            # Auto-fill remote root with standard cPanel path
            suggested_remote = f"/home/{site_title}/public_html/wp-content"
            self.remote_root_input.setText(suggested_remote)
            
            default_sync_items = self.settings_manager.get('default_sync_items', 'themes\nplugins')
            self.sync_items_input.setPlainText(default_sync_items)
            
            default_debounce = self.settings_manager.get('default_debounce_seconds', 3)
            self.debounce_input.setValue(int(default_debounce))
    
    def load_existing_config(self, site_key):
        """Try to load existing configuration for this site"""
        from pathlib import Path
        
        # Check Application Support directory first (bundled app or after migration)
        app_support_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Starter"
        config_file = app_support_dir / "sites" / f"{site_key}.env"
        
        # Fallback to old location (development mode)
        if not config_file.exists():
            if getattr(sys, 'frozen', False):
                config_file = Path(__file__).parent / "config" / "sites" / f"{site_key}.env"
            else:
                config_file = Path(__file__).parent.parent / "config" / "sites" / f"{site_key}.env"
        
        if not config_file.exists():
            return None
        
        config = {}
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Handle bash $'...' syntax for SYNC_ITEMS
                    if value.startswith("$'") and value.endswith("'"):
                        # Remove $' and trailing '
                        value = value[2:-1]
                        # Convert escaped newlines to actual newlines
                        value = value.replace('\\n', '\n')
                    else:
                        # Normal quoted value
                        value = value.strip('"')
                    
                    config[key] = value
        
        return config
    
    def browse_local_root(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Local Root Directory"
        )
        if folder:
            self.local_root_input.setText(folder)
    
    def browse_remote_folders(self):
        """Open dialog to browse and select remote folders/files"""
        # Get current connection settings
        ssh_host_list = self.site_data.get('server', [])
        ssh_host = ssh_host_list[0].get('ip', '') if ssh_host_list else ''
        ssh_user = self.ssh_user_input.text().strip()
        ssh_port = self.ssh_port_input.text().strip() or '22'
        remote_root = self.remote_root_input.text().strip()
        
        # Validate required fields
        if not ssh_host:
            QMessageBox.warning(
                self, "Missing Information",
                "Server IP is not available from API data."
            )
            return
        
        if not ssh_user:
            QMessageBox.warning(
                self, "Missing Information",
                "Please enter SSH User before browsing remote folders."
            )
            self.ssh_user_input.setFocus()
            return
        
        if not remote_root:
            QMessageBox.warning(
                self, "Missing Information",
                "Please enter Remote Root path before browsing."
            )
            self.remote_root_input.setFocus()
            return
        
        # Open folder selector dialog
        dialog = RemoteFolderSelectorDialog(
            ssh_host, ssh_port, ssh_user, remote_root,
            self.settings_manager, self
        )
        
        if dialog.exec_() == QDialog.Accepted:
            selected_items = dialog.get_selected_items()
            if selected_items:
                # Update sync items with selected folders/files
                current_text = self.sync_items_input.toPlainText().strip()
                
                # Parse existing items
                existing_items = set()
                if current_text:
                    existing_items = set(line.strip() for line in current_text.split('\n') if line.strip())
                
                # Add new items (avoid duplicates)
                for item in selected_items:
                    existing_items.add(item)
                
                # Update the text field
                self.sync_items_input.setPlainText('\n'.join(sorted(existing_items)))
    
    def validate_and_accept(self):
        if not self.ssh_user_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "SSH User is required")
            return
        if not self.local_root_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "Local Root is required")
            return
        if not self.remote_root_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "Remote Root is required")
            return
        if not self.sync_items_input.toPlainText().strip():
            QMessageBox.warning(self, "Validation Error", "At least one Sync Item is required")
            return
        
        self.accept()
    
    def get_config(self):
        """Return the configuration"""
        server_list = self.site_data.get('server', [])
        server_ip = server_list[0].get('ip', '') if server_list else ''
        
        # Convert multi-line sync items to newline-separated for bash
        sync_items_lines = self.sync_items_input.toPlainText().strip().split('\n')
        sync_items = '\n'.join([line.strip() for line in sync_items_lines if line.strip()])
        
        return {
            'site_key': self.site_data.get('title', ''),
            'ssh_host': server_ip,
            'ssh_port': self.ssh_port_input.text().strip(),
            'ssh_user': self.ssh_user_input.text().strip(),
            'local_root': self.local_root_input.text().strip(),
            'remote_root': self.remote_root_input.text().strip(),
            'sync_items': sync_items,
            'rsync_delete': '1' if self.delete_check.isChecked() else '0',
            'debounce_seconds': str(self.debounce_input.value())
        }

class SettingsDialog(QDialog):
    """Dialog for application settings"""
    
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        self.auth_thread = None
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Tabs for different settings categories
        tabs = QTabWidget()
        
        # WordPress Authentication Tab
        wp_tab = QWidget()
        wp_layout = QVBoxLayout(wp_tab)
        
        wp_layout.addWidget(QLabel("<b>WordPress Authentication</b>"))
        wp_layout.addWidget(QLabel("These credentials are required to use the application."))
        wp_layout.addSpacing(10)
        
        wp_form = QFormLayout()
        
        self.wp_url_input = QLineEdit()
        self.wp_url_input.setPlaceholderText("https://example.com")
        wp_form.addRow("WordPress URL:", self.wp_url_input)
        
        self.wp_username_input = QLineEdit()
        self.wp_username_input.setPlaceholderText("your-username")
        wp_form.addRow("Username:", self.wp_username_input)
        
        self.wp_password_input = QLineEdit()
        self.wp_password_input.setEchoMode(QLineEdit.Password)
        self.wp_password_input.setPlaceholderText("xxxx xxxx xxxx xxxx xxxx xxxx")
        wp_form.addRow("App Password:", self.wp_password_input)
        
        wp_layout.addLayout(wp_form)
        
        wp_layout.addSpacing(10)
        wp_layout.addWidget(QLabel(
            "<i>How to create an Application Password:</i><br>"
            "1. Go to your WordPress Admin → Users → Profile<br>"
            "2. Scroll to 'Application Passwords' section<br>"
            "3. Enter a name and click 'Add New Application Password'<br>"
            "4. Copy the generated password (spaces are optional)</i>"
        ))
        
        # Test authentication button
        test_btn_layout = QHBoxLayout()
        self.test_auth_btn = QPushButton("Test Authentication")
        self.test_auth_btn.clicked.connect(self.test_authentication)
        test_btn_layout.addWidget(self.test_auth_btn)
        test_btn_layout.addStretch()
        wp_layout.addLayout(test_btn_layout)
        
        self.auth_status_label = QLabel("")
        wp_layout.addWidget(self.auth_status_label)
        
        wp_layout.addStretch()
        tabs.addTab(wp_tab, "WordPress Auth")
        
        # SSH & Sync Settings Tab
        ssh_tab = QWidget()
        ssh_layout = QVBoxLayout(ssh_tab)
        
        ssh_layout.addWidget(QLabel("<b>SSH & Sync Settings</b>"))
        ssh_layout.addSpacing(10)
        
        ssh_form = QFormLayout()
        
        # SSH Key
        ssh_key_layout = QHBoxLayout()
        self.ssh_key_input = QLineEdit()
        self.ssh_key_input.setPlaceholderText("~/.ssh/id_rsa")
        ssh_key_layout.addWidget(self.ssh_key_input)
        browse_key_btn = QPushButton("Browse...")
        browse_key_btn.clicked.connect(self.browse_ssh_key)
        ssh_key_layout.addWidget(browse_key_btn)
        ssh_form.addRow("SSH Key Path:", ssh_key_layout)
        
        # Default SSH Port
        self.ssh_port_input = QSpinBox()
        self.ssh_port_input.setRange(1, 65535)
        self.ssh_port_input.setValue(22)
        ssh_form.addRow("Default SSH Port:", self.ssh_port_input)
        
        # Local Root
        local_root_layout = QHBoxLayout()
        self.local_root_input = QLineEdit()
        self.local_root_input.setPlaceholderText("~/Sites")
        local_root_layout.addWidget(self.local_root_input)
        browse_root_btn = QPushButton("Browse...")
        browse_root_btn.clicked.connect(self.browse_local_root)
        local_root_layout.addWidget(browse_root_btn)
        ssh_form.addRow("Default Local Root:", local_root_layout)
        
        ssh_layout.addLayout(ssh_form)
        
        # Default Sync Items
        ssh_layout.addSpacing(10)
        ssh_layout.addWidget(QLabel("Default Sync Items (one per line):"))
        self.sync_items_input = QPlainTextEdit()
        self.sync_items_input.setPlaceholderText("themes\nplugins")
        self.sync_items_input.setMaximumHeight(150)
        ssh_layout.addWidget(self.sync_items_input)
        
        ssh_layout.addSpacing(15)
        ssh_layout.addWidget(QLabel("<b>Editor Preferences</b>"))
        
        editor_form = QFormLayout()
        editor_layout = QHBoxLayout()
        self.editor_path_input = QLineEdit()
        self.editor_path_input.setPlaceholderText("auto (detect VS Code automatically)")
        editor_layout.addWidget(self.editor_path_input)
        
        browse_editor_btn = QPushButton("Browse...")
        browse_editor_btn.clicked.connect(self.browse_editor)
        editor_layout.addWidget(browse_editor_btn)
        
        editor_form.addRow("Code Editor:", editor_layout)
        ssh_layout.addLayout(editor_form)
        
        ssh_layout.addWidget(QLabel(
            "<i>Options:</i><br>"
            "• <b>auto</b> - Automatically detect VS Code<br>"
            "• <b>finder</b> - Always use Finder/default app<br>"
            "• Or enter custom path (e.g., /usr/local/bin/code)</i>"
        ))
        
        ssh_layout.addSpacing(15)
        ssh_layout.addWidget(QLabel("<b>Watch Mode Defaults</b>"))
        
        watch_form = QFormLayout()
        self.debounce_seconds_input = QSpinBox()
        self.debounce_seconds_input.setRange(1, 30)
        self.debounce_seconds_input.setValue(3)
        self.debounce_seconds_input.setSuffix(" sec")
        self.debounce_seconds_input.setToolTip("Default time to wait before syncing after detecting file changes")
        watch_form.addRow("Default Debounce:", self.debounce_seconds_input)
        ssh_layout.addLayout(watch_form)
        
        ssh_layout.addWidget(QLabel(
            "<i>This sets the default debounce time for new sites.<br>"
            "You can customize per-site when configuring each site.</i>"
        ))
        
        ssh_layout.addStretch()
        tabs.addTab(ssh_tab, "SSH & Sync")
        
        layout.addWidget(tabs)
        
        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        # Load current settings
        self.load_current_settings()
    
    def load_current_settings(self):
        """Load current settings into form"""
        self.wp_url_input.setText(self.settings_manager.get('wp_url', ''))
        self.wp_username_input.setText(self.settings_manager.get('wp_username', ''))
        self.wp_password_input.setText(self.settings_manager.get('wp_app_password', ''))
        self.ssh_key_input.setText(self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa'))
        self.ssh_port_input.setValue(self.settings_manager.get('ssh_port', 22))
        self.local_root_input.setText(self.settings_manager.get('default_local_root', '~/Sites'))
        self.sync_items_input.setPlainText(self.settings_manager.get('default_sync_items', 'themes\nplugins'))
        self.editor_path_input.setText(self.settings_manager.get('preferred_editor_path', 'auto'))
        self.debounce_seconds_input.setValue(self.settings_manager.get('default_debounce_seconds', 3))
        
        if self.settings_manager.is_authenticated():
            self.auth_status_label.setText("✓ Previously authenticated")
            self.auth_status_label.setStyleSheet("color: green;")
    
    def browse_editor(self):
        """Browse for code editor executable"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Code Editor",
            "/usr/local/bin",
            "All Files (*)"
        )
        if file_path:
            self.editor_path_input.setText(file_path)
    
    def browse_ssh_key(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key",
            str(Path.home() / ".ssh"),
            "All Files (*)"
        )
        if file_path:
            self.ssh_key_input.setText(file_path)
    
    def browse_local_root(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Default Local Root Directory"
        )
        if folder:
            self.local_root_input.setText(folder)
    
    def test_authentication(self):
        """Test WordPress authentication"""
        wp_url = self.wp_url_input.text().strip()
        username = self.wp_username_input.text().strip()
        password = self.wp_password_input.text().strip()
        
        if not wp_url or not username or not password:
            QMessageBox.warning(self, "Missing Information", 
                              "Please fill in all WordPress authentication fields.")
            return
        
        self.test_auth_btn.setEnabled(False)
        self.auth_status_label.setText("Testing authentication...")
        self.auth_status_label.setStyleSheet("color: blue;")
        
        self.auth_thread = AuthThread(wp_url, username, password)
        self.auth_thread.auth_result.connect(self.handle_auth_result)
        self.auth_thread.start()
    
    def handle_auth_result(self, success, message):
        """Handle authentication test result"""
        self.test_auth_btn.setEnabled(True)
        
        if success:
            self.auth_status_label.setText(f"✓ {message}")
            self.auth_status_label.setStyleSheet("color: green;")
        else:
            self.auth_status_label.setText(f"✗ {message}")
            self.auth_status_label.setStyleSheet("color: red;")
    
    def closeEvent(self, event):
        """Handle dialog close - wait for auth thread to finish"""
        if self.auth_thread and self.auth_thread.isRunning():
            self.auth_thread.blockSignals(True)
            if not self.auth_thread.wait(2000):
                self.auth_thread.terminate()
                self.auth_thread.wait(1000)
            self.auth_thread.deleteLater()
            self.auth_thread = None
        super().closeEvent(event)
    
    def save_and_close(self):
        """Save settings and close dialog"""
        # Wait for any running auth thread to complete
        if self.auth_thread and self.auth_thread.isRunning():
            self.auth_thread.blockSignals(True)
            if not self.auth_thread.wait(2000):
                self.auth_thread.terminate()
                self.auth_thread.wait(1000)
            self.auth_thread.deleteLater()
            self.auth_thread = None
        
        # Save WordPress settings
        self.settings_manager.set('wp_url', self.wp_url_input.text().strip())
        self.settings_manager.set('wp_username', self.wp_username_input.text().strip())
        self.settings_manager.set('wp_app_password', self.wp_password_input.text().strip())
        
        # Save SSH & Sync settings
        self.settings_manager.set('ssh_key_path', self.ssh_key_input.text().strip())
        self.settings_manager.set('ssh_port', self.ssh_port_input.value())
        self.settings_manager.set('default_local_root', self.local_root_input.text().strip())
        self.settings_manager.set('default_sync_items', self.sync_items_input.toPlainText().strip())
        self.settings_manager.set('preferred_editor_path', self.editor_path_input.text().strip() or 'auto')
        self.settings_manager.set('default_debounce_seconds', self.debounce_seconds_input.value())
        
        # Mark as unauthenticated - user must test auth to re-enable
        if (self.settings_manager.get('wp_url') and 
            self.settings_manager.get('wp_username') and 
            self.settings_manager.get('wp_app_password')):
            # Keep authenticated status if all fields are filled
            pass
        else:
            self.settings_manager.set('authenticated', False)
        
        if self.settings_manager.save_settings():
            self.accept()
        else:
            QMessageBox.critical(self, "Error", "Failed to save settings")

class CommandThread(QThread):
    """Thread for running shell commands"""
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    
    def __init__(self, command, cwd):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.process = None
        self._stopping = False
        
    def run(self):
        try:
            # Force unbuffered output
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            
            # Ensure Homebrew paths are available (for fswatch, etc.)
            # This is critical for packaged apps that don't inherit full shell PATH
            current_path = env.get('PATH', '')
            homebrew_paths = '/opt/homebrew/bin:/usr/local/bin'
            if homebrew_paths not in current_path:
                env['PATH'] = f"{homebrew_paths}:{current_path}"
            
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(self.cwd),
                env=env
            )
            
            # Read output with interruptible checking
            while not self._stopping and self.process.poll() is None:
                try:
                    # Use a small timeout to make readline interruptible
                    import select
                    import sys
                    
                    # Check if data is available (non-blocking check)
                    if sys.platform != 'win32':
                        ready, _, _ = select.select([self.process.stdout], [], [], 0.1)
                        if ready:
                            line = self.process.stdout.readline()
                            if line:
                                self.output_signal.emit(line)
                    else:
                        # On Windows, just read with a check
                        line = self.process.stdout.readline()
                        if line:
                            self.output_signal.emit(line)
                except (ValueError, OSError):
                    # Pipe closed - exit cleanly
                    break
                except:
                    break
            
            # Process has exited - get return code
            if self.process.poll() is not None:
                # Read any final output that might be buffered
                try:
                    remaining = self.process.stdout.read()
                    if remaining:
                        self.output_signal.emit(remaining)
                except:
                    pass
                
                return_code = self.process.returncode
            else:
                # Process is still running but we're stopping
                return_code = 0
                
            self.finished_signal.emit(return_code)
            
        except Exception as e:
            if not self._stopping:
                self.output_signal.emit(f"\nError: {e}\n")
            self.finished_signal.emit(1)
    
    def stop(self):
        """Stop the thread and its process - non-blocking"""
        self._stopping = True
        if self.process and self.process.poll() is None:
            try:
                # Close stdout to unblock any read operations immediately
                if self.process.stdout:
                    self.process.stdout.close()
            except:
                pass
            
            # Terminate the process (don't wait - let thread detect it)
            try:
                self.process.terminate()
            except:
                pass
            
            # Start a timer to kill if it doesn't terminate quickly
            import threading
            def force_kill():
                try:
                    if self.process and self.process.poll() is None:
                        self.process.kill()
                except:
                    pass
            
            kill_timer = threading.Timer(1.0, force_kill)
            kill_timer.daemon = True
            kill_timer.start()

class NewSiteDialog(QDialog):
    """Dialog for creating a new site configuration"""
    
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("New Site Configuration")
        self.setMinimumWidth(500)
        self.init_ui()
        self.load_defaults()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Form layout for inputs
        form = QFormLayout()
        
        self.site_key_input = QLineEdit()
        self.site_key_input.setPlaceholderText("e.g., client-name")
        form.addRow("Site Key:", self.site_key_input)
        
        self.ssh_host_input = QLineEdit()
        self.ssh_host_input.setPlaceholderText("e.g., example.com")
        form.addRow("SSH Host:", self.ssh_host_input)
        
        self.ssh_port_input = QLineEdit("22")
        form.addRow("SSH Port:", self.ssh_port_input)
        
        self.ssh_user_input = QLineEdit()
        self.ssh_user_input.setPlaceholderText("e.g., cpaneluser")
        form.addRow("SSH User:", self.ssh_user_input)
        
        # Local root with browse button
        local_layout = QHBoxLayout()
        self.local_root_input = QLineEdit()
        self.local_root_input.setPlaceholderText("e.g., /Users/you/Sites/project/wp-content")
        local_layout.addWidget(self.local_root_input)
        browse_local_btn = QPushButton("Browse...")
        browse_local_btn.clicked.connect(self.browse_local_root)
        local_layout.addWidget(browse_local_btn)
        form.addRow("Local Root:", local_layout)
        
        self.remote_root_input = QLineEdit()
        self.remote_root_input.setPlaceholderText("e.g., /home/user/public_html/wp-content")
        form.addRow("Remote Root:", self.remote_root_input)
        
        layout.addLayout(form)
        
        # Sync items text area
        layout.addWidget(QLabel("Sync Items (one per line):"))
        self.sync_items_input = QPlainTextEdit()
        self.sync_items_input.setPlaceholderText("themes/my-theme\nplugins/my-plugin")
        self.sync_items_input.setMaximumHeight(100)
        layout.addWidget(self.sync_items_input)
        
        # Debounce seconds
        debounce_layout = QHBoxLayout()
        debounce_layout.addWidget(QLabel("Watch debounce (seconds):"))
        self.debounce_input = QSpinBox()
        self.debounce_input.setRange(1, 30)
        self.debounce_input.setValue(3)
        self.debounce_input.setToolTip("Time to wait before syncing after detecting changes")
        debounce_layout.addWidget(self.debounce_input)
        debounce_layout.addStretch()
        layout.addLayout(debounce_layout)
        
        # Additional options
        self.delete_check = QCheckBox("Enable delete sync (dangerous)")
        layout.addWidget(self.delete_check)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def browse_local_root(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Local Root Directory"
        )
        if folder:
            self.local_root_input.setText(folder)
    
    def validate_and_accept(self):
        if not self.site_key_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "Site Key is required")
            return
        if not self.ssh_host_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "SSH Host is required")
            return
        if not self.ssh_user_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "SSH User is required")
            return
        if not self.local_root_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "Local Root is required")
            return
        if not self.remote_root_input.text().strip():
            QMessageBox.warning(self, "Validation Error", "Remote Root is required")
            return
        if not self.sync_items_input.toPlainText().strip():
            QMessageBox.warning(self, "Validation Error", "At least one Sync Item is required")
            return
        
        self.accept()
    
    def get_config(self):
        """Return the configuration as a dictionary"""
        # Convert multi-line sync items to space-separated
        sync_items_lines = self.sync_items_input.toPlainText().strip().split('\n')
        sync_items = ' '.join([line.strip() for line in sync_items_lines if line.strip()])
        
        return {
            'site_key': self.site_key_input.text().strip(),
            'ssh_host': self.ssh_host_input.text().strip(),
            'ssh_port': self.ssh_port_input.text().strip(),
            'ssh_user': self.ssh_user_input.text().strip(),
            'local_root': self.local_root_input.text().strip(),
            'remote_root': self.remote_root_input.text().strip(),
            'sync_items': sync_items,
            'rsync_delete': '1' if self.delete_check.isChecked() else '0',
            'debounce_seconds': str(self.debounce_input.value())
        }
    
    def load_defaults(self):
        """Load default values from settings"""
        default_port = self.settings_manager.get('ssh_port', 22)
        self.ssh_port_input.setText(str(default_port))
        
        default_sync_items = self.settings_manager.get('default_sync_items', 'themes\nplugins')
        self.sync_items_input.setPlainText(default_sync_items)
        
        default_debounce = self.settings_manager.get('default_debounce_seconds', 3)
        self.debounce_input.setValue(int(default_debounce))

class SelectSiteDialog(QDialog):
    """Dialog for selecting a site from available API sites"""
    
    def __init__(self, api_sites_data, configured_sites, parent=None):
        super().__init__(parent)
        self.api_sites_data = api_sites_data
        self.configured_sites = configured_sites
        self.selected_site = None
        self.init_ui()
    
    def init_ui(self):
        """Initialize the UI"""
        self.setWindowTitle("Select Site to Configure")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Instructions
        layout.addWidget(QLabel("Select a site from the API to configure, or use Manual Entry for custom sites (e.g., dev environments):"))
        
        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type to filter sites...")
        self.search_input.textChanged.connect(self.filter_sites)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)
        
        # Sites list
        self.sites_list = QListWidget()
        self.sites_list.itemDoubleClicked.connect(self.on_site_double_click)
        layout.addWidget(self.sites_list)
        
        # Populate list
        self.populate_sites()
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.manual_btn = QPushButton("Manual Entry...")
        self.manual_btn.clicked.connect(self.on_manual_entry)
        button_layout.addWidget(self.manual_btn)
        
        button_layout.addStretch()
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.on_accept)
        buttons.rejected.connect(self.reject)
        button_layout.addWidget(buttons)
        
        layout.addLayout(button_layout)
    
    def populate_sites(self):
        """Populate the sites list"""
        self.sites_list.clear()
        
        # Sort sites alphabetically
        sorted_sites = sorted(self.api_sites_data, key=lambda x: x.get('title', ''))
        
        for site_data in sorted_sites:
            site_key = site_data.get('title', '')
            if not site_key:
                continue
            
            # Mark if already configured
            if site_key in self.configured_sites:
                display_text = f"{site_key} (already configured)"
            else:
                display_text = site_key
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, site_data)
            self.sites_list.addItem(item)
    
    def filter_sites(self, text):
        """Filter sites list based on search text"""
        for i in range(self.sites_list.count()):
            item = self.sites_list.item(i)
            site_data = item.data(Qt.UserRole)
            site_key = site_data.get('title', '').lower()
            item.setHidden(text.lower() not in site_key)
    
    def on_site_double_click(self, item):
        """Handle double-click on a site"""
        self.selected_site = item.data(Qt.UserRole)
        self.accept()
    
    def on_manual_entry(self):
        """Handle manual entry button"""
        # Use a special marker to indicate manual entry
        self.selected_site = {'manual_entry': True}
        self.accept()
    
    def on_accept(self):
        """Handle OK button"""
        current_item = self.sites_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "No Selection", "Please select a site to configure.")
            return
        
        self.selected_site = current_item.data(Qt.UserRole)
        self.accept()
    
    def get_selected_site(self):
        """Return the selected site data"""
        return self.selected_site

class WPSyncGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Get project paths - handle both bundled app and development
        if getattr(sys, 'frozen', False):
            # Running as a bundled app (py2app)
            # __file__ points to Resources/wp-sync-native.py
            # So parent is Resources, which is our root for bundled apps
            self.project_root = Path(__file__).parent.resolve()
        else:
            # Running in development mode
            # __file__ points to gui/wp-sync-native.py
            # So parent.parent is the project root
            self.project_root = Path(__file__).parent.parent.resolve()
        
        # Store user data (sites, settings) in Application Support (persists across app updates)
        self.user_data_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Starter"
        self.sites_dir = self.user_data_dir / "sites"
        self.bin_dir = self.project_root / "bin"
        
        # Migrate old settings/sites to new location if needed
        self.migrate_user_data()
        
        # Initialize settings manager
        self.settings_manager = SettingsManager(self.project_root)
        
        # Thread tracking
        self.current_thread = None
        self.watch_thread = None
        self._stopping_watch = False
        self.startup_auth_thread = None
        self.fetch_sites_thread = None
        self.permissions_thread = None
        self.api_sites_data = []  # Store API sites data
        
        # System tray tracking
        self.is_syncing = False  # Track if actively pushing/pulling
        self.sync_start_time = None  # Track when sync started
        self.sync_timeout_timer = None  # Timer for sync timeout fallback
        
        self.init_ui()
        self.init_system_tray()
        self.load_sites()
        
        # Check authentication on startup
        self.check_authentication()
        
        # Check for updates automatically on startup (delayed to not interfere with auth)
        QTimer.singleShot(3000, self.auto_check_for_updates)
    
    def auto_check_for_updates(self):
        """Automatically check for updates on startup (silent if no update available)"""
        if UPDATE_CHECKER_AVAILABLE:
            self.check_for_updates(silent=True)
    
    def migrate_user_data(self):
        """Migrate settings and sites from old locations to new location (Application Support)"""
        import shutil
        
        # Old app name (if renaming from previous version)
        old_app_support_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Tool"
        
        # Old locations (inside app bundle)
        old_settings_file = self.project_root / "config" / "app-settings.json"
        old_sites_dir = self.project_root / "config" / "sites"
        
        # New locations (Application Support with new app name)
        new_settings_file = self.user_data_dir / "app-settings.json"
        new_sites_dir = self.sites_dir
        
        # Create new directory structure
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
    
    def init_system_tray(self):
        """Initialize system tray icon and menu"""
        # Check if system tray is available
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        
        # Initialize macOS native status bar with text (for title display)
        if MACOS_STATUSBAR_AVAILABLE:
            self.status_bar = NSStatusBar.systemStatusBar()
            self.status_item = self.status_bar.statusItemWithLength_(NSVariableStatusItemLength)
            self.status_item.setTitle_("⚪")
        
        # Create system tray icon (for menu)
        self.tray_icon = QSystemTrayIcon(self)
        
        # Set initial icon - try to use app icon, fallback to default
        icon_path = self.project_root / "gui" / "app-icon.png"
        if icon_path.exists():
            self.tray_icon.setIcon(QIcon(str(icon_path)))
        else:
            # Use application style icon as fallback
            self.tray_icon.setIcon(self.style().standardIcon(self.style().SP_ComputerIcon))
        
        # Create context menu
        tray_menu = QMenu()
        
        # Show/Hide window action
        self.show_action = QAction("Show Window", self)
        self.show_action.triggered.connect(self.show_window)
        tray_menu.addAction(self.show_action)
        
        tray_menu.addSeparator()
        
        # Watch status (not clickable, just shows status)
        self.watch_status_action = QAction("⚪ Watch: Inactive", self)
        self.watch_status_action.setEnabled(False)
        tray_menu.addAction(self.watch_status_action)
        
        # Sync status (not clickable, just shows status)
        self.sync_status_action = QAction("⚪ Sync: Idle", self)
        self.sync_status_action.setEnabled(False)
        tray_menu.addAction(self.sync_status_action)
        
        tray_menu.addSeparator()
        
        # Quick actions
        self.tray_toggle_watch = QAction("Start Watch", self)
        self.tray_toggle_watch.triggered.connect(self.toggle_watch)
        tray_menu.addAction(self.tray_toggle_watch)
        
        tray_menu.addSeparator()
        
        # Quit action
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        # Set menu and show
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("Webmix Sync Starter\n⚪ Watch: Inactive")
        
        # Handle tray icon clicks
        self.tray_icon.activated.connect(self.tray_icon_activated)
        
        # Show tray icon (on macOS, use native status bar instead)
        if MACOS_STATUSBAR_AVAILABLE:
            self._build_native_menu()
        else:
            self.tray_icon.show()
    
    def _build_native_menu(self):
        """Build native NSMenu for macOS status bar"""
        if not MACOS_STATUSBAR_AVAILABLE:
            return
        
        menu = NSMenu.alloc().init()
        
        # Show Window
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show Window", None, "")
        item.setTarget_(self)
        item.setAction_("showWindow:")
        menu.addItem_(item)
        
        menu.addItem_(NSMenuItem.separatorItem())
        
        # Watch status (disabled)
        self.ns_watch_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("⚪ Watch: Inactive", None, "")
        self.ns_watch_item.setEnabled_(False)
        menu.addItem_(self.ns_watch_item)
        
        # Sync status (disabled)
        self.ns_sync_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("⚪ Sync: Idle", None, "")
        self.ns_sync_item.setEnabled_(False)
        menu.addItem_(self.ns_sync_item)
        
        menu.addItem_(NSMenuItem.separatorItem())
        
        # Toggle watch
        self.ns_toggle_watch = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Start Watch", None, "")
        self.ns_toggle_watch.setTarget_(self)
        self.ns_toggle_watch.setAction_("toggleWatch:")
        menu.addItem_(self.ns_toggle_watch)
        
        menu.addItem_(NSMenuItem.separatorItem())
        
        # Quit
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", None, "")
        item.setTarget_(self)
        item.setAction_("quitApp:")
        menu.addItem_(item)
        
        self.status_item.setMenu_(menu)
    
    # PyObjC callback methods
    def showWindow_(self, sender):
        """NSMenu callback to show window"""
        self.show_window()
    
    def toggleWatch_(self, sender):
        """NSMenu callback to toggle watch"""
        self.toggle_watch()
    
    def quitApp_(self, sender):
        """NSMenu callback to quit"""
        self.quit_application()
    
    def update_tray_watch_status(self, is_active):
        """Update system tray icon to reflect watch status"""
        if not hasattr(self, 'tray_icon'):
            return
        
        if is_active:
            self.watch_status_action.setText("🟢 Watch: Active")
            self.tray_toggle_watch.setText("Stop Watch")
            status = "🟢 Watch: Active"
            icon = "🟢"
        else:
            self.watch_status_action.setText("⚪ Watch: Inactive")
            self.tray_toggle_watch.setText("Start Watch")
            status = "⚪ Watch: Inactive"
            icon = "⚪"
        
        # Update macOS menubar text
        if MACOS_STATUSBAR_AVAILABLE and hasattr(self, 'status_item'):
            sync_icon = " 🔄" if self.is_syncing else ""
            self.status_item.setTitle_(f"{icon}{sync_icon}")
            
            # Update native menu items
            if hasattr(self, 'ns_watch_item'):
                self.ns_watch_item.setTitle_(status)
            if hasattr(self, 'ns_toggle_watch'):
                self.ns_toggle_watch.setTitle_("Stop Watch" if is_active else "Start Watch")
        
        # Update tooltip
        sync_part = "\n🔄 Syncing..." if self.is_syncing else ""
        self.tray_icon.setToolTip(f"Webmix Sync Starter\n{status}{sync_part}")
    
    def update_tray_sync_status(self, is_syncing, operation=""):
        """Update system tray icon to reflect sync activity"""
        if not hasattr(self, 'tray_icon'):
            return
        
        self.is_syncing = is_syncing
        
        # Cancel timeout timer when manually clearing sync status
        if not is_syncing and hasattr(self, 'sync_timeout_timer') and self.sync_timeout_timer:
            self.sync_timeout_timer.stop()
            self.sync_timeout_timer = None
        
        if is_syncing:
            if operation:
                self.sync_status_action.setText(f"🔄 Syncing: {operation}")
                sync_text = f"🔄 Syncing: {operation}"
            else:
                self.sync_status_action.setText("🔄 Syncing...")
                sync_text = "🔄 Syncing..."
        else:
            self.sync_status_action.setText("⚪ Sync: Idle")
            sync_text = "⚪ Sync: Idle"
        
        # Update macOS menubar text
        if MACOS_STATUSBAR_AVAILABLE and hasattr(self, 'status_item'):
            watch_active = self.watch_thread and self.watch_thread.isRunning()
            watch_icon = "🟢" if watch_active else "⚪"
            sync_icon = " 🔄" if is_syncing else ""
            self.status_item.setTitle_(f"{watch_icon}{sync_icon}")
            
            # Update native menu item
            if hasattr(self, 'ns_sync_item'):
                self.ns_sync_item.setTitle_(sync_text)
        
        # Update tooltip
        watch_active = self.watch_thread and self.watch_thread.isRunning()
        watch_status = "🟢 Watch: Active" if watch_active else "⚪ Watch: Inactive"
        sync_part = "\n🔄 Syncing..." if is_syncing else ""
        self.tray_icon.setToolTip(f"Webmix Sync Starter\n{watch_status}{sync_part}")
    
    def tray_icon_activated(self, reason):
        """Handle tray icon activation (click)"""
        if reason == QSystemTrayIcon.DoubleClick or reason == QSystemTrayIcon.Trigger:
            self.show_window()
    
    def show_window(self):
        """Show and raise the main window"""
        self.show()
        self.raise_()
        self.activateWindow()
    
    def quit_application(self):
        """Quit the entire application"""
        # Stop watch if running
        if self.watch_thread and self.watch_thread.isRunning():
            self.stop_watch()
        
        # Clean up tray icon
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        
        QApplication.quit()
    
    def migrate_user_data(self):
        """Migrate settings and sites from old locations to new location (Application Support)"""
        import shutil
        
        # Old app name (if renaming from previous version)
        old_app_support_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Tool"
        
        # Old locations (inside app bundle)
        old_settings_file = self.project_root / "config" / "app-settings.json"
        old_sites_dir = self.project_root / "config" / "sites"
        
        # New locations (Application Support with new app name)
        new_settings_file = self.user_data_dir / "app-settings.json"
        new_sites_dir = self.sites_dir
        
        # Create new directory structure
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
    
        # PRIORITY 1: Migrate from old app name in Application Support (e.g., after app rename)
        if old_app_support_dir.exists() and old_app_support_dir != self.user_data_dir:
            old_app_settings = old_app_support_dir / "app-settings.json"
            old_app_sites = old_app_support_dir / "sites"
            
            if old_app_settings.exists() and not new_settings_file.exists():
                try:
                    shutil.copy2(old_app_settings, new_settings_file)
                    print(f"✓ Migrated settings from old app name: {old_app_settings}")
                except Exception as e:
                    print(f"Warning: Could not migrate settings from old app name: {e}")
            
            if old_app_sites.exists() and not new_sites_dir.exists():
                try:
                    shutil.copytree(old_app_sites, new_sites_dir)
                    print(f"✓ Migrated sites from old app name: {old_app_sites}")
                except Exception as e:
                    print(f"Warning: Could not migrate sites from old app name: {e}")
        
        # PRIORITY 2: Migrate from app bundle (first time migration from old versions)
        if old_settings_file.exists() and not new_settings_file.exists():
            try:
                shutil.copy2(old_settings_file, new_settings_file)
                print(f"✓ Migrated settings from {old_settings_file} to {new_settings_file}")
            except Exception as e:
                print(f"Warning: Could not migrate settings: {e}")
        
        # Migrate sites directory if it exists in old location and not in new location
        if old_sites_dir.exists() and not new_sites_dir.exists():
            try:
                shutil.copytree(old_sites_dir, new_sites_dir)
                print(f"✓ Migrated sites from {old_sites_dir} to {new_sites_dir}")
            except Exception as e:
                print(f"Warning: Could not migrate sites: {e}")
        
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Webmix Sync Starter")
        self.setGeometry(100, 100, 900, 650)
        
        # Set window icon if available
        icon_path = self.project_root / "gui" / "app-icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        # Apply minimalistic, modern color scheme
        self.setStyleSheet("""
            QMainWindow {
                background-color: #fafafa;
            }
            QGroupBox {
                font-weight: 500;
                font-size: 11px;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 10px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 6px;
                color: #1f2937;
            }
            QPushButton {
                background-color: #f3f4f6;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 500;
                font-size: 11px;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #e5e7eb;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #d1d5db;
            }
            QPushButton:disabled {
                background-color: #6b7280;
                color: #d1d5db;
                border-color: #6b7280;
            }
            QPushButton#pullBtn {
                background-color: #f3f4f6;
                color: #374151;
            }
            QPushButton#pullBtn:hover {
                background-color: #e5e7eb;
            }
            QPushButton#pushBtn {
                background-color: #f3f4f6;
                color: #374151;
            }
            QPushButton#pushBtn:hover {
                background-color: #e5e7eb;
            }
            QPushButton#watchBtn {
                background-color: #fee2e2;
                color: #7f1d1d;
                border: 1px solid #fca5a5;
            }
            QPushButton#watchBtn:hover {
                background-color: #fecaca;
            }
            QPushButton#watchBtnActive {
                background-color: #d1fae5;
                color: #065f46;
                border: 1px solid #6ee7b7;
            }
            QPushButton#watchBtnActive:hover {
                background-color: #a7f3d0;
            }
            QPushButton#maintenanceBtn {
                background-color: #f3f4f6;
                color: #374151;
            }
            QPushButton#maintenanceBtn:hover {
                background-color: #e5e7eb;
            }
            QPushButton#maintenanceBtn:disabled {
                background-color: #6b7280;
                color: #d1d5db;
                border-color: #6b7280;
            }
            QPushButton#devEnvBtn {
                background-color: #f3f4f6;
                color: #374151;
            }
            QPushButton#devEnvBtn:hover {
                background-color: #e5e7eb;
            }
            QPushButton#devEnvBtn:disabled {
                background-color: #6b7280;
                color: #d1d5db;
                border-color: #6b7280;
            }
            QPushButton#secondaryBtn {
                background-color: #ffffff;
                color: #6b7280;
                border: 1px solid #e5e7eb;
            }
            QPushButton#secondaryBtn:hover {
                background-color: #f9fafb;
                color: #374151;
            }
            QComboBox {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                padding: 4px 8px;
                background-color: #ffffff;
                font-size: 11px;
                min-height: 24px;
                color: #1f2937;
            }
            QComboBox:focus {
                border-color: #9ca3af;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QTextEdit {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                background-color: #f9fafb;
                color: #1f2937;
                font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }
            QLabel {
                color: #1f2937;
            }
        """)
        
        # Menu bar
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("Settings")
        
        settings_action = QAction("Preferences...", self)
        settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(settings_action)
        
        # Help menu with update checker
        help_menu = menubar.addMenu("Help")
        
        if UPDATE_CHECKER_AVAILABLE:
            update_action = QAction("Check for Updates...", self)
            update_action.triggered.connect(self.check_for_updates)
            help_menu.addAction(update_action)
        
        about_action = QAction(f"About Webmix Sync Starter v{APP_VERSION}", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
        # Central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(12, 12, 12, 12)
        
        # Site Configuration Group
        site_group = QGroupBox("Site Configuration")
        site_layout = QVBoxLayout()
        site_layout.setSpacing(8)
        
        # Currently selected site display (prominent)
        self.selected_site_label = QLabel("<b>Selected:</b> None")
        self.selected_site_label.setStyleSheet("""
            color: #1f2937;
            padding: 8px 12px;
            background-color: #dbeafe;
            border-radius: 6px;
            border: 2px solid #3b82f6;
            font-size: 13px;
            font-weight: bold;
        """)
        site_layout.addWidget(self.selected_site_label)
        
        # Site selector dropdown
        site_select_layout = QHBoxLayout()
        site_select_layout.setSpacing(6)
        site_label = QLabel("Site:")
        site_label.setMinimumWidth(35)
        site_select_layout.addWidget(site_label)
        self.site_combo = QComboBox()
        self.site_combo.setMinimumWidth(200)
        self.site_combo.currentIndexChanged.connect(self.on_site_selected)
        site_select_layout.addWidget(self.site_combo)
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("secondaryBtn")
        self.refresh_btn.clicked.connect(self.load_sites)
        site_select_layout.addWidget(self.refresh_btn)
        
        self.new_site_btn = QPushButton("New Site")
        self.new_site_btn.setObjectName("secondaryBtn")
        self.new_site_btn.clicked.connect(self.create_new_site)
        site_select_layout.addWidget(self.new_site_btn)
        
        self.sync_api_btn = QPushButton("Sync API")
        self.sync_api_btn.setObjectName("secondaryBtn")
        self.sync_api_btn.clicked.connect(self.sync_from_api)
        site_select_layout.addWidget(self.sync_api_btn)
        
        self.edit_site_btn = QPushButton("Edit")
        self.edit_site_btn.setObjectName("secondaryBtn")
        self.edit_site_btn.clicked.connect(self.edit_current_site)
        site_select_layout.addWidget(self.edit_site_btn)
        
        site_select_layout.addStretch()
        
        site_layout.addLayout(site_select_layout)
        
        # Site info
        self.site_info_label = QLabel("Select a site")
        self.site_info_label.setStyleSheet("""
            color: #6b7280;
            padding: 6px 8px;
            background-color: #f9fafb;
            border-radius: 4px;
            border: 1px solid #e5e7eb;
            font-size: 11px;
        """)
        site_layout.addWidget(self.site_info_label)
        
        site_group.setLayout(site_layout)
        main_layout.addWidget(site_group)
        
        # Site Actions Group (requires site selection)
        site_actions_group = QGroupBox("Site Actions")
        site_actions_layout = QVBoxLayout()
        site_actions_layout.setSpacing(8)
        
        # Primary sync row
        sync_row = QHBoxLayout()
        sync_row.setSpacing(6)
        
        self.pull_btn = QPushButton("⬇ Pull")
        self.pull_btn.setObjectName("pullBtn")
        self.pull_btn.setMinimumHeight(32)
        self.pull_btn.clicked.connect(self.run_pull)
        sync_row.addWidget(self.pull_btn)
        
        self.push_btn = QPushButton("⬆ Push")
        self.push_btn.setObjectName("pushBtn")
        self.push_btn.setMinimumHeight(32)
        self.push_btn.clicked.connect(self.run_push)
        sync_row.addWidget(self.push_btn)
        
        self.watch_btn = QPushButton("Watch")
        self.watch_btn.setObjectName("watchBtn")
        self.watch_btn.setMinimumHeight(32)
        self.watch_btn.clicked.connect(self.toggle_watch)
        sync_row.addWidget(self.watch_btn)
        
        site_actions_layout.addLayout(sync_row)
        
        # Secondary site actions row
        site_secondary_row = QHBoxLayout()
        site_secondary_row.setSpacing(6)
        
        self.test_connection_btn = QPushButton("🔌 Test Connection")
        self.test_connection_btn.setMinimumHeight(30)
        self.test_connection_btn.clicked.connect(self.test_connection)
        site_secondary_row.addWidget(self.test_connection_btn)
        
        self.maintenance_btn = QPushButton("🔧 Maintenance")
        self.maintenance_btn.setObjectName("maintenanceBtn")
        self.maintenance_btn.setMinimumHeight(30)
        self.maintenance_btn.setEnabled(False)
        self.maintenance_btn.clicked.connect(self.run_maintenance)
        self.maintenance_btn.hide()  # Hidden for now
        site_secondary_row.addWidget(self.maintenance_btn)
        
        self.ssh_btn = QPushButton("🖥 SSH")
        self.ssh_btn.setMinimumHeight(30)
        self.ssh_btn.setEnabled(True)
        self.ssh_btn.clicked.connect(self.open_ssh_terminal)
        site_secondary_row.addWidget(self.ssh_btn)
        
        self.open_in_editor_btn = QPushButton("📝 Open in Editor")
        self.open_in_editor_btn.setMinimumHeight(30)
        self.open_in_editor_btn.setEnabled(True)
        self.open_in_editor_btn.clicked.connect(self.open_in_editor)
        site_secondary_row.addWidget(self.open_in_editor_btn)
        
        site_actions_layout.addLayout(site_secondary_row)
        
        # Permissions management row
        permissions_row = QHBoxLayout()
        permissions_row.setSpacing(6)
        
        self.open_rights_btn = QPushButton("🔓 Open Rights")
        self.open_rights_btn.setMinimumHeight(30)
        self.open_rights_btn.setEnabled(True)
        self.open_rights_btn.setToolTip("Open file permissions on server (chmod 755/644)")
        self.open_rights_btn.clicked.connect(self.open_rights)
        permissions_row.addWidget(self.open_rights_btn)
        
        self.close_rights_btn = QPushButton("🔒 Close Rights")
        self.close_rights_btn.setMinimumHeight(30)
        self.close_rights_btn.setEnabled(True)
        self.close_rights_btn.setToolTip("Restrict file permissions on server (secure WordPress)")
        self.close_rights_btn.clicked.connect(self.close_rights)
        permissions_row.addWidget(self.close_rights_btn)
        
        site_actions_layout.addLayout(permissions_row)
        
        site_actions_group.setLayout(site_actions_layout)
        main_layout.addWidget(site_actions_group)
        
        # General Actions Group (always available)
        general_group = QGroupBox("General")
        general_layout = QHBoxLayout()
        general_layout.setSpacing(6)
        
        self.dev_env_btn = QPushButton("🚀 Dev Setup")
        self.dev_env_btn.setObjectName("devEnvBtn")
        self.dev_env_btn.setMinimumHeight(30)
        self.dev_env_btn.setEnabled(False)
        self.dev_env_btn.clicked.connect(self.setup_dev_environment)
        self.dev_env_btn.hide()  # Hidden for now
        general_layout.addWidget(self.dev_env_btn)
        
        self.clean_local_btn = QPushButton("🗑️ Clean Local Files")
        self.clean_local_btn.setObjectName("secondaryBtn")
        self.clean_local_btn.setMinimumHeight(30)
        self.clean_local_btn.setToolTip("Delete local files (keeps server files safe)")
        self.clean_local_btn.clicked.connect(self.clean_local_files)
        general_layout.addWidget(self.clean_local_btn)
        
        general_layout.addStretch()
        
        general_group.setLayout(general_layout)
        main_layout.addWidget(general_group)
        
        # Output Group
        output_group = QGroupBox("Console")
        output_layout = QVBoxLayout()
        output_layout.setSpacing(6)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFont(QFont("Monaco", 10))
        self.output_text.setMinimumHeight(240)
        output_layout.addWidget(self.output_text)
        
        # Clear button
        clear_layout = QHBoxLayout()
        clear_layout.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("secondaryBtn")
        clear_btn.setMaximumWidth(80)
        clear_btn.clicked.connect(self.clear_output)
        clear_layout.addWidget(clear_btn)
        output_layout.addLayout(clear_layout)
        
        output_group.setLayout(output_layout)
        main_layout.addWidget(output_group)
        
        # Status bar
        self.statusBar().setStyleSheet("""
            QStatusBar {
                background-color: #f9fafb;
                color: #6b7280;
                border-top: 1px solid #e5e7eb;
                font-size: 11px;
            }
        """)
        self.statusBar().showMessage("Ready")
        
        # Initial state
        self.update_button_states(False)
        self.log_output("⚡ Ready\n", "info")
        self.log_output(f"📁 {self.project_root}\n")
    
    def load_sites(self):
        """Load available sites - only showing configured ones in dropdown"""
        # Remember current selection
        current_site = self.site_combo.currentText()
        self.site_combo.clear()
        
        # Get locally configured sites
        configured_sites = set()
        if self.sites_dir.exists():
            site_files = list(self.sites_dir.glob("*.env"))
            configured_sites = {f.stem for f in site_files if f.stem != "example-site"}
        
        # Sort sites alphabetically
        sorted_sites = sorted(list(configured_sites))
        
        if not configured_sites:
            self.selected_site_label.setText("<b>Selected:</b> None")
            self.selected_site_label.setStyleSheet("""
                color: #6b7280;
                padding: 8px 12px;
                background-color: #f9fafb;
                border-radius: 6px;
                border: 1px solid #e5e7eb;
                font-size: 13px;
            """)
            self.log_output("No configured sites.\n", "warning")
            self.log_output("Click 'Sync from API' to load available sites, then '+ New Site' to configure one.\n", "info")
            return
        
        # Add only configured sites
        for site in sorted_sites:
            self.site_combo.addItem(site)
            index = self.site_combo.count() - 1
            self.site_combo.setItemData(index, site)
        
        # Restore previous selection if it still exists
        if current_site:
            for i in range(self.site_combo.count()):
                if self.site_combo.itemData(i) == current_site:
                    self.site_combo.setCurrentIndex(i)
                    break
        
        self.update_button_states(True)
        self.log_output(f"Loaded {len(configured_sites)} configured site(s)\n", "success")
    
    def create_new_site(self):
        """Open dialog to select and configure a new site from API or manual entry"""
        # Check if API sites are loaded
        if not self.api_sites_data:
            reply = QMessageBox.question(
                self, "No Sites Available",
                "No sites available from API.\n\n"
                "Do you want to manually enter site details?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                # Manual entry
                self._create_manual_site()
            return
        
        # Get already configured sites
        configured_sites = set()
        if self.sites_dir.exists():
            site_files = list(self.sites_dir.glob("*.env"))
            configured_sites = {f.stem for f in site_files if f.stem != "example-site"}
        
        # Show site selection dialog
        select_dialog = SelectSiteDialog(self.api_sites_data, configured_sites, self)
        if select_dialog.exec_() != QDialog.Accepted:
            return
        
        selected_site = select_dialog.get_selected_site()
        if not selected_site:
            return
        
        # Check if manual entry was selected
        if selected_site.get('manual_entry'):
            self._create_manual_site()
            return
        
        # Show configuration dialog for the selected site from API
        config_dialog = ConfigureSiteDialog(selected_site, self.settings_manager, self)
        if config_dialog.exec_() != QDialog.Accepted:
            return
        
        config = config_dialog.get_config()
        self._save_site_config(config)
    
    def _create_manual_site(self):
        """Create a site with manual entry"""
        dialog = NewSiteDialog(self.settings_manager, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        
        config = dialog.get_config()
        self._save_site_config(config)
    
    def _save_site_config(self, config):
        """Save site configuration to .env file"""
        # Save the configuration
        site_file = self.sites_dir / f"{config['site_key']}.env"
        
        try:
            # Ensure sites directory exists
            self.sites_dir.mkdir(parents=True, exist_ok=True)
            
            # Write the configuration file
            with open(site_file, 'w') as f:
                f.write(f'SITE_KEY="{config["site_key"]}"\n')
                f.write(f'SSH_HOST="{config["ssh_host"]}"\n')
                f.write(f'SSH_PORT="{config["ssh_port"]}"\n')
                f.write(f'SSH_USER="{config["ssh_user"]}"\n')
                f.write(f'LOCAL_ROOT="{config["local_root"]}"\n')
                f.write(f'REMOTE_ROOT="{config["remote_root"]}"\n')
                # Use bash $'...\n...' syntax for multi-line SYNC_ITEMS
                sync_items_escaped = config["sync_items"].replace('\n', '\\n')
                f.write(f"SYNC_ITEMS=$'{sync_items_escaped}'\n")
                f.write(f'RSYNC_DELETE="{config["rsync_delete"]}"\n')
                debounce = config.get('debounce_seconds', '3')
                f.write(f'DEBOUNCE_SECONDS="{debounce}"\n')
            
            self.log_output(f"\n✓ Configured site: {config['site_key']}\n", "success")
            
            # Reload sites
            self.load_sites()
            
            # Select the new site
            index = self.site_combo.findText(config['site_key'])
            if index >= 0:
                self.site_combo.setCurrentIndex(index)
            
        except Exception as e:
            self.log_output(f"\n✗ Error configuring site: {e}\n", "error")
            QMessageBox.critical(
                self, "Error",
                f"Failed to save site configuration:\n{e}"
            )
    
    def fetch_sites_from_api_silent(self):
        """Fetch sites from API silently on startup"""
        wp_url = self.settings_manager.get('wp_url', '')
        wp_username = self.settings_manager.get('wp_username', '')
        wp_password = self.settings_manager.get('wp_app_password', '')
        
        if not wp_url or not wp_username or not wp_password:
            return
        
        self.log_output("Loading sites from API...\n", "info")
        self.fetch_sites_thread = FetchSitesThread(wp_url, wp_username, wp_password)
        self.fetch_sites_thread.sites_result.connect(self.handle_api_sites_silent)
        self.fetch_sites_thread.start()
    
    def sync_from_api(self):
        """Fetch sites from WordPress API"""
        wp_url = self.settings_manager.get('wp_url', '')
        wp_username = self.settings_manager.get('wp_username', '')
        wp_password = self.settings_manager.get('wp_app_password', '')
        
        if not wp_url or not wp_username or not wp_password:
            QMessageBox.warning(
                self, "Authentication Required",
                "Please configure WordPress credentials in Settings > Preferences first."
            )
            return
        
        self.log_output("\n[Loading sites from API...]\n", "info")
        self.sync_api_btn.setEnabled(False)
        self.sync_api_btn.setText("Fetching...")
        
        self.fetch_sites_thread = FetchSitesThread(wp_url, wp_username, wp_password)
        self.fetch_sites_thread.sites_result.connect(self.handle_api_sites)
        self.fetch_sites_thread.start()
    
    def handle_api_sites_silent(self, success, sites_data, message):
        """Handle sites fetched from API on startup (silent)"""
        if success:
            self.api_sites_data = sites_data
            self.log_output(f"✓ {message}\n", "success")
        else:
            self.log_output(f"⚠️ Could not load API sites: {message}\n", "warning")
            self.api_sites_data = []
        
        # Load sites (will show all from API, marked as configured or not)
        self.load_sites()
        
        # Cleanup the thread
        if self.fetch_sites_thread:
            self.fetch_sites_thread.blockSignals(True)
            self.fetch_sites_thread.deleteLater()
            self.fetch_sites_thread = None
    
    def handle_api_sites(self, success, sites_data, message):
        """Handle sites fetched from API (manual refresh)"""
        self.sync_api_btn.setEnabled(True)
        self.sync_api_btn.setText("Sync from API")
        
        # Cleanup the thread first
        if self.fetch_sites_thread:
            self.fetch_sites_thread.blockSignals(True)
            self.fetch_sites_thread.deleteLater()
            self.fetch_sites_thread = None
        
        if not success:
            self.log_output(f"✗ {message}\n", "error")
            QMessageBox.critical(self, "API Error", f"Failed to fetch sites:\n{message}")
            return
        
        self.log_output(f"✓ {message}\n", "success")
        self.api_sites_data = sites_data
        
        # Reload sites list
        self.load_sites()
        
        # Show success message
        configured_count = len([site for site in sites_data 
                                if (self.sites_dir / f"{site.get('title', '')}.env").exists()])
        total_count = len(sites_data)
        unconfigured_count = total_count - configured_count
        
        if unconfigured_count > 0:
            QMessageBox.information(
                self, "Sites Loaded",
                f"Loaded {total_count} sites from API.\n\n"
                f"{configured_count} site(s) already configured.\n"
                f"{unconfigured_count} site(s) available to configure.\n\n"
                f"Click '+ New Site' to configure additional sites."
            )
        else:
            QMessageBox.information(
                self, "Sites Loaded",
                f"Loaded {total_count} sites from API.\nAll sites are configured."
            )
    
    def configure_api_site(self, site_data, remaining_sites):
        """Open configuration dialog for an API site"""
        dialog = ConfigureSiteDialog(site_data, self.settings_manager, self)
        
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            
            # Save the configuration
            site_file = self.sites_dir / f"{config['site_key']}.env"
            
            try:
                # Ensure sites directory exists
                self.sites_dir.mkdir(parents=True, exist_ok=True)
                
                # Write the configuration file
                with open(site_file, 'w') as f:
                    f.write(f'SITE_KEY="{config["site_key"]}"\n')
                    f.write(f'SSH_HOST="{config["ssh_host"]}"\n')
                    f.write(f'SSH_PORT="{config["ssh_port"]}"\n')
                    f.write(f'SSH_USER="{config["ssh_user"]}"\n')
                    f.write(f'LOCAL_ROOT="{config["local_root"]}"\n')
                    f.write(f'REMOTE_ROOT="{config["remote_root"]}"\n')
                    # Use bash $'...\n...' syntax for multi-line SYNC_ITEMS
                    sync_items_escaped = config["sync_items"].replace('\n', '\\n')
                    f.write(f"SYNC_ITEMS=$'{sync_items_escaped}'\n")
                    f.write(f'RSYNC_DELETE="{config["rsync_delete"]}"\n')
                    debounce = config.get('debounce_seconds', '3')
                    f.write(f'DEBOUNCE_SECONDS="{debounce}"\n')
                
                self.log_output(f"✓ Configured site: {config['site_key']}\n", "success")
                
                # If there are more sites to configure, ask user
                if remaining_sites:
                    reply = QMessageBox.question(
                        self, "More Sites Available",
                        f"Site '{config['site_key']}' configured.\n\n"
                        f"{len(remaining_sites)} more site(s) need configuration.\n"
                        f"Configure next site: {remaining_sites[0].get('title', 'Unknown')}?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.Yes:
                        self.configure_api_site(remaining_sites[0], remaining_sites[1:])
                    else:
                        self.log_output(f"{len(remaining_sites)} site(s) left unconfigured\n", "warning")
                        self.load_sites()
                else:
                    # All sites configured
                    self.load_sites()
                    QMessageBox.information(
                        self, "All Sites Configured",
                        "All sites from API have been configured!"
                    )
                
            except Exception as e:
                self.log_output(f"✗ Error configuring site: {e}\n", "error")
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to save site configuration:\n{e}"
                )
        else:
            # User cancelled, ask if they want to continue with remaining
            if remaining_sites:
                reply = QMessageBox.question(
                    self, "More Sites Available",
                    f"Skipped '{site_data.get('title', 'Unknown')}'.\n\n"
                    f"{len(remaining_sites)} more site(s) need configuration.\n"
                    f"Continue with next site?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.configure_api_site(remaining_sites[0], remaining_sites[1:])
                else:
                    self.log_output("Stopped configuring sites\n", "warning")
                    self.load_sites()
            else:
                self.load_sites()
    
    def edit_current_site(self):
        """Edit configuration for the currently selected site"""
        site_key = self.site_combo.currentText()
        if not site_key:
            QMessageBox.warning(self, "No Site Selected", "Please select a site to edit.")
            return
        
        site_file = self.sites_dir / f"{site_key}.env"
        if not site_file.exists():
            QMessageBox.warning(
                self, "Site Not Found",
                f"Configuration file not found for '{site_key}'."
            )
            return
        
        # Try to find this site in API data to get server info
        site_data = None
        for api_site in self.api_sites_data:
            if api_site.get('title', '') == site_key:
                site_data = api_site
                break
        
        # If not in API data, create minimal site data from existing config
        if not site_data:
            existing_config = {}
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Handle bash $'...' syntax for SYNC_ITEMS
                        if value.startswith("$'") and value.endswith("'"):
                            # Remove $' and trailing '
                            value = value[2:-1]
                            # Convert escaped newlines to actual newlines
                            value = value.replace('\\n', '\n')
                        else:
                            # Normal quoted value
                            value = value.strip('"')
                        
                        existing_config[key] = value
            
            site_data = {
                'title': site_key,
                'server': [{
                    'title': 'Manual Entry',
                    'ip': existing_config.get('SSH_HOST', '')
                }]
            }
        
        # Open configuration dialog
        dialog = ConfigureSiteDialog(site_data, self.settings_manager, self)
        
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            
            try:
                # Write the updated configuration
                with open(site_file, 'w') as f:
                    f.write(f'SITE_KEY="{config["site_key"]}"\n')
                    f.write(f'SSH_HOST="{config["ssh_host"]}"\n')
                    f.write(f'SSH_PORT="{config["ssh_port"]}"\n')
                    f.write(f'SSH_USER="{config["ssh_user"]}"\n')
                    f.write(f'LOCAL_ROOT="{config["local_root"]}"\n')
                    f.write(f'REMOTE_ROOT="{config["remote_root"]}"\n')
                    # Use bash $'...\n...' syntax for multi-line SYNC_ITEMS
                    sync_items_escaped = config["sync_items"].replace('\n', '\\n')
                    f.write(f"SYNC_ITEMS=$'{sync_items_escaped}'\n")
                    f.write(f'RSYNC_DELETE="{config["rsync_delete"]}"\n')
                    debounce = config.get('debounce_seconds', '3')
                    f.write(f'DEBOUNCE_SECONDS="{debounce}"\n')
                
                self.log_output(f"\n✓ Updated site configuration: {config['site_key']}\n", "success")
                
                # Reload sites to reflect changes
                self.load_sites()
                
                # Reselect the edited site
                index = self.site_combo.findText(config['site_key'])
                if index >= 0:
                    self.site_combo.setCurrentIndex(index)
                
                QMessageBox.information(
                    self, "Success",
                    f"Site '{config['site_key']}' updated successfully!"
                )
                
            except Exception as e:
                self.log_output(f"\n✗ Error updating site: {e}\n", "error")
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to update site configuration:\n{e}"
                )
        
    def on_site_selected(self):
        """Handle site selection"""
        site_key = self.site_combo.currentData()
        if not site_key:
            self.selected_site_label.setText("<b>Selected:</b> None")
            self.selected_site_label.setStyleSheet("""
                color: #6b7280;
                padding: 8px 12px;
                background-color: #f9fafb;
                border-radius: 6px;
                border: 1px solid #e5e7eb;
                font-size: 13px;
            """)
            return
        
        site_file = self.sites_dir / f"{site_key}.env"
        if site_file.exists():
            config = {}
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Handle bash $'...' syntax
                        if value.startswith("$'") and value.endswith("'"):
                            value = value[2:-1].replace('\\n', '\n')
                        else:
                            value = value.strip('"')
                        
                        config[key] = value
            
            host = config.get('SSH_HOST', 'N/A')
            user = config.get('SSH_USER', 'N/A')
            port = config.get('SSH_PORT', 'N/A')
            local_root = config.get('LOCAL_ROOT', '')
            
            # Update prominent selected site label
            self.selected_site_label.setText(f"<b>🎯 Selected:</b> {site_key}")
            self.selected_site_label.setStyleSheet("""
                color: #1f2937;
                padding: 8px 12px;
                background-color: #dbeafe;
                border-radius: 6px;
                border: 2px solid #3b82f6;
                font-size: 13px;
                font-weight: bold;
            """)
            
            # Check local file age
            age_warning = ""
            days_old = None
            if local_root:
                local_path = Path(local_root).expanduser()
                if local_path.exists():
                    days_old = self.get_local_files_age(local_path)
                    if days_old is not None:
                        if days_old > 7:
                            age_warning = f" | <span style='color: #dc2626; font-weight: bold;'>⚠️ {days_old} days old - Clean or Pull!</span>"
                        elif days_old > 3:
                            age_warning = f" | <span style='color: #f59e0b;'>⏱️ {days_old} days old</span>"
                        else:
                            age_warning = f" | <span style='color: #059669;'>✓ {days_old} days old</span>"
            
            info_text = f"📡 {user}@{host}:{port}{age_warning}"
            self.site_info_label.setText(info_text)
            
            # Change background color if files are old
            if days_old is not None and days_old > 7:
                self.site_info_label.setStyleSheet("""
                    color: #374151;
                    padding: 6px 8px;
                    background-color: #fee2e2;
                    border-radius: 4px;
                    border: 1px solid #fca5a5;
                    font-size: 11px;
                """)
            else:
                self.site_info_label.setStyleSheet("""
                    color: #374151;
                    padding: 6px 8px;
                    background-color: #f3f4f6;
                    border-radius: 4px;
                    border: 1px solid #d1d5db;
                    font-size: 11px;
                """)
        else:
            self.site_info_label.setText("⚠️ Site needs configuration - click 'Edit Site'")
            self.site_info_label.setStyleSheet("""
                color: #6b7280;
                padding: 6px 8px;
                background-color: #f9fafb;
                border-radius: 4px;
                border: 1px solid #e5e7eb;
                font-size: 11px;
            """)
    
    def check_authentication(self):
        """Check if WordPress authentication is configured"""
        wp_url = self.settings_manager.get('wp_url', '')
        wp_username = self.settings_manager.get('wp_username', '')
        wp_password = self.settings_manager.get('wp_app_password', '')
        
        if not wp_url or not wp_username or not wp_password:
            self.lock_gui("WordPress authentication not configured")
            QMessageBox.warning(
                self, "Authentication Required",
                "WordPress credentials are required to use this application.\n\n"
                "Please configure your WordPress URL, username, and application password "
                "in Settings > Preferences."
            )
            self.open_settings()
        else:
            # Validate credentials
            self.log_output("Validating WordPress credentials...\n", "info")
            self.startup_auth_thread = AuthThread(wp_url, wp_username, wp_password)
            self.startup_auth_thread.auth_result.connect(self.handle_startup_auth)
            self.startup_auth_thread.start()
    
    def handle_startup_auth(self, success, message):
        """Handle authentication result on startup"""
        # Cleanup the auth thread first
        if self.startup_auth_thread:
            self.startup_auth_thread.blockSignals(True)
            self.startup_auth_thread.deleteLater()
            self.startup_auth_thread = None
            
        if success:
            self.log_output(f"✓ {message}\n", "success")
            self.settings_manager.set('authenticated', True)
            self.settings_manager.save_settings()
            self.unlock_gui()
            # Automatically fetch sites from API
            self.fetch_sites_from_api_silent()
        else:
            self.log_output(f"✗ {message}\n", "error")
            self.settings_manager.set('authenticated', False)
            self.settings_manager.save_settings()
            self.lock_gui("WordPress authentication failed")
            QMessageBox.critical(
                self, "Authentication Failed",
                f"Failed to authenticate with WordPress:\n{message}\n\n"
                "The application will be locked until you provide valid credentials.\n"
                "Go to Settings > Preferences to update your credentials."
            )
    
    def lock_gui(self, reason="Not authenticated"):
        """Lock the GUI controls"""
        self.pull_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        self.watch_btn.setEnabled(False)
        self.ssh_btn.setEnabled(True)
        self.test_connection_btn.setEnabled(False)
        self.site_combo.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.new_site_btn.setEnabled(False)
        self.sync_api_btn.setEnabled(False)
        self.edit_site_btn.setEnabled(False)
        self.statusBar().showMessage(f"🔒 Locked: {reason}")
        self.log_output(f"\n⚠️  GUI locked: {reason}\n", "warning")
    
    def unlock_gui(self):
        """Unlock the GUI controls"""
        self.site_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.new_site_btn.setEnabled(True)
        self.sync_api_btn.setEnabled(True)
        self.edit_site_btn.setEnabled(True)
        self.update_button_states(True)
        self.statusBar().showMessage("Ready")
        self.log_output("✓ GUI unlocked\n", "success")
    
    def open_settings(self):
        """Open the settings dialog"""
        dialog = SettingsDialog(self.settings_manager, self)
        if dialog.exec_() == QDialog.Accepted:
            self.log_output("\nSettings saved. Checking authentication...\n", "info")
            # Re-check authentication after settings change
            self.check_authentication()
    
    def update_button_states(self, enabled):
        """Enable or disable action buttons"""
        self.pull_btn.setEnabled(enabled)
        self.push_btn.setEnabled(enabled)
        self.watch_btn.setEnabled(enabled)
        self.test_connection_btn.setEnabled(enabled)
        self.ssh_btn.setEnabled(enabled)
        self.open_in_editor_btn.setEnabled(enabled)
        self.open_rights_btn.setEnabled(enabled)
        self.close_rights_btn.setEnabled(enabled)
        # Maintenance button only enabled when site is selected
        self.maintenance_btn.setEnabled(False)  # Keep disabled for now
        # Dev Env buttons stay disabled
        self.dev_env_btn.setEnabled(False)
    
    def run_pull(self):
        """Execute pull command"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        script_path = self.bin_dir / "pull"
        args = [str(script_path), site_key]
        
        self.execute_command(args, "Pull")
    
    def run_push(self):
        """Execute push command"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        # SAFETY CHECK: Prevent push when local files don't exist
        site_file = self.sites_dir / f"{site_key}.env"
        if site_file.exists():
            config = {}
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Handle bash $'...' syntax
                        if value.startswith("$'") and value.endswith("'"):
                            value = value[2:-1].replace('\\n', '\n')
                        else:
                            value = value.strip('"')
                        
                        config[key] = value
            
            local_root = config.get('LOCAL_ROOT', '')
            if local_root:
                local_path = Path(local_root).expanduser()
                
                # Check if local directory exists
                if not local_path.exists():
                    QMessageBox.critical(
                        self, "⚠️ Cannot Push - No Local Files",
                        f"<b>Push aborted for safety!</b>\n\n"
                        f"Local directory does not exist:\n{local_path}\n\n"
                        f"<span style='color: #dc2626;'>⚠️ Pushing would delete all files on the server!</span>\n\n"
                        f"<b>What to do:</b>\n"
                        f"1. Use 'Pull' to download files from server first\n"
                        f"2. Make your changes locally\n"
                        f"3. Then use 'Push' to upload"
                    )
                    self.log_output("❌ Push aborted: Local directory does not exist\n", "error")
                    return
                
                # Check if local directory has any files (not empty)
                has_files = False
                try:
                    for root, dirs, files in os.walk(local_path):
                        # Skip hidden directories
                        dirs[:] = [d for d in dirs if not d.startswith('.')]
                        # Check for non-hidden files
                        if any(f for f in files if not f.startswith('.')):
                            has_files = True
                            break
                except Exception as e:
                    self.log_output(f"Warning: Could not check local files: {str(e)}\n", "error")
                
                if not has_files:
                    QMessageBox.critical(
                        self, "⚠️ Cannot Push - Local Directory Empty",
                        f"<b>Push aborted for safety!</b>\n\n"
                        f"Local directory is empty:\n{local_path}\n\n"
                        f"<span style='color: #dc2626;'>⚠️ Pushing would delete all files on the server!</span>\n\n"
                        f"<b>What to do:</b>\n"
                        f"1. Use 'Pull' to download files from server first\n"
                        f"2. Make your changes locally\n"
                        f"3. Then use 'Push' to upload"
                    )
                    self.log_output("❌ Push aborted: Local directory is empty\n", "error")
                    return
        
        reply = QMessageBox.question(
            self, "Confirm Push",
            "Are you sure you want to push local changes to the remote server?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        
        script_path = self.bin_dir / "push"
        args = [str(script_path), site_key]
        
        self.execute_command(args, "Push")
    
    def run_maintenance(self):
        """Execute maintenance operations (placeholder)"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        QMessageBox.information(
            self, "Coming Soon",
            "Maintenance operations functionality will be implemented soon.\n\n"
            f"Selected site: {site_key}"
        )
        self.log_output(f"\n🔧 Maintenance operations - Coming soon for site: {site_key}\n", "info")
    
    def setup_dev_environment(self):
        """Setup development environment (placeholder)"""
        QMessageBox.information(
            self, "Coming Soon",
            "Development environment setup functionality will be implemented soon.\n\n"
            "This will help you quickly set up a local development environment."
        )
        self.log_output(f"\n🚀 Development environment setup - Coming soon\n", "info")
    
    def toggle_watch(self):
        """Start or stop watch mode"""
        if self.watch_thread and self.watch_thread.isRunning():
            self.stop_watch()
        else:
            self.start_watch()
    
    def start_watch(self):
        """Start watch mode"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        script_path = self.bin_dir / "watch"
        args = [str(script_path), site_key]
        
        self.log_output(f"\n{'='*60}\n")
        self.log_output(f"Starting watch mode for site: {site_key}\n", "info")
        self.log_output(f"{'='*60}\n\n")
        
        self.watch_thread = CommandThread(args, self.project_root)
        self.watch_thread.output_signal.connect(self.append_output)
        self.watch_thread.finished_signal.connect(self.on_watch_finished)
        self.watch_thread.start()
        
        # Update tray icon
        self.update_tray_watch_status(True)
        
        self.watch_btn.setText("Stop")
        self.watch_btn.setObjectName("watchBtnActive")
        self.watch_btn.setStyleSheet("")
        self.watch_btn.style().unpolish(self.watch_btn)
        self.watch_btn.style().polish(self.watch_btn)
        self.pull_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        self.test_connection_btn.setEnabled(False)
        # Keep SSH, maintenance, and dev env buttons disabled
        self.ssh_btn.setEnabled(True)
        self.maintenance_btn.setEnabled(False)
        self.dev_env_btn.setEnabled(False)
        self.statusBar().showMessage("⚡ Watch mode active")
    
    def stop_watch(self):
        """Stop watch mode - non-blocking"""
        if self.watch_thread and self.watch_thread.isRunning():
            self.log_output("\nStopping watch mode...\n", "warning")
            self._stopping_watch = True
            
            # Update UI immediately to show stopping state
            self.watch_btn.setText("Stopping...")
            self.watch_btn.setEnabled(False)
            self.statusBar().showMessage("Stopping watch...")
            
            # Stop the thread (terminates subprocess)
            self.watch_thread.stop()
            
            # Set a safety timeout in case the thread doesn't finish
            QTimer.singleShot(3000, self._force_watch_cleanup)
            
            # Don't wait here - let on_watch_finished handle cleanup
            # The thread will finish shortly and emit finished_signal
        elif not self.watch_thread:
            # Already stopped, just update UI
            self._reset_watch_ui()
    
    def _force_watch_cleanup(self):
        """Force cleanup if watch thread doesn't stop gracefully"""
        if self._stopping_watch and self.watch_thread:
            self.log_output("\n⚠ Force stopping watch thread...\n", "warning")
            try:
                self.watch_thread.terminate()
                self.watch_thread.blockSignals(True)
                self.watch_thread.deleteLater()
            except:
                pass
            self.watch_thread = None
            self._stopping_watch = False
            self._reset_watch_ui()
    
    def on_watch_finished(self, return_code):
        """Handle watch mode completion"""
        # Only process if we still have a watch thread reference
        if not self.watch_thread:
            return
            
        if self._stopping_watch:
            self.log_output("\nWatch mode stopped\n", "info")
        elif return_code != 0:
            self.log_output(f"\nWatch mode exited with code {return_code}\n", "error")
        else:
            # Watch exited unexpectedly with code 0 - provide feedback
            self.log_output("\nWatch mode stopped unexpectedly\n", "warning")
            self.log_output("This may be due to: missing fswatch, invalid paths, or permissions issues\n", "warning")
            self.log_output("Try: 1) Verify fswatch is installed (brew install fswatch)\n", "info")
            self.log_output("     2) Check that local paths exist\n", "info")
            self.log_output("     3) Review the output above for errors\n", "info")
        
        self._stopping_watch = False
        self._reset_watch_ui()
        
        # Properly cleanup the watch thread
        try:
            self.watch_thread.blockSignals(True)
            self.watch_thread.deleteLater()
        except:
            pass
        self.watch_thread = None
    
    def _reset_watch_ui(self):
        """Reset watch button and UI to ready state"""
        # Update tray icons and clear sync status
        self.update_tray_watch_status(False)
        self.update_tray_sync_status(False)
        
        self.watch_btn.setText("Watch")
        self.watch_btn.setObjectName("watchBtn")
        self.watch_btn.setStyleSheet("")
        self.watch_btn.style().unpolish(self.watch_btn)
        self.watch_btn.style().polish(self.watch_btn)
        self.watch_btn.setEnabled(True)
        self.pull_btn.setEnabled(True)
        self.push_btn.setEnabled(True)
        self.test_connection_btn.setEnabled(True)
        # Keep SSH, maintenance, and dev env buttons disabled
        self.ssh_btn.setEnabled(True)
        self.maintenance_btn.setEnabled(False)
        self.dev_env_btn.setEnabled(False)
        self.statusBar().showMessage("Ready")
    
    def open_ssh_terminal(self):
        """Open an embedded SSH terminal connection to the selected site"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        # Read site configuration
        site_file = self.sites_dir / f"{site_key}.env"
        if not site_file.exists():
            QMessageBox.warning(self, "Site Not Found", f"Configuration file not found for {site_key}")
            return
        
        try:
            config = {}
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Handle bash $'...' syntax
                        if value.startswith("$'") and value.endswith("'"):
                            value = value[2:-1].replace('\\n', '\n')
                        else:
                            value = value.strip('"')
                        
                        config[key] = value
            
            ssh_host = config.get('SSH_HOST', '')
            ssh_port = config.get('SSH_PORT', '22')
            ssh_user = config.get('SSH_USER', '')
            
            if not ssh_host or not ssh_user:
                QMessageBox.warning(
                    self, "Invalid Configuration",
                    "SSH host and user are required in the site configuration."
                )
                return
            
            # Get SSH key path from settings
            ssh_key = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
            
            # Build SSH command
            ssh_cmd = f"ssh -p {ssh_port}"
            if ssh_key:
                ssh_cmd += f" -i {ssh_key}"
            ssh_cmd += f" {ssh_user}@{ssh_host}"
            
            # Open embedded terminal dialog
            self.log_output(f"\n→ Opening SSH terminal to {ssh_user}@{ssh_host}:{ssh_port}\n", "info")
            
            terminal_dialog = SSHTerminalDialog(ssh_cmd, site_key, self)
            terminal_dialog.exec_()
            
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to open SSH terminal:\n{e}"
            )
            self.log_output(f"\n✗ Error opening SSH terminal: {e}\n", "error")
    
    def find_vscode_command(self):
        """Find VS Code command path, returns None if not found"""
        # Check common installation locations
        import os
        
        possible_paths = [
            '/usr/local/bin/code',
            '/opt/homebrew/bin/code',
            str(Path.home() / '.local' / 'bin' / 'code'),
            '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code'
        ]
        
        # First check if 'code' is in PATH with expanded environment
        env = os.environ.copy()
        env['PATH'] = '/usr/local/bin:/opt/homebrew/bin:' + env.get('PATH', '')
        
        try:
            result = subprocess.run(
                ['which', 'code'],
                capture_output=True,
                text=True,
                env=env,
                timeout=2
            )
            
            if result.returncode == 0 and result.stdout.strip():
                code_path = result.stdout.strip()
                if Path(code_path).exists():
                    return code_path
        except Exception:
            pass
        
        # Check common paths directly
        for path in possible_paths:
            if Path(path).exists():
                return path
        
        return None
    
    def open_in_editor(self):
        """Open the selected site's local folder in VS Code or configured editor"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        # Read site configuration to get LOCAL_ROOT
        site_file = self.sites_dir / f"{site_key}.env"
        if not site_file.exists():
            QMessageBox.warning(self, "Site Not Found", f"Configuration file not found for {site_key}")
            return
        
        try:
            config = {}
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Handle bash $'...' syntax
                        if value.startswith("$'") and value.endswith("'"):
                            value = value[2:-1].replace('\\n', '\n')
                        else:
                            value = value.strip('"')
                        
                        config[key] = value
            
            local_root = config.get('LOCAL_ROOT', '')
            if not local_root:
                QMessageBox.warning(
                    self, "No Local Path",
                    "LOCAL_ROOT is not configured for this site.\n\n"
                    "Please configure the site first."
                )
                return
            
            # Expand ~ to user's home directory
            local_path = Path(local_root).expanduser()
            
            # Check if path exists
            if not local_path.exists():
                reply = QMessageBox.question(
                    self, "Path Not Found",
                    f"Local path does not exist:\n{local_path}\n\n"
                    "Do you want to create it?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    try:
                        local_path.mkdir(parents=True, exist_ok=True)
                        self.log_output(f"✓ Created directory: {local_path}\n", "success")
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Failed to create directory:\n{e}")
                        return
                else:
                    return
            
            # Get user's editor preference
            editor_pref = self.settings_manager.get('preferred_editor_path', 'auto').strip().lower()
            self.log_output(f"→ Opening {local_path}...\n", "info")
            
            import os
            env = os.environ.copy()
            env['PATH'] = '/usr/local/bin:/opt/homebrew/bin:' + env.get('PATH', '')
            
            editor_command = None
            editor_name = "folder"
            
            # Determine which editor to use
            if editor_pref == 'finder' or editor_pref == 'open':
                # User explicitly wants Finder/default app
                pass
            elif editor_pref == 'auto' or editor_pref == '':
                # Auto-detect VS Code
                editor_command = self.find_vscode_command()
                if editor_command:
                    editor_name = "VS Code"
                    self.log_output(f"  Found VS Code at: {editor_command}\n", "info")
                else:
                    self.log_output(f"  VS Code not found, using default app\n", "info")
            else:
                # User specified a custom path
                custom_path = Path(editor_pref).expanduser()
                if custom_path.exists():
                    editor_command = str(custom_path)
                    editor_name = custom_path.name
                    self.log_output(f"  Using custom editor: {editor_command}\n", "info")
                else:
                    self.log_output(f"  ⚠ Custom editor not found: {editor_pref}\n", "warning")
                    self.log_output(f"  Falling back to default app\n", "info")
            
            # Open with the determined editor
            try:
                if editor_command:
                    # Use specific code editor
                    import time
                    process = subprocess.Popen(
                        [editor_command, str(local_path)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=env
                    )
                    
                    # Wait briefly to check if it started successfully
                    time.sleep(0.3)
                    poll = process.poll()
                    
                    if poll is None or poll == 0:
                        self.log_output(f"✓ Opened in {editor_name}\n", "success")
                    else:
                        stdout, stderr = process.communicate(timeout=1)
                        self.log_output(f"✗ {editor_name} exited with code {poll}\n", "error")
                        if stderr:
                            self.log_output(f"  Error: {stderr.strip()}\n", "error")
                        # Fall back to Finder
                        subprocess.Popen(['open', str(local_path)])
                        self.log_output(f"→ Opened in Finder instead\n", "info")
                else:
                    # Use default app (Finder on macOS)
                    subprocess.Popen(['open', str(local_path)])
                    self.log_output(f"✓ Opened in Finder\n", "success")
                    
            except Exception as e:
                # Final fallback
                self.log_output(f"✗ Error: {e}\n", "error")
                try:
                    subprocess.Popen(['open', str(local_path)])
                    self.log_output(f"→ Opened in Finder (fallback)\n", "info")
                except Exception as e2:
                    QMessageBox.critical(
                        self, "Error",
                        f"Failed to open folder:\n{e2}"
                    )
                    self.log_output(f"✗ Failed to open folder: {e2}\n", "error")
        
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to open in editor:\n{e}"
            )
            self.log_output(f"\n✗ Error opening in editor: {e}\n", "error")
    
    def open_rights(self):
        """Open file permissions on the remote server"""
        if self.current_thread and self.current_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Another operation is in progress. Please wait.")
            return
        
        site = self.site_combo.currentText()
        if not site:
            return
        
        # Load site config
        config_file = self.sites_dir / f"{site}.env"
        if not config_file.exists():
            QMessageBox.critical(self, "Error", f"Configuration file not found for {site}")
            return
        
        config = {}
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key] = value.strip('"').strip("'")
        
        # Build SSH command
        ssh_host = config.get('SSH_HOST')
        ssh_port = config.get('SSH_PORT', '22')
        ssh_user = config.get('SSH_USER')
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        if not all([ssh_host, ssh_user]):
            QMessageBox.critical(self, "Error", "SSH configuration incomplete")
            return
        
        # Open rights commands
        commands = [
            'find . -name public_html -type d -exec chmod 775 {} \\;',
            'find ./public_html/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/ -type d -exec chmod 755 {} \\;'
        ]
        
        # Combine commands
        remote_command = ' && '.join(commands)
        
        self.log_output(f"\n=== Opening Rights on {site} ===", 'info')
        self.log_output(f"Connecting to {ssh_user}@{ssh_host}...\n")
        
        ssh_command = [
            'ssh',
            '-i', str(ssh_key_expanded),
            '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f'{ssh_user}@{ssh_host}',
            remote_command
        ]
        
        try:
            result = subprocess.run(
                ssh_command,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )
            
            if result.returncode == 0:
                self.log_output("✅ Rights opened successfully!\n", 'success')
                if result.stdout:
                    self.log_output(result.stdout)
            else:
                self.log_output(f"❌ Error opening rights (exit code {result.returncode})\n", 'error')
                if result.stderr:
                    self.log_output(result.stderr, 'error')
                if result.stdout:
                    self.log_output(result.stdout)
                    
        except subprocess.TimeoutExpired:
            self.log_output("❌ Operation timed out (5 minutes)\n", 'error')
        except FileNotFoundError:
            self.log_output("❌ SSH command not found. Please ensure SSH is installed.\n", 'error')
        except Exception as e:
            self.log_output(f"❌ Error: {str(e)}\n", 'error')
    
    def close_rights(self):
        """Close/restrict file permissions on the remote server"""
        if self.current_thread and self.current_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Another operation is in progress. Please wait.")
            return
        
        site = self.site_combo.currentText()
        if not site:
            return
        
        # Confirm action as this is restrictive
        reply = QMessageBox.question(
            self,
            "Confirm Close Rights",
            f"Are you sure you want to restrict file permissions on {site}?\n\n"
            "This will set restrictive permissions for security.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Load site config
        config_file = self.sites_dir / f"{site}.env"
        if not config_file.exists():
            QMessageBox.critical(self, "Error", f"Configuration file not found for {site}")
            return
        
        config = {}
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key] = value.strip('"').strip("'")
        
        # Build SSH command
        ssh_host = config.get('SSH_HOST')
        ssh_port = config.get('SSH_PORT', '22')
        ssh_user = config.get('SSH_USER')
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        if not all([ssh_host, ssh_user]):
            QMessageBox.critical(self, "Error", "SSH configuration incomplete")
            return
        
        # Close rights commands
        commands = [
            'find ./public_html/ -type f -exec chmod 444 {} \\;',
            'find ./public_html/ -type d -exec chmod 555 {} \\;',
            'find ./public_html/.htaccess -type f -exec chmod 444 {} \\;',
            'find ./public_html/wp-config.php -type f -exec chmod 400 {} \\;',
            'find ./public_html/wp-content/uploads/ -name .htaccess -type f -exec chmod 444 {} \\;',
            'find ./public_html/wp-content/uploads/ -name "sucuri" -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/sucuri/ -type f -exec chmod 655 {} \\;',
            'find ./public_html/wp-content/uploads/woo-product-feed-pro/xml/ -type f -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/woocommerce_uploads -R -type d -exec chmod 755 {} \\;',
            'find ./public_html/assets/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/backend-jaarverslag-2019/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/backend-jaarverslag-2020/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/backend-jaarverslag-2021/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/infinitewp/backups -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/infinitewp/temp -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/ewww/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/ewww/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/litespeed/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/litespeed/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/wp-rocket-config -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/cache/wp-rocket -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/cache/min -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/cache/busting -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/languages -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/updraft -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/plugins/litespeed-cache/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/plugins/litespeed-cache/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/themes/bridge/js -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/themes/bridge/css -type d -exec chmod 755 {} \\;',
            'find . -name public_html -type d -exec chmod 755 {} \\;',
            'find ./public_html/ -name "wp-content" -type d -exec chmod 755 {} \\;',
            'find ./public_html/.well-known/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/media/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/assets/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/cachedTiles/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/cachedTiles/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/ -name "pdftmp" -type d -exec chmod 755 {} \\;',
            'find ./public_html/pdfs -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/2020/08/data/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/uploads/sherpa-stock-sync.log -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/uploads/temp/ips/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/temp/ips/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/plugins/order-signature-for-woocommerce-pro/assets/swph_sign_images/ -type d -exec chmod 755 {} \\;'
        ]
        
        # Combine commands with && and handle errors gracefully (2>/dev/null)
        remote_command = ' && '.join([f"{cmd} 2>/dev/null || true" for cmd in commands])
        
        self.log_output(f"\n=== Closing Rights on {site} ===", 'info')
        self.log_output(f"Connecting to {ssh_user}@{ssh_host}...\n")
        self.log_output("This may take a few minutes...\n")
        
        ssh_command = [
            'ssh',
            '-i', str(ssh_key_expanded),
            '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f'{ssh_user}@{ssh_host}',
            remote_command
        ]
        
        try:
            result = subprocess.run(
                ssh_command,
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes timeout (longer for many commands)
            )
            
            if result.returncode == 0:
                self.log_output("✅ Rights closed successfully!\n", 'success')
                if result.stdout:
                    self.log_output(result.stdout)
            else:
                self.log_output(f"⚠️ Completed with exit code {result.returncode}\n", 'info')
                self.log_output("Some commands may have failed (normal if paths don't exist)\n")
                if result.stderr:
                    self.log_output(result.stderr, 'error')
                if result.stdout:
                    self.log_output(result.stdout)
                    
        except subprocess.TimeoutExpired:
            self.log_output("❌ Operation timed out (10 minutes)\n", 'error')
        except FileNotFoundError:
            self.log_output("❌ SSH command not found. Please ensure SSH is installed.\n", 'error')
        except Exception as e:
            self.log_output(f"❌ Error: {str(e)}\n", 'error')
    
    def test_connection(self):
        """Test SSH connection to the selected site"""
        site_key = self.site_combo.currentData()
        if not site_key:
            QMessageBox.warning(self, "No Site", "Please select a site first.")
            return
        
        # Read site configuration
        site_file = self.sites_dir / f"{site_key}.env"
        if not site_file.exists():
            QMessageBox.warning(self, "Site Not Found", f"Configuration file not found for {site_key}")
            return
        
        try:
            config = {}
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Handle bash $'...' syntax
                        if value.startswith("$'") and value.endswith("'"):
                            value = value[2:-1].replace('\\n', '\n')
                        else:
                            value = value.strip('"')
                        
                        config[key] = value
            
            ssh_host = config.get('SSH_HOST', '')
            ssh_port = config.get('SSH_PORT', '22')
            ssh_user = config.get('SSH_USER', '')
            
            if not ssh_host or not ssh_user:
                QMessageBox.warning(
                    self, "Invalid Configuration",
                    "SSH host and user are required in the site configuration."
                )
                return
            
            # Get SSH key path from settings
            ssh_key = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
            
            # Build SSH test command (just connect and exit)
            ssh_cmd = ['ssh', '-p', ssh_port]
            if ssh_key:
                ssh_cmd.extend(['-i', ssh_key])
            ssh_cmd.extend([
                '-o', 'ConnectTimeout=10',
                '-o', 'BatchMode=yes',
                '-q',
                f"{ssh_user}@{ssh_host}",
                'echo "Connection successful"'
            ])
            
            self.log_output(f"\n→ Testing SSH connection to {ssh_user}@{ssh_host}:{ssh_port}...\n", "info")
            self.test_connection_btn.setEnabled(False)
            self.test_connection_btn.setText("Testing...")
            
            # Run test in background
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=15
            )
            
            self.test_connection_btn.setEnabled(True)
            self.test_connection_btn.setText("Test Connection")
            
            if result.returncode == 0:
                self.log_output(f"✓ Connection successful!\n", "success")
                QMessageBox.information(
                    self, "Connection Successful",
                    f"Successfully connected to {ssh_user}@{ssh_host}:{ssh_port}\n\n"
                    f"SSH connection is working properly."
                )
            else:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                self.log_output(f"✗ Connection failed: {error_msg}\n", "error")
                QMessageBox.warning(
                    self, "Connection Failed",
                    f"Failed to connect to {ssh_user}@{ssh_host}:{ssh_port}\n\n"
                    f"Error: {error_msg}\n\n"
                    f"Please check your SSH configuration and credentials."
                )
                
        except subprocess.TimeoutExpired:
            self.test_connection_btn.setEnabled(True)
            self.test_connection_btn.setText("Test Connection")
            self.log_output(f"✗ Connection timeout\n", "error")
            QMessageBox.warning(
                self, "Connection Timeout",
                f"Connection to {ssh_user}@{ssh_host}:{ssh_port} timed out.\n\n"
                "Please check if the server is accessible."
            )
        except Exception as e:
            self.test_connection_btn.setEnabled(True)
            self.test_connection_btn.setText("Test Connection")
            self.log_output(f"✗ Error testing connection: {e}\n", "error")
            QMessageBox.critical(
                self, "Error",
                f"Failed to test connection:\n{e}"
            )
    
    def open_rights(self):
        """Open file permissions on the remote server"""
        if self.permissions_thread and self.permissions_thread.isRunning():
            QMessageBox.warning(self, "Busy", "A permissions operation is already in progress. Please wait.")
            return
        
        if self.current_thread and self.current_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Another operation is in progress. Please wait.")
            return
        
        site = self.site_combo.currentText()
        if not site:
            return
        
        # Load site config
        config_file = self.sites_dir / f"{site}.env"
        if not config_file.exists():
            QMessageBox.critical(self, "Error", f"Configuration file not found for {site}")
            return
        
        config = {}
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key] = value.strip('"').strip("'")
        
        # Build SSH command
        ssh_host = config.get('SSH_HOST')
        ssh_port = config.get('SSH_PORT', '22')
        ssh_user = config.get('SSH_USER')
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        if not all([ssh_host, ssh_user]):
            QMessageBox.critical(self, "Error", "SSH configuration incomplete")
            return
        
        # Open rights commands
        commands = [
            'find . -name public_html -type d -exec chmod 775 {} \\;',
            'find ./public_html/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/ -type d -exec chmod 755 {} \\;'
        ]
        
        # Combine commands
        remote_command = ' && '.join(commands)
        
        self.log_output(f"\n=== Opening Rights on {site} ===", 'info')
        self.log_output(f"\nConnecting to {ssh_user}@{ssh_host}...\n")
        
        ssh_command = [
            'ssh',
            '-i', str(ssh_key_expanded),
            '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f'{ssh_user}@{ssh_host}',
            remote_command
        ]
        
        # Disable button during operation
        self.open_rights_btn.setEnabled(False)
        self.open_rights_btn.setText("Opening...")
        self.update_button_states(False)
        
        # Run in background thread
        self.permissions_thread = PermissionsThread(ssh_command, "open")
        self.permissions_thread.output_signal.connect(self.log_output)
        self.permissions_thread.finished_signal.connect(self.on_open_rights_finished)
        self.permissions_thread.start()
    
    def on_open_rights_finished(self, success, message):
        """Handle completion of open rights operation"""
        self.open_rights_btn.setEnabled(True)
        self.open_rights_btn.setText("🔓 Open Rights")
        self.update_button_states(True)
        self.permissions_thread = None
    
    def close_rights(self):
        """Close/restrict file permissions on the remote server"""
        if self.permissions_thread and self.permissions_thread.isRunning():
            QMessageBox.warning(self, "Busy", "A permissions operation is already in progress. Please wait.")
            return
        
        if self.current_thread and self.current_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Another operation is in progress. Please wait.")
            return
        
        site = self.site_combo.currentText()
        if not site:
            return
        
        # Confirm action as this is restrictive
        reply = QMessageBox.question(
            self,
            "Confirm Close Rights",
            f"Are you sure you want to restrict file permissions on {site}?\n\n"
            "This will set restrictive permissions for security.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Load site config
        config_file = self.sites_dir / f"{site}.env"
        if not config_file.exists():
            QMessageBox.critical(self, "Error", f"Configuration file not found for {site}")
            return
        
        config = {}
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key] = value.strip('"').strip("'")
        
        # Build SSH command
        ssh_host = config.get('SSH_HOST')
        ssh_port = config.get('SSH_PORT', '22')
        ssh_user = config.get('SSH_USER')
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        if not all([ssh_host, ssh_user]):
            QMessageBox.critical(self, "Error", "SSH configuration incomplete")
            return
        
        # Close rights commands
        commands = [
            'find ./public_html/ -type f -exec chmod 444 {} \\;',
            'find ./public_html/ -type d -exec chmod 555 {} \\;',
            'find ./public_html/.htaccess -type f -exec chmod 444 {} \\;',
            'find ./public_html/wp-config.php -type f -exec chmod 400 {} \\;',
            'find ./public_html/wp-content/uploads/ -name .htaccess -type f -exec chmod 444 {} \\;',
            'find ./public_html/wp-content/uploads/ -name "sucuri" -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/sucuri/ -type f -exec chmod 655 {} \\;',
            'find ./public_html/wp-content/uploads/woo-product-feed-pro/xml/ -type f -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/woocommerce_uploads -R -type d -exec chmod 755 {} \\;',
            'find ./public_html/assets/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/backend-jaarverslag-2019/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/backend-jaarverslag-2020/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/backend-jaarverslag-2021/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/infinitewp/backups -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/infinitewp/temp -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/ewww/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/ewww/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/litespeed/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/litespeed/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/wp-rocket-config -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/cache/wp-rocket -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/cache/min -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/cache/busting -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/languages -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/updraft -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/plugins/litespeed-cache/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/plugins/litespeed-cache/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/themes/bridge/js -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/themes/bridge/css -type d -exec chmod 755 {} \\;',
            'find . -name public_html -type d -exec chmod 755 {} \\;',
            'find ./public_html/ -name "wp-content" -type d -exec chmod 755 {} \\;',
            'find ./public_html/.well-known/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/media/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/assets/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/cachedTiles/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/cachedTiles/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/ -name "pdftmp" -type d -exec chmod 755 {} \\;',
            'find ./public_html/pdfs -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/2020/08/data/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/uploads/sherpa-stock-sync.log -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/uploads/temp/ips/ -type d -exec chmod 755 {} \\;',
            'find ./public_html/wp-content/uploads/temp/ips/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/wp-content/plugins/order-signature-for-woocommerce-pro/assets/swph_sign_images/ -type d -exec chmod 755 {} \\;'
        ]
        
        # Combine commands with && and handle errors gracefully (2>/dev/null)
        remote_command = ' && '.join([f"{cmd} 2>/dev/null || true" for cmd in commands])
        
        self.log_output(f"\n=== Closing Rights on {site} ===", 'info')
        self.log_output(f"\nConnecting to {ssh_user}@{ssh_host}...\n")
        self.log_output("This may take a few minutes...\n")
        
        ssh_command = [
            'ssh',
            '-i', str(ssh_key_expanded),
            '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f'{ssh_user}@{ssh_host}',
            remote_command
        ]
        
        # Disable button during operation
        self.close_rights_btn.setEnabled(False)
        self.close_rights_btn.setText("Closing...")
        self.update_button_states(False)
        
        # Run in background thread
        self.permissions_thread = PermissionsThread(ssh_command, "close")
        self.permissions_thread.output_signal.connect(self.log_output)
        self.permissions_thread.finished_signal.connect(self.on_close_rights_finished)
        self.permissions_thread.start()
    
    def on_close_rights_finished(self, success, message):
        """Handle completion of close rights operation"""
        self.close_rights_btn.setEnabled(True)
        self.close_rights_btn.setText("🔒 Close Rights")
        self.update_button_states(True)
        self.permissions_thread = None
    
    def execute_command(self, args, action_name):
        """Execute a command in a thread"""
        self.log_output(f"\n{'='*60}\n")
        self.log_output(f"Running {action_name} for site: {self.site_combo.currentText()}\n", "info")
        self.log_output(f"{'='*60}\n\n")
        
        self.update_button_states(False)
        self.statusBar().showMessage(f"Running {action_name}...")
        
        self.current_thread = CommandThread(args, self.project_root)
        self.current_thread.output_signal.connect(self.append_output)
        self.current_thread.finished_signal.connect(
            lambda code: self.on_command_finished(code, action_name)
        )
        self.current_thread.start()
    
    def on_command_finished(self, return_code, action_name):
        """Handle command completion"""
        # Clear sync status
        self.update_tray_sync_status(False)
        
        if return_code == 0:
            self.log_output(f"\n✓ {action_name} completed successfully\n", "success")
            self.statusBar().showMessage("Ready")
        else:
            self.log_output(f"\n✗ {action_name} failed with exit code {return_code}\n", "error")
            self.statusBar().showMessage("Error occurred")
        
        self.update_button_states(True)
        
        # Properly cleanup the thread
        if self.current_thread:
            self.current_thread.blockSignals(True)
            self.current_thread.deleteLater()
            self.current_thread = None
    
    def stop_current_process(self):
        """Stop the currently running process"""
        if self.watch_thread and self.watch_thread.isRunning():
            self.stop_watch()
        elif self.current_thread and self.current_thread.isRunning():
            self.log_output("\nStopping current process...\n", "warning")
            self.current_thread.stop()
            self.current_thread.wait()
            self.current_thread = None
            self.update_tray_sync_status(False)
            self.update_button_states(True)
            self.statusBar().showMessage("Stopped")
    
    def log_output(self, text, level=None):
        """Add text to output with optional styling"""
        if level == "error":
            self.output_text.setTextColor(QColor("#dc2626"))  # Red
        elif level == "success":
            self.output_text.setTextColor(QColor("#16a34a"))  # Green
        elif level == "warning":
            self.output_text.setTextColor(QColor("#d97706"))  # Amber
        elif level == "info":
            self.output_text.setTextColor(QColor("#2563eb"))  # Blue
        else:
            self.output_text.setTextColor(QColor("#374151"))  # Dark gray
        
        self.output_text.append(text.rstrip())
        self.output_text.moveCursor(QTextCursor.End)
        self.output_text.setTextColor(QColor("#374151"))
    
    def append_output(self, text):
        """Append output from thread"""
        import time
        
        # Detect sync activity from output
        sync_started = False
        if "Pushing:" in text or "pushing" in text.lower():
            self.update_tray_sync_status(True, "Push")
            sync_started = True
        elif "Pulling:" in text or "pulling" in text.lower():
            self.update_tray_sync_status(True, "Pull")
            sync_started = True
        elif "rsync" in text.lower() and "building file list" in text.lower():
            self.update_tray_sync_status(True)
            sync_started = True
        
        # Track sync start time and set safety timeout
        if sync_started:
            self.sync_start_time = time.time()
            # Cancel any existing timeout
            if self.sync_timeout_timer:
                self.sync_timeout_timer.stop()
            # Set 15 second safety timeout to auto-clear sync status
            self.sync_timeout_timer = QTimer()
            self.sync_timeout_timer.setSingleShot(True)
            self.sync_timeout_timer.timeout.connect(lambda: self.update_tray_sync_status(False))
            self.sync_timeout_timer.start(15000)
        
        # Detect completion from explicit log messages
        if self.is_syncing:
            completion_detected = False
            
            # Check for completion markers
            if "✓ Push complete" in text or "✓ Pull complete" in text:
                completion_detected = True
            
            if completion_detected:
                # Cancel timeout timer since we detected completion
                if self.sync_timeout_timer:
                    self.sync_timeout_timer.stop()
                    self.sync_timeout_timer = None
                # Clear sync status after brief delay
                QTimer.singleShot(300, lambda: self.update_tray_sync_status(False))
        
        self.output_text.setTextColor(QColor("#374151"))
        self.output_text.insertPlainText(text)
        self.output_text.moveCursor(QTextCursor.End)
    
    def clear_output(self):
        """Clear the output area"""
        self.output_text.clear()
    
    def closeEvent(self, event):
        """Handle window close - minimize to tray or quit"""
        # Check if we should minimize to tray
        if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
            # If watch is running, still minimize to tray
            if self.watch_thread and self.watch_thread.isRunning():
                event.ignore()
                self.hide()
                # Show notification
                if not hasattr(self, '_tray_notification_shown'):
                    self.tray_icon.showMessage(
                        "Webmix Sync Starter",
                        "Watch mode is still running. Application minimized to menu bar.",
                        QSystemTrayIcon.Information,
                        3000
                    )
                    self._tray_notification_shown = True
                return
            else:
                # No watch running, just minimize to tray
                event.ignore()
                self.hide()
                # Show notification on first minimize
                if not hasattr(self, '_tray_notification_shown'):
                    self.tray_icon.showMessage(
                        "Webmix Sync Starter",
                        "Application minimized to menu bar. Click the icon to show the window again.",
                        QSystemTrayIcon.Information,
                        3000
                    )
                    self._tray_notification_shown = True
                return
        
        # No tray, handle normal quit
        # Helper function to safely cleanup a thread
        def cleanup_thread(thread, timeout=3000):
            if thread and thread.isRunning():
                try:
                    # Disconnect all signals to prevent crashes during cleanup
                    thread.blockSignals(True)
                    # Stop the thread if it has a stop method
                    if hasattr(thread, 'stop'):
                        thread.stop()
                    # Wait for thread to finish
                    if not thread.wait(timeout):
                        # Thread didn't finish, try to terminate
                        thread.terminate()
                        thread.wait(1000)
                    # Schedule for deletion
                    thread.deleteLater()
                except:
                    pass
                    
        if (self.watch_thread and self.watch_thread.isRunning()):
            reply = QMessageBox.question(
                self, "Watch Running",
                "Watch mode is still running. Stop it and quit?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                cleanup_thread(self.watch_thread)
                cleanup_thread(self.current_thread)
                cleanup_thread(self.startup_auth_thread, 2000)
                cleanup_thread(self.fetch_sites_thread, 2000)
                # Clear references
                self.watch_thread = None
                self.current_thread = None
                self.startup_auth_thread = None
                self.fetch_sites_thread = None
                event.accept()
            else:
                event.ignore()
        else:
            cleanup_thread(self.current_thread)
            cleanup_thread(self.startup_auth_thread, 2000)
            cleanup_thread(self.fetch_sites_thread, 2000)
            # Clear references
            self.current_thread = None
            self.startup_auth_thread = None
            self.fetch_sites_thread = None
            event.accept()
    
    def show_about(self):
        """Show about dialog"""
        QMessageBox.about(
            self,
            "About Webmix Sync Starter",
            f"<h3>Webmix Sync Starter</h3>"
            f"<p>Version {APP_VERSION}</p>"
            f"<p>A tool for syncing WordPress sites via SSH and WP-CLI</p>"
            f"<p>© 2026 Webmix B.V.</p>"
        )
    
    def check_for_updates(self, silent=False):
        """Check for app updates from GitHub Releases"""
        if not UPDATE_CHECKER_AVAILABLE:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Update Check Unavailable",
                    "Update checking is not available. Please check manually on GitHub."
                )
            return
        
        # Show progress dialog only if not silent
        progress = None
        if not silent:
            progress = QProgressDialog("Checking for updates...", None, 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setCancelButton(None)
            progress.show()
            QApplication.processEvents()
        
        try:
            checker = UpdateChecker(APP_VERSION, GITHUB_REPO_OWNER, GITHUB_REPO_NAME)
            has_update, latest_version, download_url, message = checker.check_for_updates()
            
            if progress:
                progress.close()
            
            if has_update:
                reply = QMessageBox.question(
                    self,
                    "Update Available",
                    f"<h3>New version available!</h3>"
                    f"<p><b>Current version:</b> {APP_VERSION}</p>"
                    f"<p><b>Latest version:</b> {latest_version}</p>"
                    f"<p><b>Release notes:</b></p>"
                    f"<p>{message[:500]}</p>"
                    f"<p>Would you like to download and install the update?</p>",
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    self.download_and_install_update(checker, download_url)
            else:
                if not silent:
                    QMessageBox.information(
                        self,
                        "No Updates",
                        f"You are running the latest version ({APP_VERSION}) | {message}"
                    )
        
        except Exception as e:
            if progress:
                progress.close()
            if not silent:
                QMessageBox.warning(
                    self,
                    "Update Check Failed",
                    f"Could not check for updates:\\n{str(e)}"
                )
    
    def get_local_files_age(self, local_path):
        """Get the age in days of the most recently modified file in local directory"""
        try:
            if not local_path.exists():
                return None
            
            latest_mtime = 0
            
            # Walk through all files in the directory
            for root, dirs, files in os.walk(local_path):
                # Skip hidden directories like .git, .DS_Store, etc.
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    # Skip hidden files
                    if file.startswith('.'):
                        continue
                    
                    file_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(file_path)
                        latest_mtime = max(latest_mtime, mtime)
                    except (OSError, PermissionError):
                        continue
            
            if latest_mtime == 0:
                return None
            
            # Calculate days difference
            now = time.time()
            age_seconds = now - latest_mtime
            age_days = int(age_seconds / 86400)  # Convert to days
            
            return age_days
            
        except Exception as e:
            self.log_output(f"Error checking file age: {str(e)}\n", "error")
            return None
    
    def clean_local_files(self):
        """Delete local files for the selected site (DOES NOT affect server)"""
        site_key = self.site_combo.currentData()
        
        if not site_key:
            QMessageBox.warning(
                self, "No Site Selected",
                "Please select a site first."
            )
            return
        
        # Load site config to get local path
        site_file = self.sites_dir / f"{site_key}.env"
        
        if not site_file.exists():
            QMessageBox.critical(
                self, "Error",
                f"Configuration file not found for {site_key}"
            )
            return
        
        config = {}
        with open(site_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Handle bash $'...' syntax
                    if value.startswith("$'") and value.endswith("'"):
                        value = value[2:-1].replace('\\n', '\n')
                    else:
                        value = value.strip('"')
                    
                    config[key] = value
        
        local_root = config.get('LOCAL_ROOT', '')
        if not local_root:
            QMessageBox.critical(
                self, "Error",
                "Local root path not configured for this site."
            )
            return
        
        local_path = Path(local_root).expanduser()
        
        if not local_path.exists():
            QMessageBox.information(
                self, "Already Clean",
                f"Local directory does not exist:\n{local_path}\n\nNothing to clean."
            )
            return
        
        # Get file age info for the confirmation dialog
        age_days = self.get_local_files_age(local_path)
        age_info = f"\n\nLast modified: {age_days} days ago" if age_days is not None else ""
        
        # Confirmation dialog with strong warnings
        reply = QMessageBox.question(
            self, "⚠️ Confirm Local File Deletion",
            f"<b>This will DELETE all local files for {site_key}</b>\n\n"
            f"Local path: {local_path}{age_info}\n\n"
            f"<span style='color: #dc2626;'>⚠️ This action CANNOT be undone!</span>\n\n"
            f"<span style='color: #059669;'>✓ Server files will NOT be affected</span>\n\n"
            f"You can always pull fresh files from the server after deletion.\n\n"
            f"Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            self.log_output("Local file cleanup cancelled.\n", "info")
            return
        
        # Perform deletion
        try:
            self.log_output(f"🗑️ Cleaning local files for {site_key}...\n", "info")
            self.log_output(f"Deleting: {local_path}\n")
            
            # Use shutil.rmtree to recursively delete the directory
            shutil.rmtree(local_path)
            
            self.log_output("✅ Local files deleted successfully!\n", "success")
            self.log_output("💡 Tip: Use 'Pull' to download fresh files from server\n", "info")
            
            # Update the site info display
            self.on_site_selected()
            
            QMessageBox.information(
                self, "Success",
                f"Local files deleted successfully!\n\n"
                f"The directory has been removed:\n{local_path}\n\n"
                f"Use 'Pull' to download fresh files from the server."
            )
            
        except PermissionError:
            error_msg = f"Permission denied. Cannot delete some files.\n"
            self.log_output(f"❌ {error_msg}", "error")
            QMessageBox.critical(
                self, "Permission Error",
                f"{error_msg}\nPlease check file permissions and try again."
            )
        except Exception as e:
            error_msg = f"Error deleting local files: {str(e)}\n"
            self.log_output(f"❌ {error_msg}", "error")
            QMessageBox.critical(
                self, "Error",
                f"Failed to delete local files:\n{str(e)}"
            )
    
    def download_and_install_update(self, checker, download_url):
        """Download and install an update"""
        # Create progress dialog for download
        progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        
        def update_progress(downloaded, total):
            if total > 0:
                percent = int((downloaded / total) * 100)
                progress.setValue(percent)
                QApplication.processEvents()
                if progress.wasCanceled():
                    raise Exception("Download cancelled by user")
        
        try:
            success, dmg_path, error = checker.download_update(download_url, update_progress)
            progress.close()
            
            if success:
                success, message = checker.install_update(dmg_path)
                
                if success:
                    # Quit immediately after opening DMG (no dialog)
                    QApplication.quit()
                else:
                    QMessageBox.warning(self, "Installation Error", message)
            else:
                QMessageBox.warning(self, "Download Failed", error)
                
        except Exception as e:
            progress.close()
            if "cancelled" not in str(e).lower():
                QMessageBox.warning(self, "Update Error", f"Update failed:\\n{str(e)}")

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Webmix Sync Starter")
    app.setOrganizationName("Webmix")
    app.setOrganizationDomain("webmix.nl")
    app.setStyle('Fusion')  # Modern look
    
    # Set application icon
    icon_path = Path(__file__).parent / "app-icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    
    window = WPSyncGUI()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
