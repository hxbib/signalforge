#!/usr/bin/env bash
# Public HTTPS tunnel in front of the integration server on :8000 (Cloudflare quick tunnel). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=private/bin/cloudflared
mkdir -p private/bin data
[ -x "$BIN" ] || { curl -fsSL -o "$BIN" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64; chmod +x "$BIN"; }
if ! { [ -f data/tunnel.pid ] && kill -0 "$(cat data/tunnel.pid)" 2>/dev/null; }; then
  setsid nohup "$BIN" tunnel --url http://127.0.0.1:8000 --no-autoupdate > data/tunnel.log 2>&1 &
  echo $! > data/tunnel.pid
  for _ in $(seq 1 30); do grep -Eq 'https://[a-z0-9-]+\.trycloudflare\.com' data/tunnel.log 2>/dev/null && break; sleep 1; done
fi
grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' data/tunnel.log | head -1
