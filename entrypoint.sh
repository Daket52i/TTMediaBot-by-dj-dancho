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

# The provider remains as a standalone BotGuard/PO Token service.
node /opt/bgutil-provider/server/build/main.js &
POT_PID=$!

# TTMediaBot.sh starts and owns the persistent YouTube.js bridge.

exec "$@"
