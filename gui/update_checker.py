"""
Auto-update checker for Webmix Sync Starter
Checks GitHub Releases for new versions and handles updates
"""

import requests
import subprocess
import shutil
from pathlib import Path
from packaging import version
import tempfile
import os


class UpdateChecker:
    """Check for and install app updates from GitHub Releases"""
    
    def __init__(self, current_version, repo_owner="extreemsnel", repo_name="webmix-sync-starter"):
        """
        Initialize the update checker
        
        Args:
            current_version: Current app version (e.g., "1.0.10")
            repo_owner: GitHub username/organization
            repo_name: GitHub repository name
        """
        self.current_version = current_version
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/latest"
    
    def check_for_updates(self, timeout=10):
        """
        Check if a new version is available
        
        Returns:
            tuple: (has_update: bool, latest_version: str, download_url: str, release_notes: str)
        """
        try:
            response = requests.get(self.github_api_url, timeout=timeout)
            
            if response.status_code == 404:
                # No releases found
                return False, None, None, "No releases found"
            
            if response.status_code != 200:
                return False, None, None, f"GitHub API error: {response.status_code}"
            
            release_data = response.json()
            
            # Get latest version from tag (e.g., "v1.0.11" or "1.0.11")
            latest_version_tag = release_data.get("tag_name", "").lstrip("v")
            release_notes = release_data.get("body", "No release notes available")
            
            # Find the DMG asset
            download_url = None
            for asset in release_data.get("assets", []):
                if asset["name"].endswith(".dmg"):
                    download_url = asset["browser_download_url"]
                    break
            
            if not download_url:
                return False, latest_version_tag, None, "No DMG file found in release"
            
            # Compare versions
            try:
                current = version.parse(self.current_version)
                latest = version.parse(latest_version_tag)
                
                if latest > current:
                    return True, latest_version_tag, download_url, release_notes
                else:
                    return False, latest_version_tag, download_url, "You have the latest version"
                    
            except version.InvalidVersion as e:
                return False, latest_version_tag, download_url, f"Version parse error: {e}"
                
        except requests.exceptions.Timeout:
            return False, None, None, "Connection timeout"
        except requests.exceptions.ConnectionError:
            return False, None, None, "No internet connection"
        except Exception as e:
            return False, None, None, f"Error checking for updates: {str(e)}"
    
    def download_update(self, download_url, progress_callback=None):
        """
        Download the update DMG
        
        Args:
            download_url: URL to download the DMG
            progress_callback: Optional callback function(bytes_downloaded, total_bytes)
        
        Returns:
            tuple: (success: bool, dmg_path: Path or None, error_message: str or None)
        """
        try:
            # Create temp directory for download
            temp_dir = Path(tempfile.gettempdir()) / "webmix-sync-updates"
            temp_dir.mkdir(exist_ok=True)
            
            dmg_filename = download_url.split("/")[-1]
            dmg_path = temp_dir / dmg_filename
            
            # Download with progress
            response = requests.get(download_url, stream=True, timeout=60)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(dmg_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
            
            return True, dmg_path, None
            
        except Exception as e:
            return False, None, f"Download failed: {str(e)}"
    
    def install_update(self, dmg_path):
        """
        Install the update by opening the DMG
        
        Args:
            dmg_path: Path to the downloaded DMG file
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # Open the DMG file - macOS will mount it
            subprocess.run(['open', str(dmg_path)], check=True)
            
            return True, (
                "Update DMG opened!\n\n"
                "To install:\n"
                "1. Drag the app to your Applications folder\n"
                "2. Replace the existing app when prompted\n"
                "3. Relaunch the app\n\n"
                "The app will quit now to allow installation."
            )
            
        except Exception as e:
            return False, f"Failed to open DMG: {str(e)}"
    
    @staticmethod
    def get_app_version_from_bundle(app_path="/Applications/Webmix Sync Starter.app"):
        """
        Get the version from an installed app bundle
        
        Returns:
            str or None: Version string or None if not found
        """
        try:
            info_plist_path = Path(app_path) / "Contents" / "Info.plist"
            if not info_plist_path.exists():
                return None
            
            # Use plutil to read the plist
            result = subprocess.run(
                ['plutil', '-extract', 'CFBundleShortVersionString', 'raw', str(info_plist_path)],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
            
        except Exception:
            return None
