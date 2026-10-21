"""MCP server exposing read-only documents under a single directory root."""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, TypeVar

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from localtools import __version__
from localtools.sandbox import Policy, Root, SandboxError

# stdout is the JSON-RPC channel. Anything else written there corrupts the stream.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
log = logging.getLogger("localtools")

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)

T = TypeVar("T")


@dataclass
class State:
    root: Root
    io: anyio.CapacityLimiter


def _root_from_env() -> Root:
    raw = os.environ.get("LOCALTOOLS_ROOT", "data/notes")
    try:
        return Root(raw, Policy())
    except OSError as exc:
        raise RuntimeError(f"cannot open LOCALTOOLS_ROOT ({raw}): {exc.strerror}") from None


@asynccontextmanager
async def lifespan(_: MCPServer) -> AsyncIterator[State]:
    root = _root_from_env()
    try:
        yield State(root=root, io=anyio.CapacityLimiter(8))
    finally:
        root.close()


mcp = MCPServer("local-tools", version=__version__, lifespan=lifespan)


class DocumentInfo(BaseModel):
    path: str
    size: int


class DocumentList(BaseModel):
    documents: list[DocumentInfo]
    next_cursor: str | None = None


class DocumentChunk(BaseModel):
    path: str
    etag: str = Field(description="Changes whenever the file changes; use to detect stale reads.")
    size: int
    offset: int
    next_offset: int | None = Field(description="Pass as `offset` to continue; null at end of file.")
    content: str


def _state(ctx: Context) -> State:
    state = ctx.request_context.lifespan_context
    if not isinstance(state, State):
        raise ToolError("[internal] server is not initialized")
    return state


async def _run(ctx: Context, fn: Callable[..., T], *args: object) -> T:
    try:
        return await anyio.to_thread.run_sync(fn, *args, limiter=_state(ctx).io)
    except SandboxError as exc:
        log.info('{"event":"tool_error","code":"%s"}', exc.code)
        raise ToolError(f"[{exc.code}] {exc}") from None


def _utf8_cut(raw: bytes) -> int:
    """Drop a trailing partial code point so a page boundary stays valid UTF-8."""
    cut = len(raw)
    while cut and (raw[cut - 1] & 0xC0) == 0x80:
        cut -= 1
    if cut and raw[cut - 1] >= 0xC0:
        cut -= 1
    return cut or len(raw)


@mcp.tool(annotations=READ_ONLY)
async def list_documents(
    ctx: Context,
    cursor: Annotated[str | None, Field(description="Opaque cursor from a previous call.")] = None,
    page_size: Annotated[int, Field(ge=1, le=500)] = 100,
) -> DocumentList:
    """List readable documents under the configured root."""
    entries = await _run(ctx, _state(ctx).root.list)
    if cursor is None:
        start = 0
    else:
        try:
            start = int(cursor)
        except ValueError:
            raise ToolError("[invalid_cursor] cursor must be an integer") from None
        if start < 0:
            raise ToolError("[invalid_cursor] cursor must be zero or positive") from None
    page = entries[start : start + page_size]
    nxt = start + page_size
    return DocumentList(
        documents=[DocumentInfo(path=path, size=size) for path, size in page],
        next_cursor=str(nxt) if nxt < len(entries) else None,
    )


@mcp.tool(annotations=READ_ONLY)
async def read_document(
    ctx: Context,
    path: Annotated[str, Field(description="Relative path as returned by list_documents.")],
    offset: Annotated[int, Field(ge=0)] = 0,
    max_bytes: Annotated[int, Field(ge=1, le=262_144)] = 65_536,
) -> DocumentChunk:
    """Read a bounded UTF-8 window of one document."""
    raw, key = await _run(ctx, _state(ctx).root.read, path, offset, max_bytes)
    cut = len(raw)
    if offset + cut < key.size:
        cut = _utf8_cut(raw)
    end = offset + cut
    return DocumentChunk(
        path=path,
        etag=key.etag,
        size=key.size,
        offset=offset,
        next_offset=end if end < key.size else None,
        content=raw[:cut].decode("utf-8", errors="replace"),
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
