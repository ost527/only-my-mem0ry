#!/bin/bash
# Remove launchd agents + menu bar app. Keeps your stored memories/venv/logs.
set -uo pipefail
LA="$HOME/Library/LaunchAgents"

for L in com.mem0mcp.server com.mem0mcp.toggle; do
  launchctl unload "$LA/$L.plist" 2>/dev/null || true
  rm -f "$LA/$L.plist"
done

# launchctl unload above terminates the managed processes. As a safety net,
# only target the uniquely-named server script (never a generic binary name).
pkill -f mem0_mcp_server.py 2>/dev/null || true
rm -rf "$HOME/Applications/mem0 toggle.app"

cat <<EOF
Uninstalled: launchd agents (com.mem0mcp.server, com.mem0mcp.toggle) + menu bar app.

Kept on purpose (delete manually if you want a full wipe):
  - venv:  <repo>/.venv
  - data:  ~/.mem0-mcp/chroma     (your stored memories)
  - logs:  ~/Library/Logs/mem0-mcp.log , ~/Library/Logs/mem0-toggle.log
  - the "local-mem0-mcp" entry in your MCP client config
EOF
