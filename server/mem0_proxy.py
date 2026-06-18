#!/usr/bin/env python3
"""
Per-client stdio proxy for the shared mem0 HTTP backend (Option 3 lifecycle).

Register THIS script (stdio) in your MCP client config instead of the HTTP URL.
The client then owns the proxy's lifetime: it spawns this proxy on launch and
kills it on close. The proxy:
  * auto-starts the shared HTTP backend via `launchctl kickstart` (if not up),
  * forwards all MCP calls to that single backend (one Chroma writer, safe for
    multiple concurrent clients),
  * keeps the backend warm with a periodic ping while this client is connected.
When the last client closes, no proxy remains to ping, so the backend
idle-exits (MEM0_IDLE_TIMEOUT) and frees its RAM. The next client launch starts
it again. No manual menu-bar toggle required.

Env vars (all optional):
  MEM0_MCP_HOST            backend host (default: 127.0.0.1)
  MEM0_MCP_PORT            backend port (default: 8765)
  MEM0_SERVER_LABEL        launchd label to kickstart (default: com.mem0mcp.server)
  MEM0_IDLE_TIMEOUT        backend idle timeout in s; sizes the keepalive (default: 600)
  MEM0_PROXY_KEEPALIVE     override keepalive interval in s (default: clamp(IDLE/3, 5, 120))
  MEM0_BACKEND_READY_TIMEOUT  seconds to wait for the backend to listen (default: 40)
"""
import os
import sys
import time
import socket
import asyncio
import subprocess

# Same dir as this script (sys.path[0]); the proxy must declare the instructions
# itself because a FastMCP proxy answers initialize without mirroring the backend's.
from mem0_instructions import INSTRUCTIONS

HOST = os.environ.get("MEM0_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MEM0_MCP_PORT", "8765"))
URL = f"http://{HOST}:{PORT}/mcp"
LABEL = os.environ.get("MEM0_SERVER_LABEL", "com.mem0mcp.server")
IDLE_TIMEOUT = float(os.environ.get("MEM0_IDLE_TIMEOUT", "600"))
KEEPALIVE = float(os.environ.get("MEM0_PROXY_KEEPALIVE", str(max(5.0, min(IDLE_TIMEOUT / 3, 120.0)))))
READY_TIMEOUT = float(os.environ.get("MEM0_BACKEND_READY_TIMEOUT", "40"))


def _log(msg: str) -> None:
    # stderr only -- stdout is the MCP stdio channel.
    print(f"[mem0-proxy] {msg}", file=sys.stderr, flush=True)


def _listening() -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _kickstart() -> None:
    try:
        subprocess.run(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, timeout=10,
        )
    except Exception as e:
        _log(f"kickstart failed: {e}")


async def _wait_ready(timeout: float) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if _listening():
            return True
        await asyncio.sleep(0.5)
    return _listening()


async def _keepalive() -> None:
    from fastmcp import Client
    while True:
        await asyncio.sleep(KEEPALIVE)
        try:
            async with Client(URL) as c:
                # tools/list goes through server middleware -> refreshes the
                # backend's idle timer (a bare MCP ping may bypass middleware).
                await c.list_tools()
        except Exception as e:
            _log(f"keepalive failed ({e}); re-kickstarting backend")
            _kickstart()


async def main() -> None:
    from fastmcp.server import create_proxy
    from fastmcp.server.middleware import Middleware

    class _EnsureBackend(Middleware):
        """Before forwarding any request, make sure the backend is up; if it died
        (idle-exit / crash / manual kill), kickstart it and wait. This makes the
        proxy self-healing -- a client call never fails just because the backend
        happened to be asleep."""
        async def __call__(self, context, call_next):
            if not _listening():
                _log("backend down; kickstarting before forwarding")
                _kickstart()
                await _wait_ready(READY_TIMEOUT)
            return await call_next(context)

    if not _listening():
        _log(f"backend not up; kickstarting {LABEL}")
        _kickstart()
    if not await _wait_ready(READY_TIMEOUT):
        _log(f"WARNING: backend {URL} not ready after {READY_TIMEOUT}s; serving anyway")

    proxy = create_proxy(URL, name="only-my-mem0ry", instructions=INSTRUCTIONS)
    proxy.add_middleware(_EnsureBackend())
    ka = asyncio.create_task(_keepalive())
    try:
        await proxy.run_async(transport="stdio")
    finally:
        ka.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
