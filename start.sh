#!/usr/bin/env bash
# Start the mlx-audio TTS server (unless one is already running) and the
# Voxtral Reader web app, both on 127.0.0.1. Ctrl+C stops the app and the
# TTS server this script started.
set -euo pipefail

cd "$(dirname "$0")"

MODEL="${TTS_MODEL:-mlx-community/Voxtral-4B-TTS-2603-mlx-bf16}"
TTS_PORT="${TTS_PORT:-8000}"
APP_PORT="${APP_PORT:-7860}"
# Python: $PYTHON if set, otherwise the project's .venv (created by `uv sync`).
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON=".venv/bin/python"
  else
    echo ""
    echo "  ✗ Dependencies aren't installed yet (no .venv in $(pwd))."
    if command -v uv > /dev/null 2>&1; then
      echo "    Run this once, then start again:  uv sync"
    else
      echo "    Install uv (https://docs.astral.sh/uv/), then run once:  uv sync"
    fi
    echo "    Or point PYTHON at an environment that has them:  PYTHON=python ./start.sh"
    exit 1
  fi
fi
TTS_URL="http://127.0.0.1:${TTS_PORT}"
APP_URL="http://127.0.0.1:${APP_PORT}"
TTS_LOG="logs/tts.log"

echo ""
echo "  ▶ Voxtral Reader"
echo "  ─────────────────────────────────────────"

# Who is listening on a port, e.g. "Python (pid 123)"; empty if nobody.
port_owner() {
  lsof +c 0 -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null \
    | awk 'NR > 1 { print $1 " (pid " $2 ")" }' | sort -u | paste -sd, - || true
}

# ── App port ──────────────────────────────────────────────────────────────────
owner="$(port_owner "$APP_PORT")"
if [[ -n "$owner" ]]; then
  if curl -sf "${APP_URL}/health" 2>/dev/null | grep -q '"app"'; then
    echo "  ✓ Voxtral Reader is already running → ${APP_URL}"
    exit 0
  fi
  echo "  ✗ Port ${APP_PORT} is already in use by: ${owner}"
  echo "    Stop that program, or use another port:  APP_PORT=7861 ./start.sh"
  exit 1
fi

# ── TTS server ────────────────────────────────────────────────────────────────
TTS_PID=""
WARM_PID=""
cleanup() {
  if [[ -n "$WARM_PID" ]]; then kill "$WARM_PID" 2>/dev/null || true; fi
  if [[ -n "$TTS_PID" ]] && kill -0 "$TTS_PID" 2>/dev/null; then
    echo ""
    echo "  → Stopping the TTS server (pid ${TTS_PID})"
    kill "$TTS_PID" 2>/dev/null || true
    wait "$TTS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if curl -sf "${TTS_URL}/v1/models" > /dev/null 2>&1; then
  echo "  ✓ mlx-audio already running on :${TTS_PORT}"
else
  owner="$(port_owner "$TTS_PORT")"
  if [[ -n "$owner" ]]; then
    echo "  ✗ Port ${TTS_PORT} is in use by ${owner}, which isn't an mlx-audio server."
    echo "    Stop that program, or use another port:  TTS_PORT=8001 ./start.sh"
    exit 1
  fi

  mkdir -p logs
  echo "  → Starting mlx-audio on :${TTS_PORT}  (log: ${TTS_LOG})"
  "$PYTHON" -m mlx_audio.server --host 127.0.0.1 --port "$TTS_PORT" > "$TTS_LOG" 2>&1 &
  TTS_PID=$!

  echo -n "  Waiting for TTS server"
  ready=""
  for _ in $(seq 1 90); do
    if curl -sf "${TTS_URL}/v1/models" > /dev/null 2>&1; then ready=1; break; fi
    if ! kill -0 "$TTS_PID" 2>/dev/null; then break; fi
    sleep 2; echo -n "."
  done
  if [[ -z "$ready" ]]; then
    echo " failed."
    echo "  ✗ The TTS server didn't start. Last lines of ${TTS_LOG}:"
    tail -n 20 "$TTS_LOG" | sed 's/^/    /'
    exit 1
  fi
  echo " ready."

  # Load the model weights in the background so the UI comes up right away.
  # The first run downloads the model (~8 GB) from Hugging Face.
  echo "  → Loading ${MODEL} in the background (first run downloads ~8 GB)"
  (
    curl -sf -X POST "${TTS_URL}/v1/audio/speech" --max-time 1800 \
      -H "Content-Type: application/json" \
      -d "{\"model\":\"${MODEL}\",\"input\":\"Ready.\",\"voice\":\"casual_male\",\"response_format\":\"wav\"}" \
      -o /dev/null \
      && echo "  ✓ Model loaded" \
      || echo "  ! Model warm-up failed; see ${TTS_LOG}"
  ) &
  WARM_PID=$!
fi

# ── App server ────────────────────────────────────────────────────────────────
echo ""
echo "  → ${APP_URL}"
echo "  Shortcuts: Space=play/pause  ←/→=paragraph  ■=stop generation"
echo ""

export TTS_URL TTS_MODEL="$MODEL"
"$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$APP_PORT" --log-level warning
