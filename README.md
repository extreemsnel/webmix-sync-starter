# wp-sync-starter

Minimal local-first WordPress sync workflow for custom theme/plugin development over SSH using `rsync` and `fswatch`.

## What this is

This starter gives you:

- per-site config files
- explicit `pull` from server to local
- explicit `push` from local to server
- `watch` mode using `fswatch` + debounced `rsync`
- strict path allowlisting for theme/plugin folders only
- excludes for common junk/build folders
- dry-run support

Default behavior is intentionally **one-way during development**:

- `pull`: remote -> local
- `push`: local -> remote
- `watch`: local -> remote

That avoids accidental two-way sync drift.

## Requirements

Install on macOS:

```bash
brew install fswatch rsync
```

Notes:

- macOS ships an older rsync. That usually still works fine for this setup.
- SSH key auth is assumed.
- Password auth is not supported by these scripts.

## Folder structure

```text
wp-sync-starter/
├── bin/
│   ├── pull
│   ├── push
│   ├── setup-site
│   └── watch
├── config/
│   ├── excludes.txt
│   └── sites/
│       └── example-site.env
├── docs/
│   └── GUI-GUIDE.md
├── gui/
│   └── wp-sync-gui.py
├── lib/
│   └── common.sh
├── logs/
├── launch-gui.sh
└── README.md
```

## Quick start

1. Copy the starter somewhere local.
2. Run the interactive setup script:

```bash
./bin/setup-site
```

Or manually copy the example site config:

```bash
cp config/sites/example-site.env config/sites/client-a.env
```

3. Edit `config/sites/client-a.env` (if created manually).
4. Make scripts executable:

```bash
chmod +x bin/pull bin/push bin/watch
```

5. Pull the remote folders locally:

```bash
./bin/pull client-a
```

6. Start watch mode:

```bash
./bin/watch client-a
```

## Native macOS Application

A native desktop GUI application is available for easy management of sync tasks.

### For End Users

If you received the **Webmix Sync Tool.app** or a DMG installer:

1. **Install dependencies**: `brew install fswatch rsync`
2. **Install the app**: Drag to Applications folder
3. **Bypass security warning**: See [INSTALLATION.md](INSTALLATION.md) for detailed instructions
4. **First launch**: Configure WordPress credentials in Settings
5. **Use the app**: Pull, Push, Watch, SSH terminal, and more

⚠️ **Important:** Because the app is not signed with an Apple Developer certificate, macOS will show a security warning on first launch. See [INSTALLATION.md](INSTALLATION.md) for easy bypass instructions.

**Detailed guides:**
- [INSTALLATION.md](INSTALLATION.md) - Complete installation and setup guide
- [CODESIGNING.md](CODESIGNING.md) - Code signing and security information

### For Developers

To build the application for distribution:

```bash
./build-app.sh
```

This creates a standalone macOS app at `dist/Webmix Sync Tool.app` that includes:
- Python runtime
- All dependencies (PyQt5, requests, etc.)
- GUI interface
- All sync scripts
- Ad-hoc code signing (reduces but doesn't eliminate security warnings)

To create a professional DMG installer:

```bash
./create-dmg.sh
```

**Build documentation:**
- [BUILD.md](BUILD.md) - Build process and requirements
- [CODESIGNING.md](CODESIGNING.md) - Code signing for production distribution

### Native App Features

- **WordPress Integration** - Authenticate with WordPress REST API to fetch site configurations
- **Site Management** - Configure multiple sites with SSH details, paths, and sync items
- **Pull/Push/Watch** - All CLI commands available through native buttons
- **SSH Terminal** - Embedded terminal for direct server access
- **Real-time Output** - Live command output in the app
- **Settings Persistence** - Settings stored in Application Support (survive app updates)
- **Test Connections** - Verify SSH connectivity before syncing

## Site config example

Each site config is a simple `.env`-style file.

Important fields:

- `SSH_HOST`: server hostname
- `SSH_PORT`: usually `22`
- `SSH_USER`: cPanel account username or deploy user
- `LOCAL_ROOT`: local project root
- `REMOTE_ROOT`: remote web root
- `SYNC_ITEMS`: newline-separated relative paths under the roots

Example:

```env
SITE_KEY="client-a"
SSH_HOST="example.host"
SSH_PORT="22"
SSH_USER="cpaneluser"
LOCAL_ROOT="$HOME/Sites/client-a/public_html/wp-content"
REMOTE_ROOT="/home/cpaneluser/public_html/wp-content"
SYNC_ITEMS="themes/client-theme
plugins/client-plugin"
RSYNC_DELETE="0"
DEBOUNCE_SECONDS="1"
```

## Commands

### Pull

```bash
./bin/pull client-a
```

Fetches the allowed folders from server to local.
After pull, the script also ensures `LOCAL_ROOT/.github/copilot-instructions.md`
from this repository template.

Dry run:

```bash
./bin/pull client-a --dry-run
```

In dry-run mode, the Copilot instructions file is not written.

### Push

```bash
./bin/push client-a
```

Pushes local changes to server.

Dry run:

```bash
./bin/push client-a --dry-run
```

### Watch

```bash
./bin/watch client-a
```

Watches the configured local folders and pushes changes after a small debounce.

## Delete behavior

By default, remote deletes are **disabled**.

That means if you delete a local file, it will **not** be removed remotely unless you explicitly enable:

```env
RSYNC_DELETE="1"
```

For production-like environments, keeping delete off is safer.

## Suggested usage

- staging: watch mode allowed
- production: push only, preferably dry-run first

## Safety guardrails

These scripts:

- only sync paths listed in `SYNC_ITEMS`
- require paths to stay inside the configured local/remote roots
- ignore common junk via `config/excludes.txt`
- use SSH keys only

## Notes

- Do not point this at full `wp-content` unless you really mean it.
- Keep it limited to custom themes/plugins.
- Do not sync uploads, caches, backups, or secrets.
