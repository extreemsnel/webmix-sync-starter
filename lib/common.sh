#!/usr/bin/env bash
# Don't set -e here - let the calling script decide error handling
# This allows watch script to handle errors gracefully while pull/push can be strict

# Ensure Homebrew paths are available (for fswatch, etc.)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/config"
COPILOT_INSTRUCTIONS_REL_PATH=".github/copilot-instructions.md"
COPILOT_INSTRUCTIONS_TEMPLATE="$PROJECT_ROOT/$COPILOT_INSTRUCTIONS_REL_PATH"

# Check if sites exist in Application Support (for bundled app)
APP_SUPPORT_DIR="$HOME/Library/Application Support/Webmix Sync Starter"
OLD_APP_SUPPORT_DIR="$HOME/Library/Application Support/Webmix Sync Tool"

# Check new location first, then old app name, then fallback to project directory
if [[ -d "$APP_SUPPORT_DIR/sites" ]]; then
  # Use new Application Support directory
  SITES_DIR="$APP_SUPPORT_DIR/sites"
elif [[ -d "$OLD_APP_SUPPORT_DIR/sites" ]]; then
  # Use old app name directory (for backwards compatibility during transition)
  SITES_DIR="$OLD_APP_SUPPORT_DIR/sites"
else
  # Fallback to project directory (development mode)
  SITES_DIR="$CONFIG_DIR/sites"
fi

EXCLUDES_FILE="$CONFIG_DIR/excludes.txt"
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

usage_site_arg() {
  die "Usage: $0 <site-key> [--dry-run]"
}

load_site_config() {
  local site_key="${1:-}"
  [[ -n "$site_key" ]] || usage_site_arg

  SITE_KEY="$site_key"
  SITE_FILE="$SITES_DIR/$site_key.env"
  [[ -f "$SITE_FILE" ]] || die "Site config not found: $SITE_FILE"

  # shellcheck disable=SC1090
  source "$SITE_FILE"

  : "${SSH_HOST:?Missing SSH_HOST in $SITE_FILE}"
  : "${SSH_PORT:?Missing SSH_PORT in $SITE_FILE}"
  : "${SSH_USER:?Missing SSH_USER in $SITE_FILE}"
  : "${LOCAL_ROOT:?Missing LOCAL_ROOT in $SITE_FILE}"
  : "${REMOTE_ROOT:?Missing REMOTE_ROOT in $SITE_FILE}"
  : "${SYNC_ITEMS:?Missing SYNC_ITEMS in $SITE_FILE}"

  RSYNC_DELETE="${RSYNC_DELETE:-0}"
  DEBOUNCE_SECONDS="${DEBOUNCE_SECONDS:-1}"
  SSH_KEY_FILE="${SSH_KEY_FILE:-}"

  LOCAL_ROOT="$(eval printf '%s' "$LOCAL_ROOT")"
}

