#!/bin/bash
#
# Release script for Webmix Sync Starter
# Automates version updates and distribution package creation
#

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo "======================================"
echo "Webmix Sync Starter - Release Builder"
echo "======================================"
echo ""

# Get current version from setup.py
CURRENT_VERSION=$(grep "CFBundleVersion" setup.py | head -1 | sed "s/.*'\(.*\)'.*/\1/")
echo -e "${BLUE}Current version: ${CURRENT_VERSION}${NC}"
echo ""

# Ask for new version
echo "Enter new version number (e.g., 1.0.1):"
read -r NEW_VERSION

# Trim whitespace
NEW_VERSION=$(echo "$NEW_VERSION" | xargs)

if [[ -z "$NEW_VERSION" ]]; then
    echo -e "${RED}Error: Version number required${NC}"
    exit 1
fi

# Validate version format (basic check)
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Version must be in format X.Y.Z (e.g., 1.0.12)${NC}"
    exit 1
fi

# Confirm
echo ""
echo -e "${YELLOW}Ready to release version ${NEW_VERSION}${NC}"
echo "This will:"
echo "  1. Update version in setup.py"
echo "  2. Clean and rebuild the app"
echo "  3. Create DMG installer"
echo "  4. Rename DMG with version number"
echo ""
echo "Continue? (y/n)"
read -r CONFIRM

if [[ "$CONFIRM" != "y" ]]; then
    echo "Cancelled."
    exit 0
fi

# Update version in setup.py and gui/wp-sync-native-tabbed.py
echo ""
echo "📝 Updating version in setup.py and wp-sync-native-tabbed.py..."
sed -i.bak "s/'CFBundleVersion': '.*'/'CFBundleVersion': '$NEW_VERSION'/g" setup.py
sed -i.bak "s/'CFBundleShortVersionString': '.*'/'CFBundleShortVersionString': '$NEW_VERSION'/g" setup.py
rm setup.py.bak

sed -i.bak 's/APP_VERSION = ".*"/APP_VERSION = "'$NEW_VERSION'"/g' gui/wp-sync-native-tabbed.py
rm gui/wp-sync-native-tabbed.py.bak

echo -e "${GREEN}✓ Version updated to ${NEW_VERSION} in both files${NC}"

# Clean previous builds
echo ""
echo "🧹 Cleaning previous builds..."
rm -rf build dist
echo -e "${GREEN}✓ Cleaned${NC}"

# Build app
echo ""
echo "🔨 Building application..."
./build-app.sh
if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Build failed${NC}"
    exit 1
fi

# Create DMG
echo ""
echo "📀 Creating DMG installer..."
./create-dmg.sh
if [ $? -ne 0 ]; then
    echo -e "${RED}✗ DMG creation failed${NC}"
    exit 1
fi

# Rename DMG with version
echo ""
echo "📦 Renaming DMG with version..."
DMG_VERSIONED="Webmix-Sync-Starter-v${NEW_VERSION}.dmg"
mv "dist/Webmix-Sync-Starter-Installer.dmg" "dist/${DMG_VERSIONED}"
echo -e "${GREEN}✓ Created: dist/${DMG_VERSIONED}${NC}"

# Calculate size and checksum
DMG_SIZE=$(du -sh "dist/${DMG_VERSIONED}" | cut -f1)
DMG_SHA256=$(shasum -a 256 "dist/${DMG_VERSIONED}" | cut -d' ' -f1)

# Summary
echo ""
echo "======================================"
echo -e "${GREEN}✅ Release ${NEW_VERSION} Complete!${NC}"
echo "======================================"
echo ""
echo "📦 Package: dist/${DMG_VERSIONED}"
echo "💾 Size: ${DMG_SIZE}"
echo "🔐 SHA-256: ${DMG_SHA256}"
echo ""

# Offer to create GitHub Release
echo ""
echo -e "${BLUE}Would you like to create a GitHub Release?${NC}"
echo "(Requires: git, GitHub CLI 'gh', and repo pushed to GitHub)"
echo ""
read -p "Create GitHub Release? [Y/n]: " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$  ]] || [[ -z $REPLY ]]; then
    echo ""
    echo "🚀 Creating GitHub Release..."
    
    # Check if gh CLI is installed
    if ! command -v gh &> /dev/null; then
        echo -e "${YELLOW}⚠️  GitHub CLI (gh) is not installed${NC}"
        echo "Install it with: brew install gh"
        echo ""
        echo "Manual steps:"
        echo "  1. Commit changes: git commit -am 'Release v${NEW_VERSION}'"
        echo "  2. Create tag: git tag v${NEW_VERSION}"
        echo "  3. Push: git push && git push --tags"
        echo "  4. Create release on GitHub and upload: dist/${DMG_VERSIONED}"
        exit 0
    fi
    
    # Check if logged in to GitHub
    if ! gh auth status &> /dev/null; then
        echo -e "${YELLOW}⚠️  Not logged in to GitHub${NC}"
        echo "Run: gh auth login"
        exit 1
    fi
    
    # Commit version changes
    echo "📝 Committing version changes..."
    git add setup.py gui/wp-sync-native-tabbed.py
    git commit -m "Release v${NEW_VERSION}" || true
    
    # Create git tag
    echo "🏷️  Creating git tag v${NEW_VERSION}..."
    git tag -a "v${NEW_VERSION}" -m "Release version ${NEW_VERSION}"
    
    # Push changes and tags
    echo "⬆️  Pushing to GitHub..."
    git push origin main || git push origin master
    git push --tags
    
    # Prompt for release notes
    echo ""
    echo "Enter release notes (press Ctrl+D when done, or leave empty for default):"
    RELEASE_NOTES=$(cat)
    
    if [[ -z "$RELEASE_NOTES" ]]; then
        RELEASE_NOTES="Release version ${NEW_VERSION}

📦 Download the DMG below and install

**Installation:**
1. Download ${DMG_VERSIONED}
2. Open the DMG
3. Drag app to Applications folder (replace existing)
4. Launch the app

**File Info:**
- Size: ${DMG_SIZE}
- SHA-256: ${DMG_SHA256}

**Updates:**
- See CHANGELOG.md for details"
    fi
    
    # Create GitHub release and upload DMG
    echo "🎉 Creating GitHub release..."
    gh release create "v${NEW_VERSION}" \
        "dist/${DMG_VERSIONED}" \
        --title "Webmix Sync Starter v${NEW_VERSION}" \
        --notes "$RELEASE_NOTES"
    
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✅ GitHub Release created successfully!${NC}"
        echo ""
        echo "🌐 View release: https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v${NEW_VERSION}"
        echo ""
        echo -e "${GREEN}🎉 Your app will now auto-update from GitHub!${NC}"
        echo "Users with version v${NEW_VERSION} will be notified of future updates."
    else
        echo -e "${RED}✗ Failed to create GitHub release${NC}"
        echo "You can create it manually on GitHub and upload: dist/${DMG_VERSIONED}"
    fi
else
    echo ""
    echo "Skipping GitHub Release."
    echo ""
    echo "Manual steps to publish:"
    echo "  1. Commit changes: git commit -am 'Release v${NEW_VERSION}'"
    echo "  2. Create tag: git tag v${NEW_VERSION}"
    echo "  3. Push: git push && git push --tags"
    echo "  4. Create release on GitHub: gh release create v${NEW_VERSION} dist/${DMG_VERSIONED}"
    echo ""
fi

echo ""
echo "Next steps:"
echo "  • Test the app: open 'dist/${DMG_VERSIONED}'"
echo "  • Update CHANGELOG.md with changes"
echo "  • Share release URL with your team"
echo ""
