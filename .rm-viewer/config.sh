#!/bin/sh

# ----------------------------------------------
# environment
# ----------------------------------------------
HOME="/home/root"
INSTALL_DIR="$HOME/.rm-viewer"
LIB_DIR="$INSTALL_DIR/lib"

SERVICE="rm-viewer-sync"

# ----------------------------------------------
# exports
# ----------------------------------------------
export LD_LIBRARY_PATH="$LIB_DIR"

# ----------------------------------------------
# sync config
# ----------------------------------------------
REMOTE_USER=""
REMOTE_HOST=""
SYNC_DIR="~/Documents/Remarkable/rm-viewer-sync/sync"

RSYNC_REMOTE="/opt/homebrew/bin/rsync"
DEBOUNCE=30

SSH_KEY="$INSTALL_DIR/rm-viewer-sync-key"

# Dropbear settings
#  -K 15: keepalive every 15 seconds once connection established
#  -I 120: timeout after no data recieved for 2 minutes
# This is so a dropped connection (e.g. wifi going away mid-sync) can't hang
# ssh forever
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -K 15 -I 120"

XOCHITL="/home/root/.local/share/remarkable/xochitl"
LOCKFILE="/tmp/rm-viewer-sync.lock"
