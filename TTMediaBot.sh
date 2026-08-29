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

    # Wait for YouTube.js bridge to be fully up and ready before launching the bot
    PORT=${YOUTUBE_BRIDGE_PORT:-4417}
    for i in $(seq 1 100); do
        if curl -s -f "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
            break
        fi
        sleep 0.05
    done
fi

python3 "$PROGDIR/$PROGNAME" "$@"
