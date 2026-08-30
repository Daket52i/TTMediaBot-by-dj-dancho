#!/bin/bash
set -e

rm -rf /tmp/pulseaudio*
rm -rf ~/.config/pulse
rm -rf ~/.pulse

mkdir -p ~/.config/pulse
cat << 'EOF' > ~/.config/pulse/daemon.conf
default-sample-format = s16le
default-sample-rate = 48000
alternate-sample-rate = 48000
default-sample-channels = 2
default-channel-map = front-left,front-right
resample-method = speex-float-3
high-priority = no
realtime-scheduling = no
default-fragments = 8
default-fragment-size-msec = 25
EOF

cat << 'EOF' > ~/.config/pulse/default.pa
.include /etc/pulse/default.pa
load-module module-null-sink sink_name=TTMediaBotSink rate=48000 channels=2 sink_properties=device.description="TTMediaBot_Audio_Sink"
set-default-sink TTMediaBotSink
set-default-source TTMediaBotSink.monitor
EOF

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
