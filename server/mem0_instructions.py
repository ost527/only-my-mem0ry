"""Server-level MCP instructions: the behavioral contract for connected agents.

Sent to every client in the MCP initialize response; most clients inject it into
the agent's system prompt, making it the strongest in-protocol lever for getting
agents to recall/save *proactively* instead of waiting to be told. Shared by both
the backend and the per-client stdio proxy, because a FastMCP proxy answers
initialize itself and does NOT mirror the backend's instructions. Keep the text
short: it is paid for in context tokens in every session of every client.
"""

INSTRUCTIONS = """\
This server is the user's persistent long-term memory, shared by ALL of their \
LLM clients (IDE agents, CLIs, chat apps). Use it proactively so the user never \
has to repeat or re-explain anything:
- START of any task: call search_memories with the task's key terms to recall \
prior decisions, configs, paths, and preferences.
- BEFORE asking the user a question: call search_memories first; the answer is \
often already stored.
- The MOMENT you learn a durable fact (decision, preference, config value, \
path/identifier, environment quirk, recurring command): call add_memory -- one \
atomic, self-contained fact per call.
- Reconcile instead of duplicating: update_memory refines/merges an existing \
memory; delete_memory removes one that became wrong or obsolete.
- Never store secrets (passwords, API keys, tokens).
"""
