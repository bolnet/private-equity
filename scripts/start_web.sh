#!/bin/bash
# scripts/start_web.sh
# Finance MCP — claude.ai web connection launcher
#
# Prerequisites:
#   - ngrok installed (https://ngrok.com/download or: brew install ngrok)
#   - .venv virtual environment with finance-mcp installed
#
# Usage: bash scripts/start_web.sh [port]
# Default port: 8000

set -e
cd "$(dirname "$0")/.."

PORT=${1:-8000}

echo "============================================"
echo "  Finance MCP — claude.ai Web Launcher"
echo "============================================"
echo ""

# Check prerequisites
if ! command -v ngrok &>/dev/null; then
    echo "ERROR: ngrok not found. Install it first:"
    echo "  macOS:  brew install ngrok"
    echo "  Other:  https://ngrok.com/download"
    exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

# Start Finance MCP HTTP server in background
echo "Starting Finance MCP HTTP server on port $PORT..."
.venv/bin/python -m finance_mcp.server_http "$PORT" &
SERVER_PID=$!
echo "Server started (PID $SERVER_PID)"
echo ""

# Give server a moment to bind
sleep 1

# Start ngrok tunnel in background
echo "Starting ngrok tunnel..."
ngrok http "$PORT" --log stdout > /tmp/ngrok_finance.log 2>&1 &
NGROK_PID=$!

# Wait for ngrok to establish tunnel
sleep 3

# Retrieve public URL from ngrok API
PUBLIC_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])" 2>/dev/null \
    || echo "")

echo ""
echo "============================================"
if [ -n "$PUBLIC_URL" ]; then
    echo "  Connect claude.ai to:"
    echo "  ${PUBLIC_URL}/mcp"
    echo ""
    echo "  Steps:"
    echo "  1. Go to claude.ai"
    echo "  2. Click Settings (top-right) > Connectors"
    echo "  3. Click 'Add custom connector'"
    echo "  4. Paste: ${PUBLIC_URL}/mcp"
    echo "  5. Click Add"
    echo "  6. In a new chat, click '+' > Connectors > toggle Finance MCP on"
else
    echo "  ngrok tunnel starting... check http://localhost:4040"
    echo "  Your URL will appear there. Append /mcp when connecting claude.ai."
fi
echo "============================================"
echo ""
echo "NOTE: ngrok free tier URL changes on each restart."
echo "For a persistent URL, use Cloudflare Tunnel:"
echo "  cloudflared tunnel --url http://localhost:$PORT"
echo ""
echo "Press Ctrl+C to stop both server and tunnel."

# Wait for server process (keeps script alive)
wait $SERVER_PID
