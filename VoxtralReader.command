#!/usr/bin/env bash
# Double-click this file in Finder to launch Voxtral Reader.
# It runs start.sh in this Terminal window and opens the browser as soon as
# the app answers on /health.

cd "$(dirname "$0")"

APP_PORT="${APP_PORT:-7860}"
APP_URL="http://127.0.0.1:${APP_PORT}"

if curl -sf "${APP_URL}/health" > /dev/null 2>&1; then
  echo "  ✓ Voxtral Reader is already running"
  open "$APP_URL"
  exit 0
fi

# Wait up to 15 minutes (the first run downloads the model) for the app.
(
  for _ in $(seq 1 450); do
    if curl -sf "${APP_URL}/health" > /dev/null 2>&1; then
      open "$APP_URL"
      exit 0
    fi
    sleep 2
  done
) &
POLLER=$!
trap 'kill "$POLLER" 2> /dev/null' EXIT
trap 'exit 130' INT TERM

bash start.sh
