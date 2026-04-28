# MCP Local Tools — MCP Server + Agent Integration

This project demonstrates a **production‑minded implementation of an MCP (Model Context Protocol) server** that exposes secure local tools, paired with an **agent client** that orchestrates multi‑step workflows against those tools.

The focus is **tool abstraction, security boundaries, and agent integration** — not model hype or brittle protocol hacks.

---

## What This Project Does

- Runs an MCP‑compliant server over stdio
- Exposes **read‑only local filesystem tools**
- Enforces strict security boundaries
- Implements an agent client that:
  - discovers available tools
  - selects actions deliberately
  - executes a multi‑step workflow
  - logs every step

The system runs **fully locally** and requires **no API keys**.

---

## Why This Project Matters

MCP is increasingly treated as a **standard interface layer for AI‑tool connectivity**. Rather than writing custom glue code for each agent or model, MCP defines a shared way for agents to interact with tools.

This project signals that you:

- Understand emerging AI infrastructure standards
- Can expose tools safely (not just “call functions”)
- Think about security, boundaries, and auditability
- Can integrate agents without over‑engineering or hype

---

## Exposed Capabilities

The MCP server exposes the following tools:

- **`list_documents`** — lists available local notes
- **`read_document(filename)`** — reads the contents of a specific document

Access is strictly limited to:
data/notes/

### Security Guarantees

- ✅ Read‑only access
- ✅ Single allowed directory
- ✅ No path traversal
- ✅ No shell execution
- ✅ No network access
- ✅ No credential exposure

Security constraints are enforced at the tool layer.

---

## Architecture Overview

Local Filesystem (data/notes)
|
v
MCP Server (stdio)

list_documents
read_document
|
v
Agent Client
discovers tools
selects actions
executes workflow
logs results


---

## Demonstrated Agent Workflow

1. Agent calls `list_documents`
2. Agent selects a document
3. Agent calls `read_document`
4. Agent outputs the document contents

All actions are logged explicitly for transparency and debugging.

---

## MCP Transport Note (Important)

The MCP server runs using the official stdio runtime.

The agent logic is intentionally **decoupled from MCP wire framing** to avoid re‑implementing unstable protocol internals. Tool logic and agent orchestration are **transport‑agnostic** and can adopt full MCP framing as the Python SDK stabilizes.

This reflects real‑world engineering judgment when working with evolving standards.

---

## How to Run

Start the MCP server:
bash
python -m server.mcp_server

# How to Run the agent workflow:
python -m agent.agent_client

Future Extensions
   * Document search capability
   * Policy‑aware tool filtering
   * Full MCP wire‑protocol client as SDK stabilizes
   * UI visualization of agent actions