#!/bin/bash
# mem0-mcp-toggle installer (macOS).
# Sets up: python venv + deps, menu bar app, launchd agents (server + toggle).
# Override defaults via env, e.g.:  MEM0_LLM_MODEL=qwen2.5-7b-instruct ./install.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"

PORT="${MEM0_MCP_PORT:-8765}"
MODEL="${MEM0_LLM_MODEL:-qwen2.5-14b-instruct}"
BASE_URL="${MEM0_LLM_BASE_URL:-http://localhost:1234/v1}"
APP_PATH="$HOME/Applications/mem0 toggle.app"
LA="$HOME/Library/LaunchAgents"
SERVER_LABEL="com.mem0mcp.server"
TOGGLE_LABEL="com.mem0mcp.toggle"

echo "==> mem0-mcp-toggle installer"
echo "    repo:    $REPO"
echo "    model:   $MODEL"
echo "    LLM url: $BASE_URL"
echo "    port:    $PORT"

# ---- prerequisites ----
command -v python3 >/dev/null || { echo "ERROR: python3 not found (need Python 3.10+)"; exit 1; }
command -v swiftc  >/dev/null || { echo "ERROR: swiftc not found. Run: xcode-select --install"; exit 1; }

mkdir -p "$HOME/Library/Logs" "$LA"

# ---- python venv + deps ----
echo "==> creating venv + installing deps (downloads torch etc; may take several minutes)..."
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/python3" -m pip install -q -U pip
"$REPO/.venv/bin/python3" -m pip install -q -r "$REPO/requirements.txt"
PYTHON="$REPO/.venv/bin/python3"
PYBIN_DIR="$REPO/.venv/bin"

# ---- build menu bar app ----
echo "==> building menu bar app..."
bash "$REPO/app/build.sh" "$APP_PATH"
APP_BIN="$APP_PATH/Contents/MacOS/MemoToggle"

# ---- render launchd plists from templates ----
render() {  # $1 template  $2 dest
  sed -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__PYBIN_DIR__|$PYBIN_DIR|g" \
      -e "s|__SERVER_SCRIPT__|$REPO/server/mem0_mcp_server.py|g" \
      -e "s|__APP_BIN__|$APP_BIN|g" \
      -e "s|__PORT__|$PORT|g" \
      -e "s|__MODEL__|$MODEL|g" \
      -e "s|__BASE_URL__|$BASE_URL|g" \
      -e "s|__HOME__|$HOME|g" \
      "$1" > "$2"
}
render "$REPO/launchd/$SERVER_LABEL.plist.template" "$LA/$SERVER_LABEL.plist"
render "$REPO/launchd/$TOGGLE_LABEL.plist.template" "$LA/$TOGGLE_LABEL.plist"

# ---- (re)load launchd agents ----
echo "==> loading launchd agents..."
launchctl unload "$LA/$SERVER_LABEL.plist" 2>/dev/null || true
launchctl unload "$LA/$TOGGLE_LABEL.plist" 2>/dev/null || true
launchctl load -w "$LA/$SERVER_LABEL.plist"
launchctl load -w "$LA/$TOGGLE_LABEL.plist"

cat <<EOF

==> Done. A 'memorychip' icon should appear in your menu bar (server starts OFF).

1) Make sure an OpenAI-compatible LLM is available at:
     $BASE_URL   (model: $MODEL)
   For LM Studio: load a NON-reasoning instruct model and start its local server.

2) Add this MCP server to your client config
   (e.g. ~/.kiro/settings/mcp.json, Claude Desktop, Cursor, ...):

  "local-mem0-mcp": {
    "url": "http://127.0.0.1:$PORT/mcp",
    "type": "http",
    "timeout": 300000
  }

3) Click the menu bar switch to turn the server ON, then use mem0 in your client.

CLI control is also available — see README (launchctl kickstart/kill).
EOF
