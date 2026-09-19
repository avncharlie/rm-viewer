#!/bin/sh

# ----------------------------------------------
# exit on errors
# ----------------------------------------------
set -e


log() { echo "[$(date '+%H:%M:%S')] $*"; }


# ----------------------------------------------
# make the rootfs writeable
#
# /etc is an overlay mount and / is read-only, so the service file below
# cannot be removed until both are dealt with. Root is put back to read-only
# on the way out, including when something fails part way through.
# ----------------------------------------------
_root_was_remounted=0

restore_root() {
    if [ "$_root_was_remounted" = "1" ]; then
        log "remounting / read-only"
        mount -o remount,ro / || log "WARNING: could not remount / read-only"
    fi
}
trap restore_root EXIT

log "unmounting /etc overlay"
umount -R /etc || log "/etc overlay not mounted, continuing"

log "remounting / read-write"
mount -o remount,rw /
_root_was_remounted=1


# ----------------------------------------------
# remove service
#
# Tolerate a service that is already stopped, disabled or removed, so the
# uninstall still reaches daemon-reload and the read-only remount.
# ----------------------------------------------
systemctl kill -s SIGKILL rm-viewer-sync || true
systemctl disable rm-viewer-sync || true
rm -f /etc/systemd/system/rm-viewer-sync.service
systemctl daemon-reload
