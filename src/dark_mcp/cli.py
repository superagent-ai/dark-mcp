import asyncio
import getpass
import json
import logging
import os
from enum import StrEnum
from typing import Annotated

import typer
from telethon import TelegramClient, errors

from dark_mcp.config import Settings
from dark_mcp.errors import DataAccessError
from dark_mcp.index import OnionIndex
from dark_mcp.onion import OnionConnector
from dark_mcp.server import create_server

app = typer.Typer(help="Onion and Telegram data access over MCP.", no_args_is_help=True, pretty_exceptions_enable=False)


class Transport(StrEnum):
    stdio = "stdio"
    http = "streamable-http"


@app.command()
def serve(
    transport: Transport = Transport.stdio,
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8000,
):
    """Run MCP over stdio (default) or HTTP on 127.0.0.1 only."""
    os.umask(0o077)
    logging.basicConfig(level=logging.WARNING)
    server = create_server()
    if transport == Transport.stdio:
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host="127.0.0.1", port=port)


@app.command()
def telegram_login():
    """Authorize an account locally. Never use this command through an agent tool."""
    os.umask(0o077)
    settings = Settings()
    settings.prepare_data_dir()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        typer.echo("Set DARK_MCP_TELEGRAM_API_ID and DARK_MCP_TELEGRAM_API_HASH locally.", err=True)
        raise typer.Exit(1)
    typer.echo("Log in only for data use you have permission to perform. Session stays on this machine.")

    async def login():
        client = TelegramClient(
            str(settings.session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
            receive_updates=False,
        )
        try:
            await client.start(
                phone=lambda: getpass.getpass("Telegram phone number: "),
                code_callback=lambda: getpass.getpass("Telegram verification code: "),
                password=lambda: getpass.getpass("Telegram two step verification password: "),
            )
        finally:
            await client.disconnect()
            if settings.session_path.exists():
                settings.session_path.chmod(0o600)

    try:
        asyncio.run(login())
    except (errors.RPCError, OSError, ValueError):
        typer.echo("Login failed. Check credentials, connection, and account status.", err=True)
        raise typer.Exit(1) from None
    typer.echo("Session saved. Configure permitted channels and enable the connector before serving.")


@app.command()
def crawl(
    seeds: Annotated[list[str], typer.Argument(help="One or more v3 onion URLs")],
    max_pages: Annotated[int, typer.Option(min=1, max=10)] = 5,
):
    """Populate the local onion index through Tor."""
    os.umask(0o077)
    settings = Settings()
    connector = OnionConnector(settings, OnionIndex(settings.prepare_data_dir()))
    try:
        result = asyncio.run(connector.crawl(seeds, max_pages))
    except (DataAccessError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(result, indent=2))
    if result["errors"]:
        raise typer.Exit(1)


@app.command()
def search(query: str, limit: Annotated[int, typer.Option(min=1, max=100)] = 10):
    """Search the local onion index without starting an MCP client."""
    os.umask(0o077)
    settings = Settings()
    try:
        result = OnionIndex(settings.prepare_data_dir()).search(query, limit)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(result, indent=2))
