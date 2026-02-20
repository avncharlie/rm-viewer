#!/bin/sh

# ----------------------------------------------
# get current dir and source config
# ----------------------------------------------
DIR=$(dirname $(realpath $0))
source $DIR/config.sh

REMOTE_DIRTY="$SYNC_DIR/xochitl-dirty"
SYNCFLAG="$SYNC_DIR/syncflag"

INOTIFYWAIT="/lib/ld-linux-aarch64.so.1 $INSTALL_DIR/inotifywait"
EVENT_FIFO="/tmp/rm-viewer-sync.events"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "starting sync.sh, watching $XOCHITL"

LAST_ATTEMPT=0
PENDING=0
INOPID=""

cleanup() {
    if [ -n "$INOPID" ]; then
        kill "$INOPID" 2>/dev/null || true
    fi
    exec 3>&- 2>/dev/null || true
    rm -f "$EVENT_FIFO"
}

start_inotify() {
    $INOTIFYWAIT -q -m -r "$XOCHITL" \
        -e modify -e create -e delete -e move \
        --format x >"$EVENT_FIFO" 2>/dev/null &
    INOPID=$!
}

trap cleanup EXIT INT TERM

attempt_sync() {
    (
        flock -n 200 || { log "skipped: already syncing"; exit 1; }

        # check syncflag — skip if remote hasn't consumed last sync
        if ssh $SSH_OPTS $REMOTE_USER@$REMOTE_HOST "test -f '$SYNCFLAG'"; then
            log "skipped: syncflag present"
            exit 1
        fi

        log "rsyncing to dirty..."
        rsync -a \
            --timeout=60 \
            --partial-dir=.rsync-partial \
            --no-compress \
            --delete-delay \
            --itemize-changes \
            --out-format='%i %n%L' \
            --exclude='*.thumbnails/***' \
            -e "ssh $SSH_OPTS" \
            --rsync-path="$RSYNC_REMOTE" \
            "$XOCHITL/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIRTY/"
        RC=$?

        if [ $RC -eq 0 ]; then
            if ssh $SSH_OPTS $REMOTE_USER@$REMOTE_HOST "touch '$SYNCFLAG'"; then
                log "sync complete, syncflag created"
                exit 0
            fi
            log "failed to create syncflag"
            exit 1
        fi

        log "rsync failed (exit $RC)"
        exit 1
    ) 200>"$LOCKFILE"

    RC=$?
    return $RC
}

# Persistent inotify monitor -> FIFO.
rm -f "$EVENT_FIFO"
mkfifo "$EVENT_FIFO"
exec 3<>"$EVENT_FIFO"
start_inotify

# Rate-limited syncing policy:
# - at most one sync attempt every DEBOUNCE seconds
# - first change after idle syncs ASAP
# - if blocked/failing, keep PENDING and retry on next slot
while true; do
    if ! kill -0 "$INOPID" 2>/dev/null; then
        log "inotifywait exited; restarting"
        start_inotify
    fi

    if [ $PENDING -eq 0 ]; then
        if read line <&3; then
            log "changes detected"
            PENDING=1
        else
            sleep 1
            continue
        fi
    fi

    NOW=$(date +%s)
    NEXT_ATTEMPT=$((LAST_ATTEMPT + DEBOUNCE))
    WAIT=$((NEXT_ATTEMPT - NOW))

    while [ $WAIT -gt 0 ]; do
        if read -t $WAIT line <&3; then
            : # consume additional events while waiting for next slot
        else
            break
        fi
        NOW=$(date +%s)
        WAIT=$((NEXT_ATTEMPT - NOW))
    done

    if attempt_sync; then
        PENDING=0
    else
        log "sync attempt not completed; will retry"
    fi
    LAST_ATTEMPT=$(date +%s)
done
