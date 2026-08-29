#!/bin/bash

set -e

PROGNAME=TTMediaBot.py
PROGDIR=$(dirname "$(readlink -f "$0")")
LD_LIBRARY_PATH=$PROGDIR/TeamTalk_DLL:$PROGDIR:$LD_LIBRARY_PATH
export LD_LIBRARY_PATH

BRIDGE_PID=""
cleanup() {
    if [ -n "$BRIDGE_PID" ]; then
        kill "$BRIDGE_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if [ -f "$PROGDIR/youtube_bridge/server.mjs" ]; then
    node "$PROGDIR/youtube_bridge/server.mjs" &
    BRIDGE_PID=$!
fi

python3 "$PROGDIR/$PROGNAME" "$@"
