#!/bin/bash
set -e

rm -rf /tmp/pulseaudio*
rm -rf ~/.config/pulse
rm -rf ~/.pulse

pulseaudio -D --exit-idle-time=-1

if ! pactl info > /dev/null 2>&1; then
    echo "PulseAudio failed to start."
fi

echo "PulseAudio started successfully."

PORT_SEED=$(printf '%s' "${TTBOT_INSTANCE:-$HOSTNAME}" | cksum | awk '{print $1}')
PORT_BASE=$((20000 + (PORT_SEED % 9000) * 2))
POT_PROVIDER_PORT=${POT_PROVIDER_PORT:-$PORT_BASE}
YOUTUBE_BRIDGE_PORT=${YOUTUBE_BRIDGE_PORT:-$((PORT_BASE + 1))}
export POT_PROVIDER_URL=${POT_PROVIDER_URL:-http://127.0.0.1:${POT_PROVIDER_PORT}/get_pot}
export YOUTUBE_BRIDGE_URL=${YOUTUBE_BRIDGE_URL:-http://127.0.0.1:${YOUTUBE_BRIDGE_PORT}}
export YOUTUBE_BRIDGE_PORT

# The provider remains as a standalone BotGuard/PO Token service.
node /opt/bgutil-provider/server/build/main.js --port "$POT_PROVIDER_PORT" &
POT_PID=$!

# TTMediaBot.sh starts and owns the persistent YouTube.js bridge.

exec "$@"
