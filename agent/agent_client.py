"""Discover the local-tools server over MCP and page through one document."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

REQUIRED_TOOLS = frozenset({"list_documents", "read_document"})


def project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "localtools" / "server.py").is_file():
            return candidate
    return Path.cwd()


def _server_env(root: Path) -> dict[str, str]:
    repo = project_root()
    pythonpath = os.pathsep.join(
        part for part in (str(repo), os.environ.get("PYTHONPATH", "")) if part
    )
    return {**os.environ, "LOCALTOOLS_ROOT": str(root), "PYTHONPATH": pythonpath}


async def _call(client: Client, name: str, arguments: dict[str, object]) -> dict:
    result = await client.call_tool(name, arguments)
    if result.is_error:
        detail = result.content[0].text if result.content else "tool call failed"
        raise RuntimeError(detail)
    if not isinstance(result.structured_content, dict):
        raise RuntimeError(f"{name} returned no structured content")
    return result.structured_content


async def list_all(client: Client) -> list[dict]:
    documents: list[dict] = []
    cursor: str | None = None
    while True:
        arguments: dict[str, object] = {} if cursor is None else {"cursor": cursor}
        page = await _call(client, "list_documents", arguments)
        documents.extend(page["documents"])
        cursor = page["next_cursor"]
        if cursor is None:
            return documents


async def read_all(client: Client, path: str) -> str:
    parts: list[str] = []
    offset = 0
    etag: str | None = None
    while True:
        chunk = await _call(
            client,
            "read_document",
            {"path": path, "offset": offset},
        )
        if etag is None:
            etag = chunk["etag"]
        elif chunk["etag"] != etag:
            raise RuntimeError("document changed while reading; retry")
        parts.append(chunk["content"])
        nxt = chunk["next_offset"]
        if nxt is None:
            return "".join(parts)
        offset = nxt


async def run(root: Path | None = None) -> str:
    """Talk to a fresh server over stdio. Returns the document it selected."""
    notes = (root or Path(os.environ.get("LOCALTOOLS_ROOT", project_root() / "data" / "notes"))).resolve()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "localtools.server"],
        env=_server_env(notes),
    )
    print("Agent starting MCP workflow")
    async with Client(params) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        missing = REQUIRED_TOOLS - names
        if missing:
            raise RuntimeError(f"server is missing tools: {sorted(missing)}")
        print(f"Discovered tools: {sorted(names)}")

        documents = await list_all(client)
        print(f"list_documents -> {[item['path'] for item in documents]}")
        if not documents:
            print("No documents found")
            return ""

        selected = documents[0]["path"]
        print(f"Selected document: {selected}")
        content = await read_all(client, selected)
        print(content)
        print("Agent workflow complete")
        return content


def main() -> None:
    try:
        asyncio.run(run())
    except RuntimeError as exc:
        print(f"Agent failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
