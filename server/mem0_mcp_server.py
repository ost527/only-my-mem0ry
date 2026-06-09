#!/usr/bin/env python3
"""
Local Mem0 MCP server (Chroma vector store + OpenAI-compatible LLM).

All settings come from environment variables so the same script works on any
machine. Defaults target a local LM Studio endpoint.

Env vars (all optional):
  MEM0_LLM_MODEL        LLM model id for fact extraction (default: qwen2.5-14b-instruct)
                        IMPORTANT: use a NON-reasoning instruct model. Reasoning
                        models (qwen3/QwQ/R1 ...) put output in a separate channel
                        and break extraction.
  MEM0_LLM_BASE_URL     OpenAI-compatible base URL (default: http://localhost:1234/v1)
  MEM0_LLM_API_KEY      API key (default: lm-studio ; any non-empty string for LM Studio)
  MEM0_LLM_TEMPERATURE  default: 0.1
  MEM0_EMBEDDER_MODEL   HF sentence-transformers model (default: sentence-transformers/all-MiniLM-L6-v2)
  MEM0_EMBEDDER_DIMS    embedding dims (default: 384)
  MEM0_CHROMA_PATH      Chroma persist dir (default: ~/.mem0-mcp/chroma)
  MEM0_COLLECTION       collection name (default: mem0)
  MEM0_DEFAULT_USER     default user_id (default: developer_workspace)
  MEM0_MCP_TRANSPORT    'stdio' (default) or 'http'
  MEM0_MCP_HOST         http host (default: 127.0.0.1)
  MEM0_MCP_PORT         http port (default: 8765)
  MEM0_DISABLE_JSON_RESPONSE_FORMAT
                        '1' (default) forces response_format=None on the LLM call,
                        a workaround for endpoints (e.g. LM Studio) that reject
                        {"type":"json_object"} with HTTP 400. Set '0' if your
                        endpoint supports json_object.
"""
import os
from fastmcp import FastMCP
from mem0 import Memory


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


CHROMA_PATH = _expand(os.environ.get("MEM0_CHROMA_PATH", "~/.mem0-mcp/chroma"))
os.makedirs(CHROMA_PATH, exist_ok=True)

config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": os.environ.get("MEM0_LLM_MODEL", "qwen2.5-14b-instruct"),
            "openai_base_url": os.environ.get("MEM0_LLM_BASE_URL", "http://localhost:1234/v1"),
            "api_key": os.environ.get("MEM0_LLM_API_KEY", "lm-studio"),
            "temperature": float(os.environ.get("MEM0_LLM_TEMPERATURE", "0.1")),
        },
    },
    "embedder": {
        "provider": "huggingface",
        "config": {
            "model": os.environ.get("MEM0_EMBEDDER_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            "embedding_dims": int(os.environ.get("MEM0_EMBEDDER_DIMS", "384")),
        },
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": os.environ.get("MEM0_COLLECTION", "mem0"),
            "path": CHROMA_PATH,
        },
    },
}

m = Memory.from_config(config)

# Workaround: some OpenAI-compatible servers (LM Studio) reject
# response_format={"type":"json_object"} with HTTP 400. Force it to None so the
# model returns plain text JSON (works with instruct models that follow the prompt).
if os.environ.get("MEM0_DISABLE_JSON_RESPONSE_FORMAT", "1") == "1":
    _orig = m.llm.generate_response

    def _patched(messages, response_format=None, tools=None, tool_choice=None):
        return _orig(messages, response_format=None, tools=tools, tool_choice=tool_choice)

    m.llm.generate_response = _patched

DEFAULT_USER = os.environ.get("MEM0_DEFAULT_USER", "developer_workspace")

mcp = FastMCP("Local-Mem0-MCP")


@mcp.tool()
def add_memory(text: str, user_id: str = "") -> str:
    """Add a memory (dev know-how, fixes, rules, facts) to the local vector store.
    If user_id is omitted, the default user is used."""
    try:
        m.add(text, user_id=(user_id or DEFAULT_USER))
        return f"✅ Saved to local Mem0: '{text}'"
    except Exception as e:
        return f"❌ Save failed: {e}"


@mcp.tool()
def search_memories(query: str, user_id: str = "") -> str:
    """Search past memories relevant to a query/keyword."""
    try:
        resp = m.search(query, filters={"user_id": (user_id or DEFAULT_USER)})
        results = resp.get("results", []) if isinstance(resp, dict) else resp
        if not results:
            return "🔍 No results."
        out = f"🔍 Results for '{query}':\n\n"
        for i, r in enumerate(results, 1):
            out += f"{i}. {r.get('memory', '(empty)')}\n"
        return out
    except Exception as e:
        return f"❌ Search failed: {e}"


@mcp.tool()
def list_memories(user_id: str = "") -> str:
    """List all stored memories for the (default) user."""
    try:
        resp = m.get_all(filters={"user_id": (user_id or DEFAULT_USER)})
        results = resp.get("results", []) if isinstance(resp, dict) else resp
        if not results:
            return "📋 No memories stored."
        out = f"📋 Memories (total {len(results)}):\n\n"
        for i, r in enumerate(results, 1):
            out += f"{i}. [ID: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}\n"
        return out
    except Exception as e:
        return f"❌ List failed: {e}"


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete a memory by its ID."""
    try:
        m.delete(memory_id)
        return f"✅ Deleted memory '{memory_id}'."
    except Exception as e:
        return f"❌ Delete failed: {e}"


if __name__ == "__main__":
    transport = os.environ.get("MEM0_MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(
            transport="http",
            host=os.environ.get("MEM0_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MEM0_MCP_PORT", "8765")),
        )
    else:
        mcp.run()
