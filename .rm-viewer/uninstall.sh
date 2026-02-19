systemctl stop rm-viewer-sync
systemctl disable rm-viewer-sync
rm /etc/systemd/system/rm-viewer-sync.service
systemctl daemon-reload
