#!/bin/sh

# ----------------------------------------------
# get current dir and source config
# ----------------------------------------------
DIR=$(dirname $(realpath $0))
source $DIR/config.sh

REMOTE_DIRTY="$SYNC_DIR/xochitl-dirty"
SYNCFLAG="$SYNC_DIR/syncflag"

INOTIFYWAIT="/lib/ld-linux-aarch64.so.1 $INSTALL_DIR/inotifywait"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "starting sync.sh, watching $XOCHITL"

LAST_SYNC=0

# monitor → debounce → flock → syncflag check → rsync → create syncflag
$INOTIFYWAIT -m -r "$XOCHITL" --format '%w%f' -e modify -e create -e delete -e move |
while true; do
    read line                          # block until first event
    log "changes detected"
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST_SYNC))
    if [ $ELAPSED -lt $DEBOUNCE ]; then
        while read -t $DEBOUNCE line; do :; done   # drain until quiet for DEBOUNCE seconds
    fi

    (
        flock -n 200 || { log "skipped: already syncing"; exit 0; }

        # check syncflag — skip if remote hasn't consumed last sync
        if ssh $SSH_OPTS $REMOTE_USER@$REMOTE_HOST "test -f '$SYNCFLAG'"; then
            log "skipped: syncflag present"
            exit 0
        fi

        log "rsyncing to dirty..."
        rsync -a \
            --timeout=60 \
            --partial-dir=.rsync-partial \
            --no-compress \
            --delete-delay \
            --exclude='*.thumbnails/***' \
            -e "ssh $SSH_OPTS" \
            --rsync-path="$RSYNC_REMOTE" \
            "$XOCHITL/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIRTY/"

        if [ $? -eq 0 ]; then
            ssh $SSH_OPTS $REMOTE_USER@$REMOTE_HOST "touch '$SYNCFLAG'"
            log "sync complete, syncflag created"
        else
            log "rsync failed (exit $?)"
        fi
    ) 200>"$LOCKFILE"

    LAST_SYNC=$(date +%s)
done
