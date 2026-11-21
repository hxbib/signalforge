#!/usr/bin/env bash
# Start (or restart) the integration server on :8000. Lead-only.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
if [ -f data/server.pid ] && kill -0 "$(cat data/server.pid)" 2>/dev/null; then
  kill "$(cat data/server.pid)" || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$(cat data/server.pid)" 2>/dev/null || break; sleep 0.3; done
fi
SF_STORE_INTERACTIONS="${SF_STORE_INTERACTIONS:-delete}" SF_DB="${SF_DB:-data/main.sqlite}" setsid nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*' --log-level info > data/server.log 2>&1 &
echo $! > data/server.pid
for _ in $(seq 1 40); do curl -fsS -m 2 "http://127.0.0.1:${PORT:-8000}/api/health" >/dev/null 2>&1 && break; sleep 0.25; done
curl -fsS -m 3 "http://127.0.0.1:${PORT:-8000}/api/health"; echo
