#!/usr/bin/python3
"""
WordPress Sync Native GUI - Multi-Tab Version
A native desktop application using PyQt5 with support for multiple simultaneous sites
"""

# Version - should match setup.py
APP_VERSION = "1.2.4"
GITHUB_REPO_OWNER = "extreemsnel"
GITHUB_REPO_NAME = "webmix-sync-starter"

MAX_CONCURRENT_WATCHES = 5  # Hard limit on simultaneous watch modes

import sys
import subprocess
import threading
import json
import base64
from pathlib import Path
import os
import time
import shutil
import signal
from datetime import datetime, timedelta
import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTextEdit, QCheckBox, QMessageBox,
    QGroupBox, QFrame, QDialog, QLineEdit, QFormLayout, QDialogButtonBox,
    QFileDialog, QPlainTextEdit, QMenuBar, QAction, QTabWidget, QSpinBox,
    QListWidget, QListWidgetItem, QProgressDialog, QSystemTrayIcon, QMenu, QTabBar
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
            "preferred_editor_path": "auto",
            "default_debounce_seconds": 3,
            "open_tabs": [],  # List of site keys that were open
            "tab_order": []   # Order of tabs
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
            clean_password = self.app_password.replace(' ', '')
            credentials = f"{self.username}:{clean_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded}',
                'Content-Type': 'application/json'
            }
            
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
    sites_result = pyqtSignal(bool, object, str)
    
    def __init__(self, wp_url, username, app_password):
        super().__init__()
        self.wp_url = wp_url
        self.username = username
        self.app_password = app_password
    
    def run(self):
        try:
            clean_password = self.app_password.replace(' ', '')
            credentials = f"{self.username}:{clean_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded}',
                'Content-Type': 'application/json'
            }
            
            url = f"{self.wp_url.rstrip('/')}/wp-json/wp/v2/sites"
            params = {'per_page': 100, '_embed': 1}
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                sites_data = response.json()
                self.sites_result.emit(True, sites_data, f"Fetched {len(sites_data)} sites")
            else:
                self.sites_result.emit(False, [], f"API error: {response.status_code}")
                
        except Exception as e:
            self.sites_result.emit(False, [], str(e))


class CommandThread(QThread):
    """Thread for running shell commands"""
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    
    def __init__(self, command, cwd):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.process = None
        self._should_stop = False
    
    def run(self):
        try:
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.cwd,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            while not self._should_stop:
                line = self.process.stdout.readline()
                if line:
                    self.output_signal.emit(line)
                elif self.process.poll() is not None:
                    break
            
            if not self._should_stop:
                return_code = self.process.wait()
            else:
                self.process.terminate()
                self.process.wait()
                return_code = -1
            
            self.finished_signal.emit(return_code)
            
        except Exception as e:
            self.output_signal.emit(f"Error: {str(e)}\n")
            self.finished_signal.emit(-1)
    
    def stop(self):
        """Stop the running command"""
        self._should_stop = True
        if self.process:
            try:
                # Send SIGINT first for graceful shutdown (like Ctrl+C)
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    # Then SIGTERM
                    self.process.terminate()
                    self.process.wait(timeout=2)
                except:
                    try:
                        # Finally SIGKILL
                        self.process.kill()
                    except:
                        pass
            except:
                try:
                    self.process.kill()
                except:
                    pass


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
            clean_password = self.app_password.replace(' ', '')
            credentials = f"{self.username}:{clean_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded}',
                'Content-Type': 'application/json'
            }
            
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
    sites_result = pyqtSignal(bool, object, str)
    
    def __init__(self, wp_url, username, app_password):
        super().__init__()
        self.wp_url = wp_url
        self.username = username
        self.app_password = app_password
    
    def run(self):
        try:
            clean_password = self.app_password.replace(' ', '')
            credentials = f"{self.username}:{clean_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded}',
                'Content-Type': 'application/json'
            }
            
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


class RemoteFolderSelectorDialog(QDialog):
    """Dialog to fetch and select folders from remote server with navigation"""
    
    def __init__(self, ssh_host, ssh_port, ssh_user, remote_path, settings_manager, parent=None):
        super().__init__(parent)
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.base_remote_path = remote_path
        self.current_path = remote_path
        self.settings_manager = settings_manager
        self.selected_items = []
        
        self.setWindowTitle("Select Remote Folders/Files")
        self.setMinimumSize(600, 500)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        info_label = QLabel(f"Browsing: <b>{self.ssh_user}@{self.ssh_host}</b>")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        nav_layout = QHBoxLayout()
        
        self.up_btn = QPushButton("⬆ Up")
        self.up_btn.setToolTip("Go to parent directory")
        self.up_btn.clicked.connect(self.go_up)
        self.up_btn.setEnabled(False)
        nav_layout.addWidget(self.up_btn)
        
        self.path_label = QLabel(f"<b>{self.current_path}</b>")
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("padding: 5px; background-color: #f0f0f0; border-radius: 3px;")
        nav_layout.addWidget(self.path_label, 1)
        
        layout.addLayout(nav_layout)
        
        self.items_list = QListWidget()
        self.items_list.setSelectionMode(QListWidget.MultiSelection)
        self.items_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.items_list)
        
        help_label = QLabel("💡 Double-click folders to navigate, select items and click OK to add them")
        help_label.setStyleSheet("color: #666; font-style: italic; font-size: 11px;")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        
        self.status_label = QLabel("Click 'Fetch' to load folders and files...")
        self.status_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.status_label)
        
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
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def go_up(self):
        parent_path = str(Path(self.current_path).parent)
        
        if parent_path == self.current_path or len(parent_path) < len(self.base_remote_path):
            return
        
        self.current_path = parent_path
        self.path_label.setText(f"<b>{self.current_path}</b>")
        self.up_btn.setEnabled(self.current_path != self.base_remote_path)
        self.fetch_remote_items()
    
    def on_item_double_clicked(self, item):
        item_name = item.data(Qt.UserRole)
        display_text = item.text()
        
        if display_text.startswith("📁"):
            self.current_path = str(Path(self.current_path) / item_name)
            self.path_label.setText(f"<b>{self.current_path}</b>")
            self.up_btn.setEnabled(True)
            self.fetch_remote_items()
        
    def fetch_remote_items(self):
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching...")
        self.status_label.setText("Connecting to server...")
        self.items_list.clear()
        
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        list_cmd = f"cd '{self.current_path}' 2>/dev/null && (ls -1p 2>/dev/null || echo 'ERROR: Cannot access directory')"
        
        ssh_command = [
            'ssh', '-i', str(ssh_key_expanded), '-p', str(self.ssh_port),
            '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10',
            f'{self.ssh_user}@{self.ssh_host}', list_cmd
        ]
        
        try:
            result = subprocess.run(ssh_command, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                output = result.stdout.strip()
                
                if 'ERROR: Cannot access directory' in output:
                    self.status_label.setText("❌ Cannot access remote directory")
                    self.status_label.setStyleSheet("color: red;")
                    QMessageBox.warning(self, "Access Error",
                        f"Cannot access directory:\n{self.current_path}\n\n"
                        "Please check the remote path and permissions.")
                elif output:
                    items = [line.strip() for line in output.split('\n') if line.strip()]
                    
                    if items:
                        folders = [item.rstrip('/') for item in items if item.endswith('/')]
                        files = [item for item in items if not item.endswith('/')]
                        
                        for folder in sorted(folders):
                            item = QListWidgetItem(f"📁 {folder}")
                            item.setData(Qt.UserRole, folder)
                            self.items_list.addItem(item)
                        
                        for file in sorted(files):
                            item = QListWidgetItem(f"📄 {file}")
                            item.setData(Qt.UserRole, file)
                            self.items_list.addItem(item)
                        
                        self.status_label.setText(
                            f"✓ Found {len(folders)} folder(s) and {len(files)} file(s). Select items and click OK.")
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
                QMessageBox.critical(self, "Connection Error",
                    f"Failed to connect to server:\n{error_msg}")
                
        except subprocess.TimeoutExpired:
            self.status_label.setText("❌ Connection timeout")
            self.status_label.setStyleSheet("color: red;")
            QMessageBox.critical(self, "Timeout",
                "Connection to server timed out.\n"
                "Please check your network and server settings.")
        except Exception as e:
            self.status_label.setText(f"❌ Error: {str(e)}")
            self.status_label.setStyleSheet("color: red;")
            QMessageBox.critical(self, "Error", f"An error occurred:\n{str(e)}")
        finally:
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Refresh")
    
    def select_all_items(self):
        for i in range(self.items_list.count()):
            self.items_list.item(i).setSelected(True)
    
    def clear_selection(self):
        self.items_list.clearSelection()
    
    def accept_selection(self):
        selected_items = self.items_list.selectedItems()
        
        if not selected_items:
            QMessageBox.warning(self, "No Selection",
                "Please select at least one folder or file.")
            return
        
        self.selected_items = []
        
        for item in selected_items:
            item_name = item.data(Qt.UserRole)
            
            if self.current_path == self.base_remote_path:
                relative_path = item_name
            else:
                current_relative = Path(self.current_path).relative_to(self.base_remote_path)
                relative_path = str(current_relative / item_name)
            
            self.selected_items.append(relative_path)
        
        self.accept()
    
    def get_selected_items(self):
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
        
        config_group = QGroupBox("Sync Configuration")
        config_layout = QFormLayout()
        
        self.ssh_user_input = QLineEdit()
        self.ssh_user_input.setPlaceholderText("e.g., cpanel-username")
        config_layout.addRow("SSH User:", self.ssh_user_input)
        
        self.ssh_port_input = QLineEdit()
        self.ssh_port_input.setPlaceholderText("22")
        config_layout.addRow("SSH Port:", self.ssh_port_input)
        
        local_layout = QHBoxLayout()
        self.local_root_input = QLineEdit()
        self.local_root_input.setPlaceholderText(f"~/Sites/{site_title}")
        local_layout.addWidget(self.local_root_input)
        browse_local_btn = QPushButton("Browse...")
        browse_local_btn.clicked.connect(self.browse_local_root)
        local_layout.addWidget(browse_local_btn)
        config_layout.addRow("Local Root:", local_layout)
        
        self.remote_root_input = QLineEdit()
        self.remote_root_input.setPlaceholderText("e.g., /home/user/public_html/wp-content")
        config_layout.addRow("Remote Root:", self.remote_root_input)
        
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)
        
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
        
        debounce_layout = QHBoxLayout()
        debounce_layout.addWidget(QLabel("Watch debounce (seconds):"))
        self.debounce_input = QSpinBox()
        self.debounce_input.setRange(1, 30)
        self.debounce_input.setValue(3)
        self.debounce_input.setToolTip("Time to wait before syncing after detecting changes")
        debounce_layout.addWidget(self.debounce_input)
        debounce_layout.addStretch()
        layout.addLayout(debounce_layout)
        
        self.delete_check = QCheckBox("Enable delete sync (dangerous)")
        layout.addWidget(self.delete_check)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def load_defaults(self):
        site_title = self.site_data.get('title', '')
        
        self.ssh_user_input.setText(site_title)
        
        default_port = self.settings_manager.get('ssh_port', 22)
        self.ssh_port_input.setText(str(default_port))
        
        default_root = self.settings_manager.get('default_local_root', '~/sites')
        suggested_local = f"{default_root}/{site_title}".replace('//', '/')
        self.local_root_input.setText(suggested_local)
        
        suggested_remote = f"/home/{site_title}/public_html/wp-content"
        self.remote_root_input.setText(suggested_remote)
        
        default_sync_items = self.settings_manager.get('default_sync_items', 'themes\nplugins')
        self.sync_items_input.setPlainText(default_sync_items)
        
        default_debounce = self.settings_manager.get('default_debounce_seconds', 3)
        self.debounce_input.setValue(int(default_debounce))
    
    def browse_local_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Local Root Directory")
        if folder:
            self.local_root_input.setText(folder)
    
    def browse_remote_folders(self):
        ssh_host_list = self.site_data.get('server', [])
        ssh_host = ssh_host_list[0].get('ip', '') if ssh_host_list else ''
        ssh_user = self.ssh_user_input.text().strip()
        ssh_port = self.ssh_port_input.text().strip() or '22'
        remote_root = self.remote_root_input.text().strip()
        
        if not ssh_host:
            QMessageBox.warning(self, "Missing Information",
                "Server IP is not available from API data.")
            return
        
        if not ssh_user:
            QMessageBox.warning(self, "Missing Information",
                "Please enter SSH User before browsing remote folders.")
            self.ssh_user_input.setFocus()
            return
        
        if not remote_root:
            QMessageBox.warning(self, "Missing Information",
                "Please enter Remote Root path before browsing.")
            self.remote_root_input.setFocus()
            return
        
        dialog = RemoteFolderSelectorDialog(
            ssh_host, ssh_port, ssh_user, remote_root,
            self.settings_manager, self
        )
        
        if dialog.exec_() == QDialog.Accepted:
            selected_items = dialog.get_selected_items()
            if selected_items:
                current_text = self.sync_items_input.toPlainText().strip()
                
                existing_items = set()
                if current_text:
                    existing_items = set(line.strip() for line in current_text.split('\n') if line.strip())
                
                for item in selected_items:
                    existing_items.add(item)
                
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
        server_list = self.site_data.get('server', [])
        server_ip = server_list[0].get('ip', '') if server_list else ''
        
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


