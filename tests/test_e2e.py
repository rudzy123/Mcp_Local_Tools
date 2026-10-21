"""End-to-end checks for discovery, path confinement, limits, and pagination."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from agent.agent_client import read_all, run
from localtools.server import mcp

REPO = Path(__file__).resolve().parents[1]
ATTACKS = [
    "../notes_secret/creds.md",
    "/etc/passwd",
    "link.md",
    "dirlink/creds.md",
    ".env",
    "pipe.md",
    "a.md\x00.txt",
    "sub/../../notes_secret/creds.md",
    "missing.md",
]


def _build_tree(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "notes"
    (root / "sub").mkdir(parents=True)
    secret = tmp_path / "notes_secret"
    secret.mkdir()
    (secret / "creds.md").write_text("TOP SECRET\n", encoding="utf-8")
    body = "héllo " * 20_000
    (root / "a.md").write_text(body, encoding="utf-8")
    (root / "sub" / "b.md").write_text("nested", encoding="utf-8")
    (root / ".env").write_text("API_KEY=x\n", encoding="utf-8")
    (root / "link.md").symlink_to(secret / "creds.md")
    (root / "dirlink").symlink_to(secret)
    os.mkfifo(root / "pipe.md")
    return root, body


async def _exercise(client: Client, body: str) -> None:
    tools = await client.list_tools()
    found = {tool.name: tool.annotations.read_only_hint for tool in tools.tools}
    assert found["list_documents"] is True
    assert found["read_document"] is True

    listed = await client.call_tool("list_documents", {})
    assert listed.is_error is False
    documents = listed.structured_content["documents"]
    assert [item["path"] for item in documents] == ["a.md", "sub/b.md"]
    assert listed.structured_content["next_cursor"] is None

    window = await client.call_tool("read_document", {"path": "a.md", "max_bytes": 1001})
    assert window.is_error is False
    chunk = window.structured_content
    assert chunk["offset"] == 0
    assert chunk["next_offset"] == 1001
    assert chunk["content"].endswith("lo ")
    assert "é" in chunk["content"] or chunk["next_offset"] < 1001

    pages: list[str] = []
    offset: int | None = 0
    while offset is not None:
        result = await client.call_tool(
            "read_document",
            {"path": "a.md", "offset": offset, "max_bytes": 4093},
        )
        assert result.is_error is False
        page = result.structured_content
        pages.append(page["content"])
        offset = page["next_offset"]
    assert "".join(pages) == body

    for attack in ATTACKS:
        result = await client.call_tool("read_document", {"path": attack})
        assert result.is_error is True, attack

    oversized = await client.call_tool("read_document", {"path": "a.md", "max_bytes": 10**9})
    assert oversized.is_error is True

    async def read_nested() -> None:
        result = await client.call_tool("read_document", {"path": "sub/b.md"})
        assert result.is_error is False
        assert result.structured_content["content"] == "nested"

    async with anyio.create_task_group() as group:
        for _ in range(50):
            group.start_soon(read_nested)


@pytest.fixture()
def notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    root, body = _build_tree(tmp_path)
    monkeypatch.setenv("LOCALTOOLS_ROOT", str(root))
    return root, body


@pytest.mark.anyio
async def test_in_memory_server(notes: tuple[Path, str]) -> None:
    _, body = notes
    async with Client(mcp) as client:
        await _exercise(client, body)


@pytest.mark.anyio
async def test_stdio_server(notes: tuple[Path, str]) -> None:
    root, body = notes
    env = {
        **os.environ,
        "LOCALTOOLS_ROOT": str(root),
        "PYTHONPATH": str(REPO),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "localtools.server"],
        env=env,
    )
    async with Client(params) as client:
        await _exercise(client, body)


@pytest.mark.anyio
async def test_agent_paginates_over_stdio(notes: tuple[Path, str]) -> None:
    root, body = notes
    assert await run(root) == body


@pytest.mark.anyio
async def test_read_all_stops_on_error(notes: tuple[Path, str]) -> None:
    async with Client(mcp) as client:
        with pytest.raises(RuntimeError, match="invalid_path"):
            await read_all(client, "../notes_secret/creds.md")
