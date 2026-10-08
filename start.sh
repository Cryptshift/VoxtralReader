#!/usr/bin/env bash
set -e

MODEL="mlx-community/Voxtral-4B-TTS-2603-mlx-bf16"
TTS_PORT=8000
APP_PORT=7860
# Number of parallel HTTP workers — each can handle a TTS request simultaneously.
# On M4 Max: 4 lets the 5-chunk prefetch window fill faster.
# Lower to 2 if you see memory pressure (model is ~8 GB).
WORKERS=8

echo ""
echo "  ▶ Voxtral Reader"
echo "  ─────────────────────────────────────────"

# Kill any leftover app process
lsof -ti:${APP_PORT} | xargs kill -9 2>/dev/null || true

# ── TTS server ────────────────────────────────────────────────────────────────
if curl -sf http://localhost:${TTS_PORT}/v1/models > /dev/null 2>&1; then
  echo "  ✓ mlx-audio already running on :${TTS_PORT}"
else
  lsof -ti:${TTS_PORT} | xargs kill -9 2>/dev/null || true
  echo "  → Starting mlx-audio  (${WORKERS} workers)"
  echo "    ${MODEL}"
  echo "    First run downloads ~8 GB — grab a coffee"
  echo ""

  python -m mlx_audio.server \
    --host 127.0.0.1 \
    --port ${TTS_PORT} \
    --workers ${WORKERS} &

  echo -n "  Waiting for TTS server"
  for i in $(seq 1 90); do
    curl -sf http://localhost:${TTS_PORT}/v1/models > /dev/null 2>&1 && { echo " ready."; break; }
    sleep 2; echo -n "."
    [[ $i -eq 90 ]] && echo " timed out."
  done

  # Warm up: load model weights into Metal GPU memory so first user request is instant
  echo -n "  Warming up model"
  curl -s -X POST http://localhost:${TTS_PORT}/v1/audio/speech \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"input\":\"Ready.\",\"voice\":\"casual_male\",\"response_format\":\"wav\"}" \
    -o /dev/null && echo " done." || echo " (warmup failed — continuing anyway)"
fi

# ── App server ────────────────────────────────────────────────────────────────
echo ""
echo "  → http://localhost:${APP_PORT}"
echo "  Shortcuts: Space=play/pause  ←/→=paragraph  ■=stop generation"
echo ""

cd "$(dirname "$0")"
uvicorn app:app --host 127.0.0.1 --port ${APP_PORT} --reload
