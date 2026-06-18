#!/bin/bash
# only-my-mem0ry installer (macOS).
# Sets up: a python venv + deps, and an ON-DEMAND launchd backend agent.
#
# Lifecycle: your MCP client launches a lightweight stdio proxy
# (server/mem0_proxy.py) which auto-starts this shared backend and keeps it warm
# while the client is open; the backend idle-exits when the last client closes.
# No menu-bar app, no manual toggle.
#
# Override defaults via env, e.g.:  MEM0_MCP_PORT=8800 MEM0_IDLE_TIMEOUT=900 ./install.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"

PORT="${MEM0_MCP_PORT:-8765}"
IDLE="${MEM0_IDLE_TIMEOUT:-600}"
EMBEDDER_MODEL="${MEM0_EMBEDDER_MODEL:-intfloat/multilingual-e5-small}"
EMBEDDER_DIMS="${MEM0_EMBEDDER_DIMS:-384}"
LA="$HOME/Library/LaunchAgents"
SERVER_LABEL="com.only-my-mem0ry.server"

echo "==> only-my-mem0ry installer"
echo "    repo:        $REPO"
echo "    mode:        direct store (no LLM); agent-driven memory"
echo "    port:        $PORT"
echo "    idle-exit:   ${IDLE}s"
echo "    embedder:    $EMBEDDER_MODEL (${EMBEDDER_DIMS} dims)"

# ---- prerequisites ----
command -v python3 >/dev/null || { echo "ERROR: python3 not found (need Python 3.10+)"; exit 1; }

mkdir -p "$HOME/Library/Logs" "$LA"

# ---- python venv + deps ----
echo "==> creating venv + installing deps (downloads torch etc; may take several minutes)..."
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/python3" -m pip install -q -U pip
"$REPO/.venv/bin/python3" -m pip install -q -r "$REPO/requirements.txt"
PYTHON="$REPO/.venv/bin/python3"
PYBIN_DIR="$REPO/.venv/bin"

# ---- render + load the on-demand backend launchd agent ----
echo "==> installing on-demand launchd backend agent..."
sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__PYBIN_DIR__|$PYBIN_DIR|g" \
    -e "s|__SERVER_SCRIPT__|$REPO/server/mem0_mcp_server.py|g" \
    -e "s|__PORT__|$PORT|g" \
    -e "s|__IDLE__|$IDLE|g" \
    -e "s|__EMBEDDER_MODEL__|$EMBEDDER_MODEL|g" \
    -e "s|__EMBEDDER_DIMS__|$EMBEDDER_DIMS|g" \
    -e "s|__HOME__|$HOME|g" \
    "$REPO/launchd/$SERVER_LABEL.plist.template" > "$LA/$SERVER_LABEL.plist"

launchctl unload "$LA/$SERVER_LABEL.plist" 2>/dev/null || true
launchctl load -w "$LA/$SERVER_LABEL.plist"   # registers it; RunAtLoad=false so it stays OFF until a client needs it

cat <<EOF

==> Done. The backend is registered as an on-demand launchd agent (starts OFF).

Add this MCP server to your client config
(e.g. ~/.kiro/settings/mcp.json, Claude Desktop, Cursor, ...):

  "only-my-mem0ry": {
    "command": "$PYTHON",
    "args": ["$REPO/server/mem0_proxy.py"]
  }

How it runs: launching your IDE/CLI spawns the proxy, which auto-starts the
shared backend; closing the last client lets the backend idle-exit after ${IDLE}s
and free its RAM. Multiple clients safely share the one backend. No toggle.

Memories are embedded locally and stored verbatim — no LLM, no API key.

Recommended: make agents recall/save WITHOUT being told. Server instructions
and tool descriptions already push for this, but for maximum reliability paste
the rules snippet from README.md ("Getting agents to use memory proactively")
into your agent's always-on rules file (AGENTS.md / CLAUDE.md / .cursorrules).
EOF
