# Multi-Tab Migration Guide

## Overview

The multi-tab version (`wp-sync-native-tabbed.py`) allows you to work on multiple sites simultaneously without constant switching. Each site gets its own tab with independent watch mode, logs, and controls.

## What's New

### Core Features
- **Tabs instead of dropdown** - Each site opens in its own tab
- **Simultaneous watch modes** - Up to 5 sites can watch at once (configurable via `MAX_CONCURRENT_WATCHES`)
- **Independent logs** - Each tab has its own console output
- **Self-contained controls** - All action buttons (Pull/Push/Watch) are in each tab
- **Session persistence** - Reopens your last set of tabs on launch

### Tab Management
- **"+" button** - Opens searchable site picker
- **Close tabs** - X button on each tab, warns if watch is active
- **Drag to reorder** - Rearrange tabs by dragging
- **Visual indicators**:
  - 🟢 Green icon + green text = watching
  - ⚪ Gray icon = idle

### System Tray
- Shows count: "🟢 3 watching" when multiple sites are active
- Menu lists all watching sites

### Safety Features
- **5-watch limit** - Prevents resource overload
- **Close confirmation** - Warns when closing tab with active watch
- **Quit confirmation** - Lists all active watches before quitting
- **Empty push protection** - Same safety checks as before

## Testing the New Version

### 1. Backup Current Setup
```bash
# Backup your current app
cp gui/wp-sync-native.py gui/wp-sync-native-backup.py
```

### 2. Run Side-by-Side
```bash
# Test the new version without replacing the old one
cd /Users/bram/Documents/GitHub/webmix-sync-starter
python3 gui/wp-sync-native-tabbed.py
```

### 3. Test Workflow
1. **Open multiple sites**
   - Click the "+" button
   - Search and select a site
   - Repeat for 2-3 sites

2. **Start multiple watches**
   - Click "Watch" in each tab
   - Verify all tabs show 🟢 icon
   - Check system tray shows "🟢 3 watching"

3. **Make changes**
   - Edit files in one site's local folder
   - Watch that tab's console for sync activity
   - Other tabs remain independent

4. **Close and restore**
   - Close the app
   - Reopen - should restore your open tabs

5. **Test watch limit**
   - Try starting watch on 6 sites
   - Should show error after 5th watch

## Migration Path

### Option A: Replace Existing (Recommended)
Once you've tested and confirmed it works:

```bash
# Backup old version
mv gui/wp-sync-native.py gui/wp-sync-native-old.py

# Use new version
mv gui/wp-sync-native-tabbed.py gui/wp-sync-native.py

# Rebuild app (if using .app bundle)
./build-app.sh
```

### Option B: Run Both Versions
Keep both files and choose at runtime:

```bash
# Old version (dropdown)
python3 gui/wp-sync-native-old.py

# New version (tabs)
python3 gui/wp-sync-native.py
```

## Known Limitations

### Not Yet Implemented
These features from the original version need to be ported:

1. **SSH Terminal dialog** - Currently shows placeholder message
2. **New Site creation dialog** - Opens placeholder, use File > New Site
3. **Settings dialog** - Placeholder, settings file still works
4. **Update checker integration** - Placeholder
5. **Permission management (Open/Close Rights)** - Not yet ported
6. **Configure/Edit site dialogs** - Not yet ported
7. **WordPress API sync** - Not yet ported

### To Complete
Add these classes from original `wp-sync-native.py`:
- `SSHTerminalDialog` (lines ~234-418)
- `RemoteFolderSelectorDialog` (lines ~419-692)
- `ConfigureSiteDialog` (lines ~693-987)
- `SettingsDialog` (lines ~988-1275)
- `PermissionsThread` (lines ~185-233)
- `NewSiteDialog` (lines ~1388-1535)
- `SelectSiteDialog` (lines ~1536-1635)

And implement these methods:
- `create_new_site()` - full implementation
- `open_settings()` - full dialog
- `check_for_updates()` - if using UpdateChecker
- Open/close rights functionality

## Configuration

### Adjust Watch Limit
Edit line 13 in `wp-sync-native-tabbed.py`:

```python
MAX_CONCURRENT_WATCHES = 5  # Change to 3, 8, 10, etc.
```

### Session Storage
Sessions save to:
```
~/Library/Application Support/Webmix Sync Starter/app-settings.json
```

New keys:
- `open_tabs`: Array of site keys that were open
- `tab_order`: Order of tabs (currently unused, for future drag-to-reorder)

## Rollback

If you need to revert:

```bash
# Restore old version
mv gui/wp-sync-native-old.py gui/wp-sync-native.py

# Remove session data (optional)
# This clears the "open_tabs" setting
rm ~/Library/Application\ Support/Webmix\ Sync\ Starter/app-settings.json
```

## Troubleshooting

### Tabs don't restore on launch
Check that `app-settings.json` has `open_tabs` array:
```bash
cat ~/Library/Application\ Support/Webmix\ Sync\ Starter/app-settings.json
```

### Watch limit not working
Verify `MAX_CONCURRENT_WATCHES` constant in the Python file.

### Tabs appear but buttons don't work
Site config files must exist in:
```
~/Library/Application Support/Webmix Sync Starter/sites/*.env
```

### System tray doesn't update
Check console output for errors. macOS status bar requires `AppKit` module.

## Development Notes

### Architecture

**SiteTab** (lines ~248-850)
- Self-contained widget for one site
- Own buttons, logs, threads
- Emits signals: `watch_started`, `watch_stopped`, `sync_status_changed`

**TabManager** (lines ~853-926)
- Coordinates all tabs
- Enforces watch limit
- Updates visual indicators
- Saves/restores sessions

**WPSyncGUI** (lines ~1054-1456)
- Main window with QTabWidget
- Handles tab lifecycle
- System tray integration
- Menu bar actions

### Key Differences from Original

| Original | Tabbed Version |
|----------|----------------|
| Single `QComboBox` dropdown | `QTabWidget` with multiple tabs |
| One `QTextEdit` output | Each tab has own output |
| One `watch_thread` | Each `SiteTab` has own `watch_thread` |
| `self.current_thread` | Each `SiteTab` has own `current_thread` |
| Site selection triggers UI rebuild | Tabs persist, switch instantly |
| No session restore | Restores open tabs on launch |

## Next Steps

1. **Complete missing features** - Port dialogs from original
2. **Test thoroughly** - Multiple sites, watch modes, edge cases
3. **Update build scripts** - Ensure .app bundle uses new version
4. **Update documentation** - README, GUI-GUIDE, etc.
5. **Consider adding**:
   - Tab context menu (right-click)
   - Keyboard shortcuts (Cmd+1-9 for tabs)
   - Global log view (all sites)
   - Tab pinning
   - Custom watch limits per site

## Questions?

Test the basic workflow first. The core multi-tab functionality works. Missing features can be added incrementally without breaking the tab architecture.
