"""
py2app setup script for Webmix Sync Starter GUI
"""
from setuptools import setup

APP = ['gui/wp-sync-native-tabbed.py']
DATA_FILES = [
    ('config', ['config/excludes.txt']),
    ('bin', ['bin/pull', 'bin/push', 'bin/watch', 'bin/setup-site']),
    ('lib', ['lib/common.sh']),
    ('.github', ['.github/copilot-instructions.md']),
]

OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'gui/app-icon.icns',  # We'll create this
    'plist': {
        'CFBundleName': 'Webmix Sync Starter',
        'CFBundleDisplayName': 'Webmix Sync Starter',
        'CFBundleIdentifier': 'com.webmix.syncstarter',
        'CFBundleVersion': '1.2.4',
        'CFBundleShortVersionString': '1.2.4',
        'NSHumanReadableCopyright': 'Copyright © 2026 Webmix',
        'LSMinimumSystemVersion': '10.13',
        'NSHighResolutionCapable': True,
    },
    'packages': ['PyQt5', 'requests', 'packaging'],
    'includes': ['subprocess', 'pathlib', 'json', 'base64'],
}

setup(
    name='Webmix Sync Starter',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
