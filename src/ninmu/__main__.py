"""Ninmu CLI entry point."""

import os

import uvicorn
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from ninmu.config import load_config
from ninmu.server import build_server


def main() -> None:
    """Run the Ninmu MCP server over streamable HTTP."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    config = load_config()
    server = build_server(config)
    # FastMCP does not add CORS headers itself; browser-based MCP clients
    # need them to read cross-origin responses. Origins can be restricted
    # via CORS_ORIGINS (comma-separated, default: all).
    origins = os.getenv("CORS_ORIGINS")
    app = server.http_app(
        path="/mcp",
        transport="streamable-http",
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=origins.split(",") if origins else ["*"],
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id"],
            )
        ],
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
