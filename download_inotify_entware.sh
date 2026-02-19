#!/bin/bash

INOTIFYWAIT_URL="https://bin.entware.net/aarch64-k3.10/inotifywait_4.23.9.0-2_aarch64-3.10.ipk"
LIBINOTIFYTOOLS_URL="https://bin.entware.net/aarch64-k3.10/libinotifytools_4.23.9.0-2_aarch64-3.10.ipk"

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# download packages from entware into temp dir
echo "downloading $INOTIFYWAIT_URL ..."
curl -s "$INOTIFYWAIT_URL" --output "$TMPDIR/inotifywait.ipk"
echo "downloading $LIBINOTIFYTOOLS_URL ..."
curl -s "$LIBINOTIFYTOOLS_URL" --output "$TMPDIR/libinotifytools.ipk"

# extract inotifywait
mkdir "$TMPDIR/inotifywait"
tar -xf "$TMPDIR/inotifywait.ipk" -C "$TMPDIR/inotifywait"
tar -xf "$TMPDIR/inotifywait/data.tar.gz" -C "$TMPDIR/inotifywait"
cp "$TMPDIR/inotifywait/opt/bin/inotifywait" .

# extract libinotifytools
mkdir "$TMPDIR/libinotifytools"
tar -xf "$TMPDIR/libinotifytools.ipk" -C "$TMPDIR/libinotifytools"
tar -xf "$TMPDIR/libinotifytools/data.tar.gz" -C "$TMPDIR/libinotifytools"
cp "$TMPDIR/libinotifytools/opt/lib/libinotifytools.so.0" .
