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


# ----------------------------------------------
# create systemd service if needed
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

if [ ! -f /etc/systemd/system/$SERVICE.service ]; then
  echo "$_service" > /etc/systemd/system/$SERVICE.service
fi


# ----------------------------------------------
# create sync ssh key
# ----------------------------------------------
rm -f $DIR/rm-viewer-sync-key
rm -f $DIR/rm-viewer-sync-key.pub
dropbearkey -f $DIR/rm-viewer-sync-key

# ----------------------------------------------
# install service
# ----------------------------------------------
systemctl daemon-reload && systemctl enable --now $SERVICE

# ----------------------------------------------
# epilogue
# ----------------------------------------------
echo
echo "SUCCESS - rm-viewer-sync installed."
echo
echo "Make sure to add $DIR/rm-viewer-sync-key.pub to your remote's ssh keys file."
echo
echo "To see logs:"
echo "   journalctl -fu rm-viewer-sync"
echo "To uninstall:"
echo "   ./uninstall.sh"
echo