class NewSiteDialog(QDialog):
    """Dialog for creating a new site configuration"""
    
    site_deleted = pyqtSignal(str)  # site_key
    
    def __init__(self, settings_manager, parent=None, edit_mode=False, site_key=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.edit_mode = edit_mode
        self.site_key_to_delete = site_key
        
        title = "Edit Site Configuration" if edit_mode else "New Site Configuration"
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self.init_ui()
        if not edit_mode:
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
        button_layout = QHBoxLayout()
        
        # Delete site button (only in edit mode)
        if self.edit_mode:
            self.delete_site_btn = QPushButton("🗑 Delete Site Configuration")
            self.delete_site_btn.setStyleSheet("""
                QPushButton {
                    background-color: #dc2626;
                    color: white;
                    padding: 8px 16px;
                    border: none;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #b91c1c;
                }
            """)
            self.delete_site_btn.clicked.connect(self.delete_site)
            button_layout.addWidget(self.delete_site_btn)
            button_layout.addStretch()
        
        # Standard OK/Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        button_layout.addWidget(buttons)
        
        layout.addLayout(button_layout)
    
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
        # Convert multi-line sync items to newline-separated
        sync_items_lines = self.sync_items_input.toPlainText().strip().split('\n')
        sync_items = '\n'.join([line.strip() for line in sync_items_lines if line.strip()])
        
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
    
    def delete_site(self):
        """Delete the site configuration"""
        if not self.site_key_to_delete:
            return
        
        reply = QMessageBox.warning(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the configuration for '{self.site_key_to_delete}'?\n\n"
            f"This will permanently remove the .env file.\n"
            f"Your local and remote files will NOT be affected.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        try:
            # Find and delete the .env file
            sites_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Starter" / "sites"
            site_file = sites_dir / f"{self.site_key_to_delete}.env"
            
            if site_file.exists():
                site_file.unlink()
                QMessageBox.information(
                    self,
                    "Success",
                    f"Site configuration '{self.site_key_to_delete}' has been deleted."
                )
                # Emit signal so parent can close the tab if open
                self.site_deleted.emit(self.site_key_to_delete)
                self.accept()  # Close the dialog
            else:
                QMessageBox.warning(
                    self,
                    "Not Found",
                    f"Configuration file for '{self.site_key_to_delete}' was not found."
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to delete site configuration: {e}"
            )
    
    def load_defaults(self):
        """Load default values from settings"""
        default_port = self.settings_manager.get('ssh_port', 22)
        self.ssh_port_input.setText(str(default_port))
        
        default_sync_items = self.settings_manager.get('default_sync_items', 'themes\nplugins')
        self.sync_items_input.setPlainText(default_sync_items)
        
        default_debounce = self.settings_manager.get('default_debounce_seconds', 3)
        self.debounce_input.setValue(int(default_debounce))


class SiteTab(QWidget):
    """Self-contained widget for managing one site"""
    
    # Signals
    watch_started = pyqtSignal(str)  # site_key
    watch_stopped = pyqtSignal(str)  # site_key
    sync_status_changed = pyqtSignal(str, bool, str)  # site_key, is_syncing, operation
    close_requested = pyqtSignal(object)  # self
    
    def __init__(self, site_key, project_root, sites_dir, settings_manager, parent=None):
        super().__init__(parent)
        self.site_key = site_key
        self.project_root = Path(project_root)
        self.sites_dir = Path(sites_dir)
        self.bin_dir = self.project_root / "bin"
        self.settings_manager = settings_manager
        
        # Thread management
        self.current_thread = None
        self.watch_thread = None
        self._stopping_watch = False
        self.is_syncing = False
        self.sync_start_time = None
        self.sync_timeout_timer = None
        self.permissions_thread = None
        self.force_cleanup_timer = None
        self.wp_config_dialog = None
        self.debug_log_dialog = None
        
        # Load site config
        self.config = self.load_config()
        
        self.init_ui()

    def _set_remote_file_buttons_enabled(self, enabled):
        """Enable or disable remote file action buttons."""
        if hasattr(self, 'edit_wp_config_btn'):
            self.edit_wp_config_btn.setEnabled(enabled)
        if hasattr(self, 'view_debug_log_btn'):
            self.view_debug_log_btn.setEnabled(enabled)
        if hasattr(self, 'clear_debug_log_btn'):
            self.clear_debug_log_btn.setEnabled(enabled)

    def _shell_quote(self, value):
        """Safely quote a shell argument with single quotes."""
        return "'" + str(value).replace("'", "'\"'\"'") + "'"

    def _remote_join(self, base, rel):
        base_clean = (base or "").rstrip('/')
        rel_clean = rel.lstrip('/')
        if base_clean in ("", "/"):
            return f"/{rel_clean}"
        return f"{base_clean}/{rel_clean}"

    def _get_remote_paths(self):
        remote_root = self.config.get('REMOTE_ROOT', '').strip()
        if not remote_root:
            QMessageBox.warning(
                self,
                "Missing Configuration",
                "REMOTE_ROOT is required in the site configuration."
            )
            return None

        remote_root_clean = remote_root.rstrip('/') or '/'
        root_name = Path(remote_root_clean).name

        # Support both styles:
        # 1) REMOTE_ROOT points to WordPress root (contains wp-content)
        # 2) REMOTE_ROOT points directly to wp-content
        if root_name == 'wp-content':
            wp_content_dir = remote_root_clean
            wp_root_dir = str(Path(remote_root_clean).parent).replace('\\', '/') or '/'
        else:
            wp_root_dir = remote_root_clean
            wp_content_dir = self._remote_join(remote_root_clean, 'wp-content')

        wp_config_path = self._remote_join(wp_root_dir, 'wp-config.php')
        debug_log_path = self._remote_join(wp_content_dir, 'debug.log')
        return wp_config_path, debug_log_path

    def _build_ssh_command(self, remote_command):
        ssh_host = self.config.get('SSH_HOST', '').strip()
        ssh_port = self.config.get('SSH_PORT', '22').strip() or '22'
        ssh_user = self.config.get('SSH_USER', '').strip()
        ssh_key = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')

        if not ssh_host or not ssh_user:
            QMessageBox.warning(
                self,
                "Invalid Configuration",
                "SSH host and user are required in the site configuration."
            )
            return None

        ssh_cmd = ['ssh', '-p', ssh_port]
        if ssh_key:
            ssh_cmd.extend(['-i', str(Path(ssh_key).expanduser())])
        ssh_cmd.extend([
            '-o', 'ConnectTimeout=10',
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=accept-new',
            f"{ssh_user}@{ssh_host}",
            remote_command
        ])
        return ssh_cmd

    def _run_ssh(self, remote_command, timeout=45):
        ssh_cmd = self._build_ssh_command(remote_command)
        if not ssh_cmd:
            return None

        try:
            return subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "SSH Timeout", "Remote operation timed out.")
            return None
        except Exception as e:
            QMessageBox.critical(self, "SSH Error", f"Remote operation failed:\n{e}")
            return None

    def _fetch_wp_config_content(self):
        paths = self._get_remote_paths()
        if not paths:
            return False, "", "Missing REMOTE_ROOT configuration."

        wp_config_path, _ = paths
        quoted_path = self._shell_quote(wp_config_path)
        remote_cmd = (
            f"if [ ! -f {quoted_path} ]; then "
            f"echo '__MISSING__'; exit 44; "
            f"fi; cat {quoted_path}"
        )

        result = self._run_ssh(remote_cmd, timeout=45)
        if result is None:
            return False, "", "SSH command failed."

        if result.returncode == 0:
            return True, result.stdout, "Loaded wp-config.php"

        if result.returncode == 44:
            return False, "", f"wp-config.php not found at {wp_config_path}"

        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return False, "", f"Failed to load wp-config.php: {error_msg}"

    def _save_wp_config_content(self, content):
        paths = self._get_remote_paths()
        if not paths:
            return False, "Missing REMOTE_ROOT configuration."

        wp_config_path, _ = paths
        payload = base64.b64encode(content.encode('utf-8')).decode('ascii')
        quoted_path = self._shell_quote(wp_config_path)
        quoted_payload = self._shell_quote(payload)

        remote_cmd = f"""
set -e
wp_config={quoted_path}
payload={quoted_payload}

if [ ! -f "$wp_config" ]; then
  echo "__MISSING__"
  exit 44
fi

backup="$wp_config.bak-$(date +%Y%m%d-%H%M%S)"
orig_mode="$(stat -c '%a' "$wp_config" 2>/dev/null || stat -f '%Lp' "$wp_config" 2>/dev/null || true)"
chmod_changed=0

if [ ! -w "$wp_config" ]; then
  chmod u+w "$wp_config"
  chmod_changed=1
fi

cp "$wp_config" "$backup"
tmp_file="$wp_config.tmp.$$"

if ! (printf '%s' "$payload" | base64 -d > "$tmp_file" 2>/dev/null); then
  printf '%s' "$payload" | base64 -D > "$tmp_file"
fi

mv "$tmp_file" "$wp_config"

if command -v php >/dev/null 2>&1; then
  if ! lint_output="$(php -l "$wp_config" 2>&1)"; then
    cp "$backup" "$wp_config"
    if [ "$chmod_changed" -eq 1 ] && [ -n "$orig_mode" ]; then
      chmod "$orig_mode" "$wp_config"
    fi
    echo "__LINT_FAIL__"
    echo "$lint_output"
    echo "__BACKUP__:$backup"
    exit 42
  fi
  echo "__LINT_OK__"
  echo "$lint_output"
else
  echo "__PHP_MISSING__"
fi

if [ "$chmod_changed" -eq 1 ] && [ -n "$orig_mode" ]; then
  chmod "$orig_mode" "$wp_config"
fi

echo "__BACKUP__:$backup"
"""

        result = self._run_ssh(remote_cmd, timeout=45)
        if result is None:
            return False, "SSH command failed while saving wp-config.php."

        stdout = result.stdout or ""
        stderr = result.stderr.strip()

        if result.returncode == 0:
            if "__PHP_MISSING__" in stdout:
                return True, "Saved wp-config.php. PHP lint skipped (php not found)."
            return True, "Saved wp-config.php and PHP lint passed."

        if result.returncode == 44:
            return False, f"wp-config.php not found at {wp_config_path}"

        if result.returncode == 42 and "__LINT_FAIL__" in stdout:
            lint_lines = []
            capture = False
            backup_path = ""
            for line in stdout.splitlines():
                if line.strip() == "__LINT_FAIL__":
                    capture = True
                    continue
                if line.startswith("__BACKUP__:"):
                    backup_path = line.split(":", 1)[1].strip()
                    capture = False
                    continue
                if capture:
                    lint_lines.append(line)

            lint_msg = "\n".join(lint_lines).strip() or "PHP syntax error"
            if backup_path:
                return False, (
                    "Save failed PHP lint. Rolled back from backup.\n\n"
                    f"Backup: {backup_path}\n"
                    f"Lint output:\n{lint_msg}"
                )
            return False, f"Save failed PHP lint. Rolled back.\n\nLint output:\n{lint_msg}"

        generic_error = stderr or stdout.strip() or "Unknown error"
        return False, f"Failed to save wp-config.php: {generic_error}"

    def _fetch_debug_log_content(self):
        paths = self._get_remote_paths()
        if not paths:
            return "error", "", "Missing REMOTE_ROOT configuration."

        _, debug_log_path = paths
        quoted_path = self._shell_quote(debug_log_path)
        remote_cmd = (
            f"if [ ! -f {quoted_path} ]; then "
            f"echo '__MISSING__'; exit 44; "
            f"fi; tail -n 2000 {quoted_path}"
        )

        result = self._run_ssh(remote_cmd, timeout=45)
        if result is None:
            return "error", "", "SSH command failed."

        if result.returncode == 0:
            return "ok", result.stdout, "Loaded latest 2000 lines from debug.log."

        if result.returncode == 44:
            return "missing", "", f"debug.log not found at {debug_log_path}"

        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return "error", "", f"Failed to read debug.log: {error_msg}"

    def _ensure_debug_log_exists(self):
        paths = self._get_remote_paths()
        if not paths:
            return False, "Missing REMOTE_ROOT configuration."

        _, debug_log_path = paths
        quoted_path = self._shell_quote(debug_log_path)
        quoted_dir = self._shell_quote(str(Path(debug_log_path).parent).replace('\\', '/'))

        remote_cmd = (
            f"mkdir -p {quoted_dir} && "
            f"if [ ! -f {quoted_path} ]; then touch {quoted_path}; fi"
        )

        result = self._run_ssh(remote_cmd, timeout=45)
        if result is None:
            return False, "SSH command failed."

        if result.returncode == 0:
            return True, "debug.log created."

        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return False, f"Failed to create debug.log: {error_msg}"

    def _clear_debug_log_content(self):
        paths = self._get_remote_paths()
        if not paths:
            return False, "Missing REMOTE_ROOT configuration."

        _, debug_log_path = paths
        quoted_path = self._shell_quote(debug_log_path)
        quoted_dir = self._shell_quote(str(Path(debug_log_path).parent).replace('\\', '/'))

        remote_cmd = f"""
set -e
log_file={quoted_path}
log_dir={quoted_dir}

mkdir -p "$log_dir"

if [ ! -f "$log_file" ]; then
  touch "$log_file"
fi

orig_mode="$(stat -c '%a' "$log_file" 2>/dev/null || stat -f '%Lp' "$log_file" 2>/dev/null || true)"
chmod_changed=0
if [ ! -w "$log_file" ]; then
  chmod u+w "$log_file"
  chmod_changed=1
fi

: > "$log_file"

if [ "$chmod_changed" -eq 1 ] && [ -n "$orig_mode" ]; then
  chmod "$orig_mode" "$log_file"
fi
"""

        result = self._run_ssh(remote_cmd, timeout=45)
        if result is None:
            return False, "SSH command failed."

        if result.returncode == 0:
            return True, "debug.log cleared."

        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return False, f"Failed to clear debug.log: {error_msg}"

    def open_wp_config_editor(self):
        """Open wp-config editor dialog."""
        if self.is_watching() or (self.current_thread and self.current_thread.isRunning()) or (
            self.permissions_thread and self.permissions_thread.isRunning()
        ):
            QMessageBox.warning(self, "Busy", "Another operation is currently running.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit wp-config.php - {self.site_key}")
        dialog.setMinimumSize(900, 700)

        layout = QVBoxLayout(dialog)
        info_label = QLabel("Remote file editor for wp-config.php")
        layout.addWidget(info_label)

        editor = QPlainTextEdit()
        editor.setFont(QFont("Monaco", 10))
        layout.addWidget(editor)

        status_label = QLabel("")
        layout.addWidget(status_label)

        button_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        save_btn = QPushButton("Save")
        close_btn = QPushButton("Close")
        save_btn.setEnabled(False)

        button_row.addWidget(refresh_btn)
        button_row.addStretch()
        button_row.addWidget(save_btn)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        state = {'original': "", 'loading': False}

        def set_status(text, ok=None):
            if ok is True:
                status_label.setStyleSheet("color: #16a34a;")
            elif ok is False:
                status_label.setStyleSheet("color: #dc2626;")
            else:
                status_label.setStyleSheet("color: #374151;")
            status_label.setText(text)

        def refresh_content():
            state['loading'] = True
            save_btn.setEnabled(False)
            ok, content, message = self._fetch_wp_config_content()
            if ok:
                editor.setPlainText(content)
                state['original'] = content
                set_status(message, True)
            else:
                set_status(message, False)
            state['loading'] = False

        def on_text_changed():
            if state['loading']:
                return
            changed = editor.toPlainText() != state['original']
            save_btn.setEnabled(changed)

        def save_content():
            if editor.toPlainText() == state['original']:
                save_btn.setEnabled(False)
                return
            set_status("Saving...", None)
            success, message = self._save_wp_config_content(editor.toPlainText())
            if success:
                state['original'] = editor.toPlainText()
                save_btn.setEnabled(False)
                set_status(message, True)
                self.log_output(f"✓ wp-config.php saved for {self.site_key}\n", "success")
            else:
                set_status(message, False)
                self.log_output(f"✗ wp-config.php save failed for {self.site_key}\n", "error")

        refresh_btn.clicked.connect(refresh_content)
        save_btn.clicked.connect(save_content)
        close_btn.clicked.connect(dialog.reject)
        editor.textChanged.connect(on_text_changed)

        refresh_content()
        dialog.exec_()

    def view_debug_log(self):
        """Open debug.log viewer dialog with one-shot refresh."""
        if self.is_watching() or (self.current_thread and self.current_thread.isRunning()) or (
            self.permissions_thread and self.permissions_thread.isRunning()
        ):
            QMessageBox.warning(self, "Busy", "Another operation is currently running.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"debug.log - {self.site_key}")
        dialog.setMinimumSize(900, 650)

        layout = QVBoxLayout(dialog)
        info_label = QLabel("Showing latest 2000 lines (one-shot refresh).")
        layout.addWidget(info_label)

        log_text = QPlainTextEdit()
        log_text.setReadOnly(True)
        log_text.setFont(QFont("Monaco", 10))
        layout.addWidget(log_text)

        status_label = QLabel("")
        layout.addWidget(status_label)

        button_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        create_btn = QPushButton("Create debug.log")
        create_btn.setVisible(False)
        close_btn = QPushButton("Close")

        button_row.addWidget(refresh_btn)
        button_row.addWidget(create_btn)
        button_row.addStretch()
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        def set_status(text, ok=None):
            if ok is True:
                status_label.setStyleSheet("color: #16a34a;")
            elif ok is False:
                status_label.setStyleSheet("color: #dc2626;")
            else:
                status_label.setStyleSheet("color: #374151;")
            status_label.setText(text)

        def refresh_log():
            status, content, message = self._fetch_debug_log_content()
            if status == "ok":
                log_text.setPlainText(content)
                create_btn.setVisible(False)
                set_status(message, True)
                self.log_output(f"✓ Loaded debug.log for {self.site_key}\n", "info")
            elif status == "missing":
                log_text.setPlainText(message)
                create_btn.setVisible(True)
                set_status("debug.log not found.", False)
            else:
                log_text.setPlainText(message)
                create_btn.setVisible(False)
                set_status(message, False)

        def create_log():
            ok, message = self._ensure_debug_log_exists()
            if ok:
                set_status(message, True)
                refresh_log()
            else:
                set_status(message, False)

        refresh_btn.clicked.connect(refresh_log)
        create_btn.clicked.connect(create_log)
        close_btn.clicked.connect(dialog.reject)

        refresh_log()
        dialog.exec_()

    def clear_debug_log(self):
        """Clear debug.log contents on remote server."""
        if self.is_watching() or (self.current_thread and self.current_thread.isRunning()) or (
            self.permissions_thread and self.permissions_thread.isRunning()
        ):
            QMessageBox.warning(self, "Busy", "Another operation is currently running.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Clear debug.log",
            f"Erase debug.log content for {self.site_key}?\n\n"
            "This keeps the file and only clears its content.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        ok, message = self._clear_debug_log_content()
        if ok:
            self.log_output(f"✓ {message}\n", "success")
            QMessageBox.information(self, "Success", message)
        else:
            self.log_output(f"✗ {message}\n", "error")
            QMessageBox.warning(self, "Failed", message)
    
    def load_config(self):
        """Load site configuration from .env file"""
        site_file = self.sites_dir / f"{self.site_key}.env"
        config = {}
        
        if site_file.exists():
            with open(site_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        if value.startswith("$'") and value.endswith("'"):
                            value = value[2:-1].replace('\\n', '\n')
                        else:
                            value = value.strip('"')
                        
                        config[key] = value
        
        return config
    
    def init_ui(self):
        """Initialize the tab UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Action buttons
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(8)
        
        # Primary sync row
        sync_row = QHBoxLayout()
        sync_row.setSpacing(8)
        self.pull_btn = QPushButton("⬇ Pull")
        self.pull_btn.setObjectName("pullBtn")
        self.pull_btn.setMinimumHeight(38)
        self.pull_btn.clicked.connect(self.run_pull)
        sync_row.addWidget(self.pull_btn)
        
        self.push_btn = QPushButton("⬆ Push")
        self.push_btn.setObjectName("pushBtn")
        self.push_btn.setMinimumHeight(38)
        self.push_btn.clicked.connect(self.run_push)
        sync_row.addWidget(self.push_btn)
        
        self.watch_btn = QPushButton("👁 Watch")
        self.watch_btn.setObjectName("watchBtn")
        self.watch_btn.setMinimumHeight(38)
        self.watch_btn.clicked.connect(self.toggle_watch)
        sync_row.addWidget(self.watch_btn)
        
        actions_layout.addLayout(sync_row)
        
        # Secondary actions row
        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(8)
        
        self.test_connection_btn = QPushButton("🔌 Test Connection")
        self.test_connection_btn.setMinimumHeight(32)
        self.test_connection_btn.clicked.connect(self.test_connection)
        secondary_row.addWidget(self.test_connection_btn)
        
        self.ssh_btn = QPushButton("🖥 SSH")
        self.ssh_btn.setMinimumHeight(32)
        self.ssh_btn.clicked.connect(self.open_ssh_terminal)
        secondary_row.addWidget(self.ssh_btn)
        
        self.open_in_editor_btn = QPushButton("📝 Open in Editor")
        self.open_in_editor_btn.setMinimumHeight(32)
        self.open_in_editor_btn.clicked.connect(self.open_in_editor)
        secondary_row.addWidget(self.open_in_editor_btn)
        
        self.edit_btn = QPushButton("⚙️ Edit Site")
        self.edit_btn.setMinimumHeight(32)
        self.edit_btn.clicked.connect(self.edit_site)
        secondary_row.addWidget(self.edit_btn)
        
        actions_layout.addLayout(secondary_row)
        
        # Permissions row
        permissions_row = QHBoxLayout()
        permissions_row.setSpacing(8)
        
        self.open_rights_btn = QPushButton("🔓 Open Rights")
        self.open_rights_btn.setMinimumHeight(32)
        self.open_rights_btn.setToolTip("Open file permissions on server (chmod 755/644)")
        self.open_rights_btn.clicked.connect(self.open_rights)
        permissions_row.addWidget(self.open_rights_btn)
        
        self.close_rights_btn = QPushButton("🔒 Close Rights")
        self.close_rights_btn.setMinimumHeight(32)
        self.close_rights_btn.setToolTip("Restrict file permissions on server (secure WordPress)")
        self.close_rights_btn.clicked.connect(self.close_rights)
        permissions_row.addWidget(self.close_rights_btn)
        
        self.clean_local_btn = QPushButton("🗑 Clean Local Files")
        self.clean_local_btn.setMinimumHeight(32)
        self.clean_local_btn.setToolTip("Delete local files (keeps server files safe)")
        self.clean_local_btn.clicked.connect(self.clean_local_files)
        permissions_row.addWidget(self.clean_local_btn)
        
        actions_layout.addLayout(permissions_row)

        # Remote file tools row
        remote_files_row = QHBoxLayout()
        remote_files_row.setSpacing(8)

        self.edit_wp_config_btn = QPushButton("Edit wp-config")
        self.edit_wp_config_btn.setMinimumHeight(32)
        self.edit_wp_config_btn.setToolTip("Edit remote wp-config.php (auto-detected from REMOTE_ROOT)")
        self.edit_wp_config_btn.clicked.connect(self.open_wp_config_editor)
        remote_files_row.addWidget(self.edit_wp_config_btn)

        self.view_debug_log_btn = QPushButton("View debug.log")
        self.view_debug_log_btn.setMinimumHeight(32)
        self.view_debug_log_btn.setToolTip("View latest 2000 lines of remote debug.log (auto-detected from REMOTE_ROOT)")
        self.view_debug_log_btn.clicked.connect(self.view_debug_log)
        remote_files_row.addWidget(self.view_debug_log_btn)

        self.clear_debug_log_btn = QPushButton("Clear debug.log")
        self.clear_debug_log_btn.setMinimumHeight(32)
        self.clear_debug_log_btn.setToolTip("Erase content of remote debug.log (auto-detected from REMOTE_ROOT)")
        self.clear_debug_log_btn.clicked.connect(self.clear_debug_log)
        remote_files_row.addWidget(self.clear_debug_log_btn)

        actions_layout.addLayout(remote_files_row)
        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)
        
        # Console output
        output_group = QGroupBox("Console")
        output_layout = QVBoxLayout()
        output_layout.setSpacing(8)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFont(QFont("Monaco", 10))
        self.output_text.setMinimumHeight(240)
        self.output_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid rgba(0, 0, 0, 0.06);
                border-radius: 6px;
                background-color: #fafbfc;
                color: #1f2937;
                font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
                font-size: 11px;
                padding: 12px;
                line-height: 1.5;
            }
        """)
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
        layout.addWidget(output_group)
        
        # Initial log message
        self.log_output(f"⚡ Ready: {self.site_key}\n", "info")
    
    def is_watching(self):
        """Check if watch mode is active"""
        return self.watch_thread and self.watch_thread.isRunning()
    
    def get_status_icon(self):
        """Get status icon for tab"""
        if self.is_watching():
            return "🟢"
        return "⚪"
    
    def get_status_color(self):
        """Get background color for tab based on status"""
        if self.is_watching():
            return "#d1fae5"  # Light green
        return None  # Default
    
    def run_pull(self):
        """Execute pull command"""
        script_path = self.bin_dir / "pull"
        args = [str(script_path), self.site_key]
        self.execute_command(args, "Pull")
    
    def run_push(self):
        """Execute push command"""
        # Safety check
        local_root = self.config.get('LOCAL_ROOT', '')
        if local_root:
            local_path = Path(local_root).expanduser()
            
            if not local_path.exists():
                QMessageBox.critical(
                    self, "⚠️ Cannot Push - No Local Files",
                    f"<b>Push aborted for safety!</b>\n\n"
                    f"Local directory does not exist:\n{local_path}\n\n"
                    f"<span style='color: #dc2626;'>⚠️ Pushing would delete all files on the server!</span>"
                )
                self.log_output("❌ Push aborted: Local directory does not exist\n", "error")
                return
            
            has_files = False
            try:
                for root, dirs, files in os.walk(local_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
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
                    f"<span style='color: #dc2626;'>⚠️ Pushing would delete all files on the server!</span>"
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
        args = [str(script_path), self.site_key]
        self.execute_command(args, "Push")
    
    def toggle_watch(self):
        """Start or stop watch mode"""
        if self.is_watching():
            self.stop_watch()
        else:
            self.start_watch()
    
    def start_watch(self):
        """Start watch mode"""
        script_path = self.bin_dir / "watch"
        args = [str(script_path), self.site_key]
        
        self.log_output(f"\n{'='*60}\n")
        self.log_output(f"Starting watch mode for site: {self.site_key}\n", "info")
        self.log_output(f"{'='*60}\n\n")
        
        self.watch_thread = CommandThread(args, self.project_root)
        self.watch_thread.output_signal.connect(self.append_output)
        self.watch_thread.finished_signal.connect(self.on_watch_finished)
        self.watch_thread.start()
        
        self.watch_btn.setText("⏹ Stop")
        self.watch_btn.setObjectName("watchBtnActive")
        self.watch_btn.setStyleSheet("")
        self.watch_btn.style().unpolish(self.watch_btn)
        self.watch_btn.style().polish(self.watch_btn)
        
        self.pull_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        self.test_connection_btn.setEnabled(False)
        self._set_remote_file_buttons_enabled(False)
        
        self.watch_started.emit(self.site_key)
    
    def stop_watch(self):
        """Stop watch mode"""
        if self.watch_thread and self.watch_thread.isRunning():
            self.log_output("\nStopping watch mode...\n", "warning")
            self._stopping_watch = True
            
            self.watch_btn.setText("⏸ Stopping...")
            self.watch_btn.setEnabled(False)
            
            self.watch_thread.stop()
            
            # Set timer for force cleanup if graceful stop fails
            if self.force_cleanup_timer:
                self.force_cleanup_timer.stop()
            self.force_cleanup_timer = QTimer()
            self.force_cleanup_timer.setSingleShot(True)
            self.force_cleanup_timer.timeout.connect(self._force_watch_cleanup)
            self.force_cleanup_timer.start(5000)
    
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
            
            # Clean up timer
            if self.force_cleanup_timer:
                self.force_cleanup_timer.stop()
                self.force_cleanup_timer = None
            
            self._reset_watch_ui()
            
            # Emit signal to update UI since on_watch_finished won't be called
            self.watch_stopped.emit(self.site_key)
    
    def on_watch_finished(self, return_code):
        """Handle watch mode completion"""
        if not self.watch_thread:
            return
        
        # Cancel force cleanup timer since we stopped gracefully
        if self.force_cleanup_timer:
            self.force_cleanup_timer.stop()
            self.force_cleanup_timer = None
        
        if self._stopping_watch:
            self.log_output("\nWatch mode stopped\n", "info")
        elif return_code != 0:
            self.log_output(f"\nWatch mode exited with code {return_code}\n", "error")
        else:
            self.log_output("\nWatch mode stopped unexpectedly\n", "warning")
        
        self._stopping_watch = False
        self._reset_watch_ui()
        
        try:
            self.watch_thread.blockSignals(True)
            self.watch_thread.deleteLater()
        except:
            pass
        self.watch_thread = None
        
        self.watch_stopped.emit(self.site_key)
    
    def _reset_watch_ui(self):
        """Reset watch button and UI"""
        self.watch_btn.setText("👁 Watch")
        self.watch_btn.setObjectName("watchBtn")
        self.watch_btn.setStyleSheet("")
        self.watch_btn.style().unpolish(self.watch_btn)
        self.watch_btn.style().polish(self.watch_btn)
        self.watch_btn.setEnabled(True)
        
        self.pull_btn.setEnabled(True)
        self.push_btn.setEnabled(True)
        self.test_connection_btn.setEnabled(True)
        self._set_remote_file_buttons_enabled(True)
        
        self.sync_status_changed.emit(self.site_key, False, "")
    
    def execute_command(self, args, action_name):
        """Execute a command in a thread"""
        self.log_output(f"\n{'='*60}\n")
        self.log_output(f"Running {action_name} for site: {self.site_key}\n", "info")
        self.log_output(f"{'='*60}\n\n")
        
        self.pull_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        self._set_remote_file_buttons_enabled(False)
        
        self.current_thread = CommandThread(args, self.project_root)
        self.current_thread.output_signal.connect(self.append_output)
        self.current_thread.finished_signal.connect(
            lambda code: self.on_command_finished(code, action_name)
        )
        self.current_thread.start()
    
    def on_command_finished(self, return_code, action_name):
        """Handle command completion"""
        self.sync_status_changed.emit(self.site_key, False, "")
        
        if return_code == 0:
            self.log_output(f"\n✓ {action_name} completed successfully\n", "success")
        else:
            self.log_output(f"\n✗ {action_name} failed with exit code {return_code}\n", "error")
        
        self.pull_btn.setEnabled(True)
        self.push_btn.setEnabled(True)
        self._set_remote_file_buttons_enabled(True)
        
        if self.current_thread:
            self.current_thread.blockSignals(True)
            self.current_thread.deleteLater()
            self.current_thread = None
    
    def test_connection(self):
        """Test SSH connection"""
        ssh_host = self.config.get('SSH_HOST', '')
        ssh_port = self.config.get('SSH_PORT', '22')
        ssh_user = self.config.get('SSH_USER', '')
        ssh_key = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        
        if not ssh_host or not ssh_user:
            QMessageBox.warning(
                self, "Invalid Configuration",
                "SSH host and user are required in the site configuration."
            )
            return
        
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
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            
            self.test_connection_btn.setEnabled(True)
            self.test_connection_btn.setText("🔌 Test Connection")
            
            if result.returncode == 0:
                self.log_output(f"✓ Connection successful!\n", "success")
                QMessageBox.information(
                    self, "Connection Successful",
                    f"Successfully connected to {ssh_user}@{ssh_host}:{ssh_port}"
                )
            else:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                self.log_output(f"✗ Connection failed: {error_msg}\n", "error")
                QMessageBox.warning(
                    self, "Connection Failed",
                    f"Failed to connect to {ssh_user}@{ssh_host}:{ssh_port}\n\nError: {error_msg}"
                )
        except subprocess.TimeoutExpired:
            self.test_connection_btn.setEnabled(True)
            self.test_connection_btn.setText("🔌 Test Connection")
            self.log_output(f"✗ Connection timeout\n", "error")
            QMessageBox.warning(self, "Connection Timeout", "Connection timed out.")
        except Exception as e:
            self.test_connection_btn.setEnabled(True)
            self.test_connection_btn.setText("🔌 Test Connection")
            self.log_output(f"✗ Error: {e}\n", "error")
    
    def open_ssh_terminal(self):
        """Open SSH terminal (placeholder)"""
        QMessageBox.information(
            self, "SSH Terminal",
            "SSH terminal integration coming soon.\n\n"
            f"For now, open terminal and run:\nssh {self.config.get('SSH_USER')}@{self.config.get('SSH_HOST')}"
        )
    
    def open_in_editor(self):
        """Open local folder in editor"""
        local_root = self.config.get('LOCAL_ROOT', '')
        if not local_root:
            QMessageBox.warning(
                self, "No Local Path",
                "LOCAL_ROOT is not configured for this site."
            )
            return
        
        local_path = Path(local_root).expanduser()
        
        if not local_path.exists():
            reply = QMessageBox.question(
                self, "Path Not Found",
                f"Local path does not exist:\n{local_path}\n\nDo you want to create it?",
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
        
        self.log_output(f"→ Opening {local_path}...\n", "info")
        
        # Get editor preference
        editor_path = self.settings_manager.get('preferred_editor_path', 'auto')
        
        try:
            if editor_path == 'auto':
                # Try to detect VS Code
                try:
                    result = subprocess.run(['which', 'code'], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0 and result.stdout.strip():
                        subprocess.Popen(['code', str(local_path)])
                        self.log_output(f"✓ Opened in VS Code\n", "success")
                        return
                except:
                    pass
                
                # Fall back to Finder
                subprocess.Popen(['open', str(local_path)])
                self.log_output(f"✓ Opened in Finder\n", "success")
            else:
                # Use custom editor path
                subprocess.Popen([editor_path, str(local_path)])
                self.log_output(f"✓ Opened in {Path(editor_path).name}\n", "success")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open folder:\n{e}")
            self.log_output(f"✗ Failed to open folder: {e}\n", "error")
    
    def edit_site(self):
        """Edit site configuration"""
        dialog = NewSiteDialog(self.settings_manager, self.parent(), edit_mode=True, site_key=self.site_key)
        
        # Connect delete signal
        dialog.site_deleted.connect(self.on_site_deleted)
        
        # Pre-fill with current config
        dialog.site_key_input.setText(self.site_key)
        dialog.site_key_input.setReadOnly(True)  # Can't change site key
        dialog.ssh_host_input.setText(self.config.get('SSH_HOST', ''))
        dialog.ssh_port_input.setText(self.config.get('SSH_PORT', '22'))
        dialog.ssh_user_input.setText(self.config.get('SSH_USER', ''))
        dialog.local_root_input.setText(self.config.get('LOCAL_ROOT', ''))
        dialog.remote_root_input.setText(self.config.get('REMOTE_ROOT', ''))
        
        sync_items = self.config.get('SYNC_ITEMS', '')
        dialog.sync_items_input.setPlainText(sync_items)
        
        dialog.delete_check.setChecked(self.config.get('RSYNC_DELETE', '0') == '1')
        
        debounce = self.config.get('DEBOUNCE_SECONDS', '3')
        try:
            dialog.debounce_input.setValue(int(debounce))
        except:
            dialog.debounce_input.setValue(3)
        
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self._save_site_config(config)
            
            # Reload config
            self.config = self.load_config()
            self.log_output(f"\n✓ Site configuration updated: {self.site_key}\n", "success")
            QMessageBox.information(self, "Success", f"Site '{self.site_key}' updated successfully!")
    
    def on_site_deleted(self, site_key):
        """Handle site deletion - request tab closure"""
        self.log_output(f"\n🗑 Site configuration deleted: {site_key}\n", "warning")
        # Request the tab to be closed
        self.close_requested.emit(self)
    
    def _save_site_config(self, config):
        """Save site configuration to .env file"""
        site_file = self.sites_dir / f"{config['site_key']}.env"
        
        try:
            self.sites_dir.mkdir(parents=True, exist_ok=True)
            
            with open(site_file, 'w') as f:
                f.write(f'SITE_KEY="{config["site_key"]}"\n')
                f.write(f'SSH_HOST="{config["ssh_host"]}"\n')
                f.write(f'SSH_PORT="{config["ssh_port"]}"\n')
                f.write(f'SSH_USER="{config["ssh_user"]}"\n')
                f.write(f'LOCAL_ROOT="{config["local_root"]}"\n')
                f.write(f'REMOTE_ROOT="{config["remote_root"]}"\n')
                sync_items_escaped = config["sync_items"].replace('\n', '\\n')
                f.write(f"SYNC_ITEMS=$'{sync_items_escaped}'\n")
                f.write(f'RSYNC_DELETE="{config["rsync_delete"]}"\n')
                debounce = config.get('debounce_seconds', '3')
                f.write(f'DEBOUNCE_SECONDS="{debounce}"\n')
        except Exception as e:
            self.log_output(f"\n✗ Error saving site config: {e}\n", "error")
            raise
    
    def open_rights(self):
        """Open file permissions on the remote server"""
        if self.permissions_thread and self.permissions_thread.isRunning():
            QMessageBox.warning(self, "Busy", "A permissions operation is already in progress.")
            return
        
        ssh_host = self.config.get('SSH_HOST')
        ssh_port = self.config.get('SSH_PORT', '22')
        ssh_user = self.config.get('SSH_USER')
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        if not all([ssh_host, ssh_user]):
            QMessageBox.critical(self, "Error", "SSH configuration incomplete")
            return
        
        commands = [
            'find . -name public_html -type d -exec chmod 775 {} \\;',
            'find ./public_html/ -type f -exec chmod 644 {} \\;',
            'find ./public_html/ -type d -exec chmod 755 {} \\;'
        ]
        
        remote_command = ' && '.join(commands)
        
        self.log_output(f"\n=== Opening Rights on {self.site_key} ===\n", 'info')
        self.log_output(f"Connecting to {ssh_user}@{ssh_host}...\n")
        
        ssh_command = [
            'ssh', '-i', str(ssh_key_expanded), '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10',
            f'{ssh_user}@{ssh_host}', remote_command
        ]
        
        self.open_rights_btn.setEnabled(False)
        self.open_rights_btn.setText("Opening...")
        self._set_remote_file_buttons_enabled(False)
        
        self.permissions_thread = PermissionsThread(ssh_command, "open")
        self.permissions_thread.output_signal.connect(self.log_output)
        self.permissions_thread.finished_signal.connect(self.on_open_rights_finished)
        self.permissions_thread.start()
    
    def on_open_rights_finished(self, success, message):
        """Handle completion of open rights operation"""
        self.open_rights_btn.setEnabled(True)
        self.open_rights_btn.setText("🔓 Open Rights")
        self._set_remote_file_buttons_enabled(True)
        self.permissions_thread = None
    
    def close_rights(self):
        """Close/restrict file permissions on the remote server"""
        if self.permissions_thread and self.permissions_thread.isRunning():
            QMessageBox.warning(self, "Busy", "A permissions operation is already in progress.")
            return
        
        reply = QMessageBox.question(
            self, "Confirm Close Rights",
            f"Are you sure you want to restrict file permissions on {self.site_key}?\n\n"
            "This will set restrictive permissions for security.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        ssh_host = self.config.get('SSH_HOST')
        ssh_port = self.config.get('SSH_PORT', '22')
        ssh_user = self.config.get('SSH_USER')
        ssh_key_path = self.settings_manager.get('ssh_key_path', '~/.ssh/id_rsa')
        ssh_key_expanded = Path(ssh_key_path).expanduser()
        
        if not all([ssh_host, ssh_user]):
            QMessageBox.critical(self, "Error", "SSH configuration incomplete")
            return
        
        commands = [
            'find ./public_html/ -type f -exec chmod 444 {} \\;',
            'find ./public_html/ -type d -exec chmod 555 {} \\;',
            'find ./public_html/.htaccess -type f -exec chmod 444 {} \\;',
            'find ./public_html/wp-config.php -type f -exec chmod 400 {} \\;',
            'find ./public_html/wp-content/uploads/ -type d -exec chmod 755 {} \\;',
            'find . -name public_html -type d -exec chmod 755 {} \\;',
            'find ./public_html/ -name "wp-content" -type d -exec chmod 755 {} \\;'
        ]
        
        remote_command = ' && '.join([f"{cmd} 2>/dev/null || true" for cmd in commands])
        
        self.log_output(f"\n=== Closing Rights on {self.site_key} ===\n", 'info')
        self.log_output(f"Connecting to {ssh_user}@{ssh_host}...\n")
        self.log_output("This may take a few minutes...\n")
        
        ssh_command = [
            'ssh', '-i', str(ssh_key_expanded), '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10',
            f'{ssh_user}@{ssh_host}', remote_command
        ]
        
        self.close_rights_btn.setEnabled(False)
        self.close_rights_btn.setText("Closing...")
        self._set_remote_file_buttons_enabled(False)
        
        self.permissions_thread = PermissionsThread(ssh_command, "close")
        self.permissions_thread.output_signal.connect(self.log_output)
        self.permissions_thread.finished_signal.connect(self.on_close_rights_finished)
        self.permissions_thread.start()
    
    def on_close_rights_finished(self, success, message):
        """Handle completion of close rights operation"""
        self.close_rights_btn.setEnabled(True)
        self.close_rights_btn.setText("🔒 Close Rights")
        self._set_remote_file_buttons_enabled(True)
        self.permissions_thread = None
    
    def clean_local_files(self):
        """Delete local files for the selected site (DOES NOT affect server)"""
        local_root = self.config.get('LOCAL_ROOT', '')
        if not local_root:
            QMessageBox.critical(self, "Error", "Local root path not configured for this site.")
            return
        
        local_path = Path(local_root).expanduser()
        
        if not local_path.exists():
            QMessageBox.information(
                self, "Already Clean",
                f"Local directory does not exist:\n{local_path}\n\nNothing to clean."
            )
            return
        
        # Get file age info
        age_days = self.get_local_files_age(local_path)
        age_info = f"\n\nLast modified: {age_days} days ago" if age_days is not None else ""
        
        # Confirmation dialog
        reply = QMessageBox.question(
            self, "⚠️ Confirm Local File Deletion",
            f"<b>This will DELETE all local files for {self.site_key}</b>\n\n"
            f"Local path: {local_path}{age_info}\n\n"
            f"<span style='color: #dc2626;'>⚠️ This action CANNOT be undone!</span>\n\n"
            f"<span style='color: #059669;'>✓ Server files will NOT be affected</span>\n\n"
            f"You can always pull fresh files from the server after deletion.\n\n"
            f"Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            self.log_output("Local file cleanup cancelled.\n", "info")
            return
        
        # Perform deletion
        try:
            self.log_output(f"🗑️ Cleaning local files for {self.site_key}...\n", "info")
            self.log_output(f"Deleting: {local_path}\n")
            
            shutil.rmtree(local_path)
            
            self.log_output("✅ Local files deleted successfully!\n", "success")
            self.log_output("💡 Tip: Use 'Pull' to download fresh files from server\n", "info")
            
            QMessageBox.information(
                self, "Success",
                f"Local files deleted successfully!\n\n"
                f"Use 'Pull' to download fresh files from the server."
            )
        except Exception as e:
            self.log_output(f"✗ Error deleting files: {e}\n", "error")
            QMessageBox.critical(self, "Error", f"Failed to delete local files:\n{e}")
    
    def get_local_files_age(self, local_path):
        """Get the age in days of the most recently modified file"""
        try:
            if not local_path.exists():
                return None
            
            latest_mtime = 0
            
            for root, dirs, files in os.walk(local_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
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
            
            now = time.time()
            age_seconds = now - latest_mtime
            age_days = int(age_seconds / 86400)
            
            return age_days
        except Exception:
            return None
    
    def log_output(self, text, level=None):
        """Add text to output with optional styling"""
        if level == "error":
            self.output_text.setTextColor(QColor("#dc2626"))
        elif level == "success":
            self.output_text.setTextColor(QColor("#16a34a"))
        elif level == "warning":
            self.output_text.setTextColor(QColor("#d97706"))
        elif level == "info":
            self.output_text.setTextColor(QColor("#2563eb"))
        else:
            self.output_text.setTextColor(QColor("#374151"))
        
        self.output_text.append(text.rstrip())
        self.output_text.moveCursor(QTextCursor.End)
        self.output_text.setTextColor(QColor("#374151"))
    
    def append_output(self, text):
        """Append output from thread"""
        sync_started = False
        if "Pushing:" in text or "pushing" in text.lower():
            self.sync_status_changed.emit(self.site_key, True, "Push")
            sync_started = True
        elif "Pulling:" in text or "pulling" in text.lower():
            self.sync_status_changed.emit(self.site_key, True, "Pull")
            sync_started = True
        elif "rsync" in text.lower() and "building file list" in text.lower():
            self.sync_status_changed.emit(self.site_key, True, "")
            sync_started = True
        
        if sync_started:
            self.sync_start_time = time.time()
            if self.sync_timeout_timer:
                self.sync_timeout_timer.stop()
            self.sync_timeout_timer = QTimer()
            self.sync_timeout_timer.setSingleShot(True)
            self.sync_timeout_timer.timeout.connect(
                lambda: self.sync_status_changed.emit(self.site_key, False, "")
            )
            self.sync_timeout_timer.start(15000)
        
        if self.is_syncing:
            if "✓ Push complete" in text or "✓ Pull complete" in text:
                if self.sync_timeout_timer:
                    self.sync_timeout_timer.stop()
                    self.sync_timeout_timer = None
                QTimer.singleShot(300, lambda: self.sync_status_changed.emit(self.site_key, False, ""))
        
        self.output_text.setTextColor(QColor("#374151"))
        self.output_text.insertPlainText(text)
        self.output_text.moveCursor(QTextCursor.End)
    
    def clear_output(self):
        """Clear the output area"""
        self.output_text.clear()
        self.log_output(f"⚡ Ready: {self.site_key}\n", "info")
    
    def cleanup(self):
        """Cleanup threads before closing"""
        # Stop timers
        if self.force_cleanup_timer:
            self.force_cleanup_timer.stop()
            self.force_cleanup_timer = None
        
        if self.watch_thread and self.watch_thread.isRunning():
            self.watch_thread.stop()
            self.watch_thread.wait(2000)
        
        if self.current_thread and self.current_thread.isRunning():
            self.current_thread.stop()
            self.current_thread.wait(2000)
        
        if self.permissions_thread and self.permissions_thread.isRunning():
            self.permissions_thread.wait(2000)


class TabManager:
    """Manages all site tabs"""
    
    def __init__(self, tab_widget, settings_manager):
        self.tab_widget = tab_widget
        self.settings_manager = settings_manager
        self.site_tabs = {}  # site_key -> SiteTab
    
    def add_tab(self, site_tab):
        """Add a new site tab"""
        self.site_tabs[site_tab.site_key] = site_tab
        icon = site_tab.get_status_icon()
        index = self.tab_widget.addTab(site_tab, f"{icon} {site_tab.site_key}")
        
        # Set tab background color
        color = site_tab.get_status_color()
        if color:
            self.tab_widget.tabBar().setTabTextColor(index, QColor("#065f46"))
        
        return index
    
    def remove_tab(self, site_key):
        """Remove a tab"""
        if site_key in self.site_tabs:
            site_tab = self.site_tabs[site_key]
            index = self.tab_widget.indexOf(site_tab)
            if index >= 0:
                self.tab_widget.removeTab(index)
            site_tab.cleanup()
            del self.site_tabs[site_key]
    
    def get_tab(self, site_key):
        """Get a site tab by key"""
        return self.site_tabs.get(site_key)
    
    def is_site_open(self, site_key):
        """Check if a site is already open"""
        return site_key in self.site_tabs
    
    def get_watching_count(self):
        """Get number of sites currently watching"""
        count = 0
        for tab in self.site_tabs.values():
            if tab.is_watching():
                count += 1
        return count
    
    def get_watching_sites(self):
        """Get list of site keys currently watching"""
        watching = []
        for site_key, tab in self.site_tabs.items():
            if tab.is_watching():
                watching.append(site_key)
        return watching
    
    def update_tab_visual(self, site_key):
        """Update tab visual indicators"""
        if site_key in self.site_tabs:
            site_tab = self.site_tabs[site_key]
            index = self.tab_widget.indexOf(site_tab)
            if index >= 0:
                icon = site_tab.get_status_icon()
                self.tab_widget.setTabText(index, f"{icon} {site_key}")
                
                # Update tab color
                color = site_tab.get_status_color()
                if color:
                    self.tab_widget.tabBar().setTabTextColor(index, QColor("#065f46"))
                else:
                    self.tab_widget.tabBar().setTabTextColor(index, QColor("#1f2937"))
    
    def save_session(self):
        """Save current tab session"""
        open_tabs = list(self.site_tabs.keys())
        self.settings_manager.set('open_tabs', open_tabs)
        self.settings_manager.save_settings()
    
    def get_open_site_keys(self):
        """Get list of currently open site keys"""
        return list(self.site_tabs.keys())


class OpenSiteDialog(QDialog):
    """Searchable dialog for opening sites"""
    
    def __init__(self, available_sites, open_sites, parent=None):
        super().__init__(parent)
        self.available_sites = available_sites
        self.open_sites = set(open_sites)
        self.selected_site = None
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle("Open Site")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        layout = QVBoxLayout(self)
        
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
        
        self.populate_sites()
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def populate_sites(self):
        """Populate the sites list"""
        self.sites_list.clear()
        
        for site_key in sorted(self.available_sites):
            if site_key in self.open_sites:
                display_text = f"{site_key} (already open)"
                item = QListWidgetItem(display_text)
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            else:
                item = QListWidgetItem(site_key)
            
            item.setData(Qt.UserRole, site_key)
            self.sites_list.addItem(item)
    
    def filter_sites(self, text):
        """Filter sites based on search text"""
        for i in range(self.sites_list.count()):
            item = self.sites_list.item(i)
            site_key = item.data(Qt.UserRole)
            visible = text.lower() in site_key.lower()
            item.setHidden(not visible)
    
    def on_site_double_click(self, item):
        """Handle double-click on site"""
        if item.flags() & Qt.ItemIsEnabled:
            self.selected_site = item.data(Qt.UserRole)
            self.accept()
    
    def on_accept(self):
        """Handle OK button"""
        current_item = self.sites_list.currentItem()
        if current_item and (current_item.flags() & Qt.ItemIsEnabled):
            self.selected_site = current_item.data(Qt.UserRole)
            self.accept()
        else:
            QMessageBox.warning(self, "No Selection", "Please select an available site.")
    
    def get_selected_site(self):
        """Get the selected site key"""
        return self.selected_site


# Import remaining dialogs from original (SettingsDialog, ConfigureSiteDialog, etc.)
# For brevity, using placeholder - would need to copy from original file

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
        
        ssh_key_layout = QHBoxLayout()
        self.ssh_key_input = QLineEdit()
        self.ssh_key_input.setPlaceholderText("~/.ssh/id_rsa")
        ssh_key_layout.addWidget(self.ssh_key_input)
        browse_key_btn = QPushButton("Browse...")
        browse_key_btn.clicked.connect(self.browse_ssh_key)
        ssh_key_layout.addWidget(browse_key_btn)
        ssh_form.addRow("SSH Key Path:", ssh_key_layout)
        
        self.ssh_port_input = QSpinBox()
        self.ssh_port_input.setRange(1, 65535)
        self.ssh_port_input.setValue(22)
        ssh_form.addRow("Default SSH Port:", self.ssh_port_input)
        
        local_root_layout = QHBoxLayout()
        self.local_root_input = QLineEdit()
        self.local_root_input.setPlaceholderText("~/Sites")
        local_root_layout.addWidget(self.local_root_input)
        browse_root_btn = QPushButton("Browse...")
        browse_root_btn.clicked.connect(self.browse_local_root)
        local_root_layout.addWidget(browse_root_btn)
        ssh_form.addRow("Default Local Root:", local_root_layout)
        
        ssh_layout.addLayout(ssh_form)
        
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
        
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
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
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Code Editor", "/usr/local/bin", "All Files (*)"
        )
        if file_path:
            self.editor_path_input.setText(file_path)
    
    def browse_ssh_key(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home() / ".ssh"), "All Files (*)"
        )
        if file_path:
            self.ssh_key_input.setText(file_path)
    
    def browse_local_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Default Local Root Directory")
        if folder:
            self.local_root_input.setText(folder)
    
    def test_authentication(self):
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
        self.test_auth_btn.setEnabled(True)
        
        if success:
            self.auth_status_label.setText(f"✓ {message}")
            self.auth_status_label.setStyleSheet("color: green;")
        else:
            self.auth_status_label.setText(f"✗ {message}")
            self.auth_status_label.setStyleSheet("color: red;")
    
    def closeEvent(self, event):
        if self.auth_thread and self.auth_thread.isRunning():
            self.auth_thread.blockSignals(True)
            if not self.auth_thread.wait(2000):
                self.auth_thread.terminate()
                self.auth_thread.wait(1000)
            self.auth_thread.deleteLater()
            self.auth_thread = None
        super().closeEvent(event)
    
    def save_and_close(self):
        if self.auth_thread and self.auth_thread.isRunning():
            self.auth_thread.blockSignals(True)
            if not self.auth_thread.wait(2000):
                self.auth_thread.terminate()
                self.auth_thread.wait(1000)
            self.auth_thread.deleteLater()
            self.auth_thread = None
        
        self.settings_manager.set('wp_url', self.wp_url_input.text().strip())
        self.settings_manager.set('wp_username', self.wp_username_input.text().strip())
        self.settings_manager.set('wp_app_password', self.wp_password_input.text().strip())
        self.settings_manager.set('ssh_key_path', self.ssh_key_input.text().strip())
        self.settings_manager.set('ssh_port', self.ssh_port_input.value())
        self.settings_manager.set('default_local_root', self.local_root_input.text().strip())
        self.settings_manager.set('default_sync_items', self.sync_items_input.toPlainText().strip())
        self.settings_manager.set('preferred_editor_path', self.editor_path_input.text().strip() or 'auto')
        self.settings_manager.set('default_debounce_seconds', self.debounce_seconds_input.value())
        
        if (self.settings_manager.get('wp_url') and 
            self.settings_manager.get('wp_username') and 
            self.settings_manager.get('wp_app_password')):
            pass
        else:
            self.settings_manager.set('authenticated', False)
        
        if self.settings_manager.save_settings():
            self.accept()
        else:
            QMessageBox.critical(self, "Error", "Failed to save settings")


class SelectSiteDialog(QDialog):
    """Dialog for selecting a site from available API sites"""
    
    def __init__(self, api_sites_data, configured_sites, parent=None):
        super().__init__(parent)
        self.api_sites_data = api_sites_data
        self.configured_sites = configured_sites
        self.selected_site = None
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle("Select Site to Configure")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        layout.addWidget(QLabel("Select a site from the API to configure, or use Manual Entry for custom sites (e.g., dev environments):"))
        
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type to filter sites...")
        self.search_input.textChanged.connect(self.filter_sites)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)
        
        self.sites_list = QListWidget()
        self.sites_list.itemDoubleClicked.connect(self.on_site_double_click)
        layout.addWidget(self.sites_list)
        
        self.populate_sites()
        
        button_layout = QHBoxLayout()
        
        self.manual_btn = QPushButton("Manual Entry...")
        self.manual_btn.clicked.connect(self.on_manual_entry)
        button_layout.addWidget(self.manual_btn)
        
        button_layout.addStretch()
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.on_accept)
        buttons.rejected.connect(self.reject)
        button_layout.addWidget(buttons)
        
        layout.addLayout(button_layout)
    
    def populate_sites(self):
        self.sites_list.clear()
        
        sorted_sites = sorted(self.api_sites_data, key=lambda x: x.get('title', ''))
        
        for site_data in sorted_sites:
            site_key = site_data.get('title', '')
            if not site_key:
                continue
            
            if site_key in self.configured_sites:
                display_text = f"{site_key} (already configured)"
            else:
                display_text = site_key
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, site_data)
            self.sites_list.addItem(item)
    
    def filter_sites(self, text):
        for i in range(self.sites_list.count()):
            item = self.sites_list.item(i)
            site_data = item.data(Qt.UserRole)
            site_key = site_data.get('title', '').lower()
            item.setHidden(text.lower() not in site_key)
    
    def on_site_double_click(self, item):
        self.selected_site = item.data(Qt.UserRole)
        self.accept()
    
    def on_manual_entry(self):
        self.selected_site = {'manual_entry': True}
        self.accept()
    
    def on_accept(self):
        current_item = self.sites_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "No Selection", "Please select a site to configure.")
            return
        
        self.selected_site = current_item.data(Qt.UserRole)
        self.accept()
    
    def get_selected_site(self):
        return self.selected_site


class WPSyncGUI(QMainWindow):
    """Main application window with multi-tab support"""
    
    def __init__(self):
        super().__init__()
        
        # Get project paths
        if getattr(sys, 'frozen', False):
            self.project_root = Path(__file__).parent.resolve()
        else:
            self.project_root = Path(__file__).parent.parent.resolve()
        
        self.user_data_dir = Path.home() / "Library" / "Application Support" / "Webmix Sync Starter"
        self.sites_dir = self.user_data_dir / "sites"
        self.bin_dir = self.project_root / "bin"
        
        # Initialize settings
        self.settings_manager = SettingsManager(self.project_root)
        
        # API sites data and threads
        self.api_sites_data = []
        self.fetch_sites_thread = None
        
        # Initialize UI
        self.init_ui()
        self.init_system_tray()
        
        # Restore session
        self.restore_session()
        
        # Check for updates automatically on startup (delayed to not interfere with UI)
        QTimer.singleShot(3000, self.auto_check_for_updates)
    
    def auto_check_for_updates(self):
        """Automatically check for updates on startup (silent if no update available)"""
        if UPDATE_CHECKER_AVAILABLE:
            self.check_for_updates(silent=True)
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Webmix Sync Starter")
        self.setGeometry(100, 100, 1000, 700)
        
        # Apply styles
        self.apply_styles()
        
        # Menu bar
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        new_site_action = QAction("New Site...", self)
        new_site_action.triggered.connect(self.create_new_site)
        file_menu.addAction(new_site_action)
        
        # Settings menu
        settings_menu = menubar.addMenu("Settings")
        prefs_action = QAction("Preferences...", self)
        prefs_action.triggered.connect(self.open_settings)
        settings_menu.addAction(prefs_action)
        
        # Help menu
        help_menu = menubar.addMenu("Help")
        if UPDATE_CHECKER_AVAILABLE:
            update_action = QAction("Check for Updates...", self)
            update_action.triggered.connect(self.check_for_updates)
            help_menu.addAction(update_action)
        
        about_action = QAction(f"About v{APP_VERSION}", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
        # Central widget with absolute positioning for + button
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        
        main_layout.addWidget(self.tab_widget)
        
        # Add + button with absolute positioning
        self.add_tab_button = QPushButton("+", central_widget)
        self.add_tab_button.setObjectName("addTabBtn")
        self.add_tab_button.setFixedSize(32, 32)
        self.add_tab_button.setToolTip("Open new site tab")
        self.add_tab_button.clicked.connect(self.open_site_picker)
        self.add_tab_button.raise_()
        
        # Position button in top-right
        self.add_tab_button.move(central_widget.width() - 44, 6)
        
        # Initialize tab manager
        self.tab_manager = TabManager(self.tab_widget, self.settings_manager)
        
        # Status bar
        self.statusBar().showMessage("Ready")
    
    def apply_styles(self):
        """Apply application styles"""
        self.setStyleSheet("""
            /* Main window */
            QMainWindow {
                background-color: #f8f9fa;
            }
            
            /* Group boxes - cards with shadow */
            QGroupBox {
                font-weight: 600;
                font-size: 11px;
                border: none;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                background-color: #ffffff;
                /* Layered shadows for depth */
                border: 1px solid rgba(0, 0, 0, 0.06);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 8px;
                color: #1f2937;
                font-weight: 600;
                letter-spacing: 0.3px;
            }
            
            /* Base button style */
            QPushButton {
                background-color: #ffffff;
                color: #374151;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 500;
                font-size: 11px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: rgba(0, 0, 0, 0.12);
            }
            QPushButton:pressed {
                background-color: #f3f4f6;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
                border-color: rgba(0, 0, 0, 0.04);
            }
            
            /* Primary action buttons */
            QPushButton#pullBtn {
                background-color: #eff6ff;
                color: #1e40af;
                border: 1px solid #bfdbfe;
            }
            QPushButton#pullBtn:hover {
                background-color: #dbeafe;
                border-color: #93c5fd;
            }
            QPushButton#pullBtn:pressed {
                background-color: #bfdbfe;
            }
            
            QPushButton#pushBtn {
                background-color: #fef3c7;
                color: #92400e;
                border: 1px solid #fde68a;
            }
            QPushButton#pushBtn:hover {
                background-color: #fde68a;
                border-color: #fcd34d;
            }
            QPushButton#pushBtn:pressed {
                background-color: #fcd34d;
            }
            
            /* Watch button states */
            QPushButton#watchBtn {
                background-color: #fee2e2;
                color: #991b1b;
                border: 1px solid #fca5a5;
                font-weight: 600;
            }
            QPushButton#watchBtn:hover {
                background-color: #fecaca;
                border-color: #f87171;
            }
            QPushButton#watchBtn:pressed {
                background-color: #fca5a5;
            }
            
            QPushButton#watchBtnActive {
                background-color: #d1fae5;
                color: #065f46;
                border: 1px solid #6ee7b7;
                font-weight: 600;
            }
            QPushButton#watchBtnActive:hover {
                background-color: #a7f3d0;
                border-color: #34d399;
            }
            QPushButton#watchBtnActive:pressed {
                background-color: #6ee7b7;
            }
            
            /* Secondary buttons */
            QPushButton#secondaryBtn {
                background-color: #ffffff;
                color: #6b7280;
                border: 1px solid rgba(0, 0, 0, 0.08);
            }
            QPushButton#secondaryBtn:hover {
                background-color: #f9fafb;
                color: #374151;
                border-color: rgba(0, 0, 0, 0.12);
            }
            
            /* Tab widget - sleeker design */
            QTabWidget::pane {
                border: none;
                background-color: #ffffff;
                border-radius: 8px;
                border: 1px solid rgba(0, 0, 0, 0.06);
                margin-top: -1px;
            }
            
            QTabWidget::tab-bar {
                alignment: left;
            }
            
            QTabBar {
                background-color: transparent;
            }
            
            QTabBar::tab {
                background-color: transparent;
                color: #6b7280;
                border: none;
                border-bottom: 2px solid transparent;
                padding: 10px 10px 10px 10px;
                margin-right: 4px;
                min-height: 24px;
                font-weight: 500;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background-color: transparent;
                color: #1f2937;
                border-bottom: 2px solid #2563eb;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: rgba(0, 0, 0, 0.02);
                color: #374151;
                border-bottom: 2px solid #e5e7eb;
            }
            
            /* Tab close button styling */
            QTabBar QToolButton {
                background: transparent;
                border: none;
                padding: 4px;
                margin: 0px 8px 0px 0px;
                width: 16px;
                height: 16px;
            }
            QTabBar QToolButton:hover {
                background-color: rgba(239, 68, 68, 0.15);
                border-radius: 3px;
            }
            
            /* Add tab button */
            QPushButton#addTabBtn {
                background-color: #ffffff;
                color: #2563eb;
                border: 1px solid rgba(37, 99, 235, 0.2);
                border-radius: 6px;
                font-size: 18px;
                font-weight: 600;
                padding: 0px;
            }
            QPushButton#addTabBtn:hover {
                background-color: #eff6ff;
                border-color: #2563eb;
            }
            QPushButton#addTabBtn:pressed {
                background-color: #dbeafe;
            }
        """)
    
    def init_system_tray(self):
        """Initialize system tray"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        
        # macOS native status bar with menu
        if MACOS_STATUSBAR_AVAILABLE:
            self.status_bar = NSStatusBar.systemStatusBar()
            self.status_item = self.status_bar.statusItemWithLength_(NSVariableStatusItemLength)
            self.status_item.setTitle_("⚪")
            
            # Create native macOS menu (simple, just shows status)
            self.native_menu = NSMenu.alloc().init()
            
            # Watch status item
            self.native_watch_status = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "⚪ No sites watching", None, ""
            )
            self.native_watch_status.setEnabled_(False)
            self.native_menu.addItem_(self.native_watch_status)
            
            # Attach menu to status item
            self.status_item.setMenu_(self.native_menu)
            
            # Don't create Qt tray on macOS - we only use native status bar
            return
        
        # Qt system tray (for non-macOS systems)
        self.tray_icon = QSystemTrayIcon(self)
        tray_menu = QMenu()
        
        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)
        
        tray_menu.addSeparator()
        
        self.watch_status_action = QAction("⚪ No sites watching", self)
        self.watch_status_action.setEnabled(False)
        tray_menu.addAction(self.watch_status_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("Webmix Sync Starter\n⚪ No sites watching")
        self.tray_icon.activated.connect(self.tray_icon_activated)
        self.tray_icon.show()
    
    def update_tray_status(self):
        """Update system tray status"""
        watching_count = self.tab_manager.get_watching_count()
        watching_sites = self.tab_manager.get_watching_sites()
        
        if watching_count > 0:
            status_text = f"🟢 {watching_count} watching"
            icon = f"🟢 {watching_count}"
            tooltip = f"Webmix Sync Starter\n🟢 {watching_count} site(s) watching"
            
            # Update native macOS menu item
            if MACOS_STATUSBAR_AVAILABLE and hasattr(self, 'native_watch_status'):
                if watching_count == 1:
                    self.native_watch_status.setTitle_(f"🟢 {watching_sites[0]} watching")
                else:
                    sites_list = ", ".join(watching_sites[:3])
                    if watching_count > 3:
                        sites_list += f" (+{watching_count-3} more)"
                    self.native_watch_status.setTitle_(f"🟢 {sites_list}")
            
            # Update Qt tray action (non-macOS)
            if hasattr(self, 'watch_status_action'):
                if watching_count == 1:
                    self.watch_status_action.setText(f"🟢 {watching_sites[0]} watching")
                else:
                    sites_list = ", ".join(watching_sites[:3])
                    if watching_count > 3:
                        sites_list += f" (+{watching_count-3} more)"
                    self.watch_status_action.setText(f"🟢 {sites_list}")
        else:
            status_text = "⚪ No sites watching"
            icon = "⚪"
            tooltip = "Webmix Sync Starter\n⚪ No sites watching"
            
            # Update native macOS menu item
            if MACOS_STATUSBAR_AVAILABLE and hasattr(self, 'native_watch_status'):
                self.native_watch_status.setTitle_("⚪ No sites watching")
            
            # Update Qt tray action (non-macOS)
            if hasattr(self, 'watch_status_action'):
                self.watch_status_action.setText("⚪ No sites watching")
        
        # Update macOS status bar icon
        if MACOS_STATUSBAR_AVAILABLE and hasattr(self, 'status_item'):
            self.status_item.setTitle_(icon)
        
        # Update Qt tray icon (non-macOS)
        if hasattr(self, 'tray_icon'):
            self.tray_icon.setToolTip(tooltip)
    
    def tray_icon_activated(self, reason):
        """Handle tray icon clicks"""
        if reason == QSystemTrayIcon.DoubleClick or reason == QSystemTrayIcon.Trigger:
            self.show_window()
    
    def show_window(self):
        """Show and raise the main window"""
        self.show()
        self.raise_()
        self.activateWindow()
    
    def open_site_picker(self):
        """Show dialog to open a site"""
        # Get configured sites
        configured_sites = []
        if self.sites_dir.exists():
            site_files = list(self.sites_dir.glob("*.env"))
            configured_sites = [f.stem for f in site_files if f.stem != "example-site"]
        
        if not configured_sites:
            QMessageBox.information(
                self, "No Sites",
                "No sites configured yet.\n\nUse File > New Site to create a site configuration."
            )
            return
        
        # Get currently open sites
        open_sites = self.tab_manager.get_open_site_keys()
        
        # Show picker dialog
        dialog = OpenSiteDialog(configured_sites, open_sites, self)
        if dialog.exec_() == QDialog.Accepted:
            site_key = dialog.get_selected_site()
            if site_key:
                self.open_site_tab(site_key)
    
    def open_site_tab(self, site_key):
        """Open a site in a new tab"""
        # Check if already open
        if self.tab_manager.is_site_open(site_key):
            # Switch to existing tab
            site_tab = self.tab_manager.get_tab(site_key)
            index = self.tab_widget.indexOf(site_tab)
            self.tab_widget.setCurrentIndex(index)
            return
        
        # Check watch limit
        watching_count = self.tab_manager.get_watching_count()
        
        # Create new tab
        site_tab = SiteTab(site_key, self.project_root, self.sites_dir, self.settings_manager, self)
        site_tab.watch_started.connect(self.on_watch_started)
        site_tab.watch_stopped.connect(self.on_watch_stopped)
        site_tab.sync_status_changed.connect(self.on_sync_status_changed)
        site_tab.close_requested.connect(self.on_tab_close_requested)
        
        index = self.tab_manager.add_tab(site_tab)
        self.tab_widget.setCurrentIndex(index)
        
        # Save session
        self.tab_manager.save_session()
    
    def close_tab(self, index):
        """Close a tab at the given index"""
        site_tab = self.tab_widget.widget(index)
        if not site_tab:
            return
        
        # Check if watching
        if site_tab.is_watching():
            reply = QMessageBox.question(
                self, "Watch Mode Active",
                f"Watch mode is active on '{site_tab.site_key}'.\n\n"
                "Stop watching and close tab?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            
            # Stop watch
            site_tab.stop_watch()
        
        # Remove tab
        self.tab_manager.remove_tab(site_tab.site_key)
        
        # Save session
        self.tab_manager.save_session()
    
    def on_watch_started(self, site_key):
        """Handle watch started on a tab"""
        # Check limit
        watching_count = self.tab_manager.get_watching_count()
        if watching_count > MAX_CONCURRENT_WATCHES:
            site_tab = self.tab_manager.get_tab(site_key)
            if site_tab:
                site_tab.stop_watch()
                QMessageBox.warning(
                    self, "Watch Limit Reached",
                    f"Maximum {MAX_CONCURRENT_WATCHES} sites can watch simultaneously.\n\n"
                    "Stop another watch first."
                )
                return
        
        self.tab_manager.update_tab_visual(site_key)
        self.update_tray_status()
    
    def on_watch_stopped(self, site_key):
        """Handle watch stopped on a tab"""
        # Force update with slight delay to ensure state is fully updated
        QTimer.singleShot(100, lambda: self.tab_manager.update_tab_visual(site_key))
        QTimer.singleShot(100, self.update_tray_status)
    
    def on_sync_status_changed(self, site_key, is_syncing, operation):
        """Handle sync status change"""
        # Could show in status bar or tray
        pass
    
    def on_tab_close_requested(self, site_tab):
        """Handle tab close request (e.g., from site deletion)"""
        # Remove the tab
        self.tab_manager.remove_tab(site_tab.site_key)
        
        # Update tray status
        self.update_tray_status()
        
        # Save session
        self.tab_manager.save_session()
    
    def restore_session(self):
        """Restore previously open tabs"""
        open_tabs = self.settings_manager.get('open_tabs', [])
        
        if not open_tabs:
            # First launch - show empty state
            return
        
        for site_key in open_tabs:
            site_file = self.sites_dir / f"{site_key}.env"
            if site_file.exists():
                self.open_site_tab(site_key)
    
    def create_new_site(self):
        """Create a new site - fetch from API or manual entry"""
        # Check authentication
        if not self.settings_manager.is_authenticated():
            reply = QMessageBox.question(
                self, "Authentication Required",
                "WordPress authentication is required to fetch sites from API.\n\n"
                "Do you want to configure authentication now?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.open_settings()
                return
            else:
                # Allow manual entry without auth
                dialog = NewSiteDialog(self.settings_manager, self)
                if dialog.exec_() == QDialog.Accepted:
                    self._save_and_open_new_site(dialog.get_config())
                return
        
        # Fetch sites from API
        wp_url = self.settings_manager.get('wp_url')
        username = self.settings_manager.get('wp_username')
        password = self.settings_manager.get('wp_app_password')
        
        progress = QProgressDialog("Fetching sites from API...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        self.fetch_sites_thread = FetchSitesThread(wp_url, username, password)
        self.fetch_sites_thread.sites_result.connect(lambda success, data, msg: self._on_sites_fetched(success, data, msg, progress))
        self.fetch_sites_thread.start()
    
    def _on_sites_fetched(self, success, data, message, progress):
        """Handle sites fetched from API"""
        progress.close()
        
        if not success:
            reply = QMessageBox.warning(
                self, "API Fetch Failed",
                f"Failed to fetch sites from API:\n{message}\n\n"
                "Would you like to enter site details manually instead?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                dialog = NewSiteDialog(self.settings_manager, self)
                if dialog.exec_() == QDialog.Accepted:
                    self._save_and_open_new_site(dialog.get_config())
            return
        
        self.api_sites_data = data
        
        # Get list of configured sites
        configured_sites = []
        if self.sites_dir.exists():
            for site_file in self.sites_dir.glob("*.env"):
                configured_sites.append(site_file.stem)
        
        # Show site selector
        dialog = SelectSiteDialog(self.api_sites_data, configured_sites, self)
        if dialog.exec_() == QDialog.Accepted:
            selected = dialog.get_selected_site()
            if not selected:
                return
            
            if selected.get('manual_entry'):
                # User chose manual entry
                manual_dialog = NewSiteDialog(self.settings_manager, self)
                if manual_dialog.exec_() == QDialog.Accepted:
                    self._save_and_open_new_site(manual_dialog.get_config())
            else:
                # User selected from API
                self._configure_api_site(selected)
    
    def _configure_api_site(self, site_data):
        """Configure a site from API data"""
        site_key = site_data.get('title', '')
        if not site_key:
            QMessageBox.critical(self, "Error", "Invalid site data")
            return
        
        # Show configuration dialog
        dialog = ConfigureSiteDialog(site_data, self.settings_manager, self)
        
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            
            # Check if already exists
            site_file = self.sites_dir / f"{site_key}.env"
            if site_file.exists():
                reply = QMessageBox.question(
                    self, "Site Exists",
                    f"Site '{site_key}' already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            
            self._save_and_open_new_site(config)
    
    def _save_and_open_new_site(self, config):
        """Save site config and open in new tab"""
        site_key = config['site_key']
        site_file = self.sites_dir / f"{site_key}.env"
        
        try:
            self.sites_dir.mkdir(parents=True, exist_ok=True)
            
            with open(site_file, 'w') as f:
                f.write(f'SITE_KEY="{config["site_key"]}"\n')
                f.write(f'SSH_HOST="{config["ssh_host"]}"\n')
                f.write(f'SSH_PORT="{config["ssh_port"]}"\n')
                f.write(f'SSH_USER="{config["ssh_user"]}"\n')
                f.write(f'LOCAL_ROOT="{config["local_root"]}"\n')
                f.write(f'REMOTE_ROOT="{config["remote_root"]}"\n')
                sync_items_escaped = config["sync_items"].replace('\n', '\\n')
                f.write(f"SYNC_ITEMS=$'{sync_items_escaped}'\n")
                f.write(f'RSYNC_DELETE="{config["rsync_delete"]}"\n')
                debounce = config.get('debounce_seconds', '3')
                f.write(f'DEBOUNCE_SECONDS="{debounce}"\n')
            
            QMessageBox.information(
                self, "Success",
                f"Site '{site_key}' created successfully!\n\nOpening in new tab..."
            )
            
            # Open the new site in a tab
            self.open_site_tab(site_key)
            
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to create site:\n{e}"
            )
    
    def open_settings(self):
        """Open settings dialog"""
        dialog = SettingsDialog(self.settings_manager, self)
        dialog.exec_()
    
    def check_for_updates(self, silent=False):
        """Check for updates from GitHub"""
        if not UPDATE_CHECKER_AVAILABLE:
            if not silent:
                QMessageBox.warning(self, "Updates", "Update checker module not available.")
            return
        
        # Show progress dialog only if not silent
        progress = None
        if not silent:
            progress = QProgressDialog("Checking for updates...", None, 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setCancelButton(None)
            progress.show()
            QApplication.processEvents()
        
        try:
            checker = UpdateChecker(APP_VERSION)
            has_update, latest_version, download_url, message = checker.check_for_updates()
            
            if progress:
                progress.close()
            
            if has_update:
                # Show update available dialog
                reply = QMessageBox.question(
                    self, "Update Available",
                    f"<b>New version available: {latest_version}</b><br>"
                    f"Current version: {APP_VERSION}<br><br>"
                    f"<b>Release Notes:</b><br>"
                    f"{message[:500]}{'...' if len(message) > 500 else ''}<br><br>"
                    f"Would you like to download and install the update?",
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    self.download_and_install_update(download_url, latest_version)
            else:
                # No update or error
                if not silent:
                    if latest_version:
                        QMessageBox.information(
                            self, "No Updates",
                            f"You are running the latest version ({APP_VERSION})."
                        )
                    else:
                        QMessageBox.warning(
                            self, "Update Check Failed",
                            f"Could not check for updates:\n{message}"
                        )
                    
        except Exception as e:
            if progress:
                progress.close()
            if not silent:
                QMessageBox.critical(self, "Error", f"Update check failed:\n{e}")
    
    def download_and_install_update(self, download_url, version):
        """Download and install update"""
        # Create progress dialog
        progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        def update_progress(downloaded, total):
            if total > 0:
                percent = int((downloaded / total) * 100)
                progress.setValue(percent)
                progress.setLabelText(f"Downloading update... {downloaded // 1024} KB / {total // 1024} KB")
            QApplication.processEvents()
            
            if progress.wasCanceled():
                raise Exception("Download cancelled by user")
        
        try:
            checker = UpdateChecker(APP_VERSION)
            success, dmg_path, error = checker.download_update(download_url, update_progress)
            
            progress.close()
            
            if success:
                # Ask to install
                reply = QMessageBox.question(
                    self, "Download Complete",
                    f"Update {version} has been downloaded.\n\n"
                    "Open the installer now?\n\n"
                    "The app will quit to allow installation.",
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    install_success, install_msg = checker.install_update(dmg_path)
                    
                    if install_success:
                        QMessageBox.information(self, "Update Ready", install_msg)
                        # Quit the app
                        QApplication.quit()
                    else:
                        QMessageBox.critical(self, "Installation Failed", install_msg)
            else:
                QMessageBox.critical(self, "Download Failed", error)
                
        except Exception as e:
            progress.close()
            if "cancelled" not in str(e).lower():
                QMessageBox.critical(self, "Error", f"Download failed:\n{e}")
    
    def show_about(self):
        """Show about dialog"""
        QMessageBox.about(
            self, "About",
            f"<b>Webmix Sync Starter</b><br>"
            f"Version {APP_VERSION}<br><br>"
            f"Multi-site WordPress sync tool"
        )
    
    def quit_application(self):
        """Quit the application"""
        # Check for active watches
        watching_sites = self.tab_manager.get_watching_sites()
        
        if watching_sites:
            sites_list = ", ".join(watching_sites[:5])
            if len(watching_sites) > 5:
                sites_list += f" (+{len(watching_sites)-5} more)"
            
            reply = QMessageBox.question(
                self, "Active Watches",
                f"{len(watching_sites)} site(s) are watching:\n{sites_list}\n\n"
                "Stop all and quit?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        
        # Save session
        self.tab_manager.save_session()
        
        # Cleanup all tabs
        for site_key in list(self.tab_manager.site_tabs.keys()):
            self.tab_manager.remove_tab(site_key)
        
        # Hide tray
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        
        QApplication.quit()
    
    def closeEvent(self, event):
        """Handle window close"""
        # Minimize to tray if tray is available
        if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            return
        
        # Otherwise, quit
        self.quit_application()
    
    def resizeEvent(self, event):
        """Handle window resize - keep + button positioned"""
        super().resizeEvent(event)
        if hasattr(self, 'add_tab_button'):
            # Position button in top-right corner
            self.add_tab_button.move(self.width() - 44, 6)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Webmix Sync Starter")
    
    window = WPSyncGUI()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