build_ssh_cmd() {
  local -a cmd=(ssh -q -p "$SSH_PORT" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
  if [[ -n "$SSH_KEY_FILE" ]]; then
    cmd+=(-i "$SSH_KEY_FILE")
  fi
  printf '%q ' "${cmd[@]}"
}

build_rsync_base_args() {
  local -a args=(-az --compress --human-readable --itemize-changes)
  
  # Add exclude-from only if file exists and path is properly handled
  if [[ -f "$EXCLUDES_FILE" ]]; then
    args+=(--exclude-from="$EXCLUDES_FILE")
  fi

  if [[ "$RSYNC_DELETE" == "1" ]]; then
    args+=(--delete)
  fi

  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    args+=(--dry-run)
  fi

  printf '%q ' "${args[@]}"
}

normalize_sync_items() {
  printf '%s\n' "$SYNC_ITEMS" | sed '/^[[:space:]]*$/d'
}

ensure_safe_relative_path() {
  local rel="$1"

  [[ "$rel" != /* ]] || die "SYNC_ITEMS must be relative, got absolute path: $rel"
  [[ "$rel" != *'..'* ]] || die "SYNC_ITEMS may not contain '..': $rel"
  [[ "$rel" != '.' ]] || die "SYNC_ITEMS may not be '.': $rel"
}

ensure_local_dirs_exist_for_pull() {
  while IFS= read -r rel; do
    ensure_safe_relative_path "$rel"
    mkdir -p "$LOCAL_ROOT/$rel"
  done < <(normalize_sync_items)
}

print_summary() {
  log "Site: $SITE_KEY"
  log "Host: $SSH_USER@$SSH_HOST:$SSH_PORT"
  log "Local root: $LOCAL_ROOT"
  log "Remote root: $REMOTE_ROOT"
  log "Delete enabled: $RSYNC_DELETE"
  log "Dry run: ${DRY_RUN:-0}"
}

run_rsync_pull_item() {
  local rel="$1"
  ensure_safe_relative_path "$rel"

  # Build SSH command as string for -e flag
  local ssh_cmd
  ssh_cmd="$(build_ssh_cmd)"

  # Build rsync args as array
  local -a rsync_args=(-az --compress --human-readable --itemize-changes)
  
  if [[ -f "$EXCLUDES_FILE" ]]; then
    rsync_args+=(--exclude-from="$EXCLUDES_FILE")
  fi
  
  if [[ "$RSYNC_DELETE" == "1" ]]; then
    rsync_args+=(--delete)
  fi
  
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    rsync_args+=(--dry-run)
  fi

  local remote="$SSH_USER@$SSH_HOST:$REMOTE_ROOT/$rel/"
  local local="$LOCAL_ROOT/$rel/"

  log "Pulling: $rel"
  # shellcheck disable=SC2086
  rsync "${rsync_args[@]}" -e "$ssh_cmd" "$remote" "$local"
  log "Pull complete"
}

run_rsync_push_item() {
  local rel="$1"
  ensure_safe_relative_path "$rel"

  # Build SSH command as string for -e flag
  local ssh_cmd
  ssh_cmd="$(build_ssh_cmd)"

  # Build rsync args as array
  local -a rsync_args=(-az --compress --human-readable --itemize-changes)
  
  if [[ -f "$EXCLUDES_FILE" ]]; then
    rsync_args+=(--exclude-from="$EXCLUDES_FILE")
  fi
  
  if [[ "$RSYNC_DELETE" == "1" ]]; then
    rsync_args+=(--delete)
  fi
  
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    rsync_args+=(--dry-run)
  fi

  local local="$LOCAL_ROOT/$rel/"
  local remote="$SSH_USER@$SSH_HOST:$REMOTE_ROOT/$rel/"

  [[ -d "$LOCAL_ROOT/$rel" ]] || die "Local path does not exist: $LOCAL_ROOT/$rel"

  log "Pushing: $rel"
  # shellcheck disable=SC2086
  rsync "${rsync_args[@]}" -e "$ssh_cmd" "$local" "$remote"
  local rsync_exit=$?
  log "Push complete"
  return $rsync_exit
}

ensure_copilot_instructions_for_pull() {
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    log "Dry run enabled, skipping Copilot instructions file update"
    return
  fi

  if [[ ! -f "$COPILOT_INSTRUCTIONS_TEMPLATE" ]]; then
    log "Copilot instructions template not found, skipping: $COPILOT_INSTRUCTIONS_TEMPLATE"
    return
  fi

  local dest_dir="$LOCAL_ROOT/.github"
  local dest_file="$dest_dir/copilot-instructions.md"

  mkdir -p "$dest_dir"
  cp "$COPILOT_INSTRUCTIONS_TEMPLATE" "$dest_file"
  log "Ensured Copilot instructions file: $dest_file"
}

run_all_pull() {
  while IFS= read -r rel; do
    run_rsync_pull_item "$rel"
  done < <(normalize_sync_items)

  ensure_copilot_instructions_for_pull
}

run_all_push() {
  while IFS= read -r rel; do
    run_rsync_push_item "$rel"
  done < <(normalize_sync_items)
}

watch_paths() {
  while IFS= read -r rel; do
    printf '%s\n' "$LOCAL_ROOT/$rel"
  done < <(normalize_sync_items)
}

assert_watch_paths_exist() {
  while IFS= read -r p; do
    [[ -d "$p" ]] || die "Watch path does not exist: $p"
  done < <(watch_paths)
}
