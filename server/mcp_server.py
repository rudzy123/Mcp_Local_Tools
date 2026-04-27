from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequest, CallToolResult, TextContent

#from server.tools import list_documents, read_document
from .tools import list_documents, read_document

server = Server(name="local-tools-mcp")


def handle_tool_call(request: CallToolRequest) -> CallToolResult:
    """
    Explicit tool router.
    """
    try:
        if request.name == "list_documents":
            result = list_documents()

        elif request.name == "read_document":
            filename = request.arguments.get("filename")
            result = read_document(filename)

        else:
            raise ValueError(f"Unknown tool: {request.name}")

        return CallToolResult(
            content=[TextContent(type="text", text=str(result))]
        )

    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ERROR: {e}")]
        )


# Register handler (stable API)
server.call_tool = handle_tool_call


def main():
    stdio_server(server)


if __name__ == "__main__":
    main()