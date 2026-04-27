from mcp.server import Server
from mcp.server.stdio import stdio_server

# Minimal MCP server (no tools yet)
server = Server(name="local-tools-mcp")


def main():
    # Start MCP server over stdio
    stdio_server(server)


if __name__ == "__main__":
    main()
    