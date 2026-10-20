import json

import typer

from dark_mcp.server import create_server

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve():
    """Serve the AIL-backed MCP over stdio."""
    create_server().run(transport="stdio")


@app.command()
def config():
    """Report non-secret connection configuration without making a backend request."""
    from dark_mcp.config import Settings

    settings = Settings()
    print(json.dumps({"backend": "AIL Framework", "url": settings.ail_url, "verify_tls": settings.ail_verify_tls}))
