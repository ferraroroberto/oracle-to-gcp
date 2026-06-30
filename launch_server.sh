#!/bin/bash
# Launch Streamlit + expose it via Cloudflare Tunnel.
# Share the printed https:// URL with anyone.
#
# Prerequisites:
#   sudo apt install cloudflared      # Debian/Ubuntu
#   brew install cloudflared          # macOS

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8501

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERROR] Virtual environment not found."
    echo "  python -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

if ! command -v cloudflared &> /dev/null; then
    echo "[ERROR] cloudflared is not installed."
    echo "  Debian/Ubuntu : sudo apt install cloudflared"
    echo "  macOS         : brew install cloudflared"
    exit 1
fi

echo "[1/2] Starting Streamlit on port $PORT ..."
.venv/bin/python -m streamlit run app/app.py \
    --server.port "$PORT" \
    --server.enableXsrfProtection false \
    --server.enableCORS false \
    --server.headless true &
STREAMLIT_PID=$!

sleep 3

if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
    echo "[ERROR] Streamlit failed to start."
    exit 1
fi

echo "[2/2] Opening Cloudflare Tunnel ..."
echo ""
echo "  Share the https:// URL printed below with anyone."
echo "  Press Ctrl+C to stop both the tunnel and Streamlit."
echo ""
cloudflared tunnel --url "http://localhost:$PORT" 2>&1 | awk '!/Cannot determine default origin certificate path/'

kill "$STREAMLIT_PID" 2>/dev/null
wait "$STREAMLIT_PID" 2>/dev/null
echo ""
echo "Server stopped."
