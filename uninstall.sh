#!/bin/bash
# Remove the launchd backend agent. Keeps your stored memories/venv/logs.
set -uo pipefail
LA="$HOME/Library/LaunchAgents"

launchctl unload "$LA/com.mem0mcp.server.plist" 2>/dev/null || true
rm -f "$LA/com.mem0mcp.server.plist"

# Legacy cleanup: remove the old menu-bar toggle agent + app if present
# (versions before the automatic stdio-proxy lifecycle).
launchctl unload "$LA/com.mem0mcp.toggle.plist" 2>/dev/null || true
rm -f "$LA/com.mem0mcp.toggle.plist"
rm -rf "$HOME/Applications/mem0 toggle.app"

# Safety net: only target the uniquely-named scripts (never a generic binary).
pkill -f mem0_mcp_server.py 2>/dev/null || true
pkill -f mem0_proxy.py 2>/dev/null || true

cat <<EOF
Uninstalled: launchd backend agent (com.mem0mcp.server) + any legacy menu-bar toggle.

Kept on purpose (delete manually if you want a full wipe):
  - venv:  <repo>/.venv
  - data:  ~/.mem0-mcp/chroma     (your stored memories)
  - logs:  ~/Library/Logs/mem0-mcp.log
  - the "local-mem0-mcp" entry in your MCP client config
EOF
