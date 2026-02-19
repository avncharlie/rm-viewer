#!/bin/sh

# ----------------------------------------------
# get current dir and source config
# ----------------------------------------------
DIR=$(dirname $(realpath $0))
source $DIR/config.sh


# ----------------------------------------------
# exit on errors
# ----------------------------------------------
set -e


log() { echo "[$(date '+%H:%M:%S')] $*"; }


# ----------------------------------------------
# ensure install paths exist
# ----------------------------------------------
mkdir -p "$INSTALL_DIR"
mkdir -p "$LIB_DIR"


# ----------------------------------------------
# install/update systemd service
# ----------------------------------------------
_service=$(cat << EOF
[Unit]
Description=rm-viewer-sync
After=home.mount

[Service]
ExecStart=$INSTALL_DIR/sync.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
)

echo "$_service" > "/etc/systemd/system/$SERVICE.service"


# ----------------------------------------------
# ensure sync ssh key exists (reuse if present)
# ----------------------------------------------
if [ -f "$SSH_KEY" ]; then
    log "reusing existing sync key: $SSH_KEY"
else
    log "generating new sync key: $SSH_KEY"
    dropbearkey -f "$SSH_KEY"
fi

# Recreate .pub from private key if missing.
if [ ! -f "$SSH_KEY.pub" ]; then
    log "rebuilding missing public key: $SSH_KEY.pub"
    dropbearkey -y -f "$SSH_KEY" | sed -n 's/^Public key portion is://p;/^ssh-/p' | tail -n 1 > "$SSH_KEY.pub"
fi

# ----------------------------------------------
# install service
# ----------------------------------------------
systemctl daemon-reload
systemctl enable --now "$SERVICE"

# ----------------------------------------------
# epilogue
# ----------------------------------------------
echo
echo "SUCCESS - rm-viewer-sync installed."
echo
echo "Make sure to add $SSH_KEY.pub to your remote's ssh keys file."
echo
echo "To see logs:"
echo "   journalctl -fu rm-viewer-sync"
echo "To uninstall:"
echo "   ./uninstall.sh"
echo
