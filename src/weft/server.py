# SPDX-License-Identifier: GPL-2.0-only
"""MCP server entry point.

The same tool implementations are served over stdio and over Streamable HTTP;
only the transport differs. The HTTP path is guarded by a static bearer token
from the configuration.
"""

import argparse
import hmac
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken

from . import __version__
from .config import Config, load
from .fastloop.lint import lint as run_lint
from .fastloop.simulate import simulate as run_simulate

#: A tool result stays small enough for a client to read without paging.
MAX_DIAGNOSTICS = 100

CONFIG_ENV = "WEFT_CONFIG"
DEFAULT_CONFIG = Path("~/.config/weft/weft.toml")


class StaticTokenVerifier:
    """Bearer token check against a single configured secret.

    WEFT is a LAN tool, not an OAuth resource server: the configuration names
    one token and any request carrying it is accepted.
    """

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        """verify_token - accept the configured token, reject everything else

        @token: the bearer token from the Authorization header

        Return: an AccessToken when @token matches, otherwise None.
        """
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(token=token, client_id="weft", scopes=[])


def build(config: Config) -> MCPServer:
    """build - assemble the server and register its tools

    @config: validated configuration

    Return: an MCPServer with every tool of the current milestone registered.
    """
    server = MCPServer(
        name="weft",
        version=__version__,
        instructions=(
            "Drives an Intel Quartus Prime FPGA flow. Paths are relative to the "
            "configured workspace root; nothing outside it can be reached."
        ),
    )

    @server.tool(
        description=(
            "Lint HDL sources without simulating them. Verilator handles Verilog "
            "and SystemVerilog, GHDL handles VHDL; a design written in both is "
            "linted one language at a time."
        )
    )
    def lint(files: list[str], language: str) -> dict[str, Any]:
        """lint - report diagnostics for a set of sources

        @files: workspace-relative paths, in dependency order for VHDL
        @language: "verilog" (covers SystemVerilog) or "vhdl"

        Return: the diagnostics and how many there were in total.
        """
        found = run_lint(config.image, config.workspace, files, language)
        return {
            "total": len(found),
            "returned": min(len(found), MAX_DIAGNOSTICS),
            "diagnostics": [asdict(d) for d in found[:MAX_DIAGNOSTICS]],
        }

    @server.tool(
        description=(
            "Build and run a testbench. The simulator follows from the sources' "
            "suffixes unless one is named. No open simulator reads Verilog and "
            "VHDL in one run, so a source set spanning both is narrowed to the "
            "language of the testbench and the files left out are listed in the "
            "result; without a testbench to choose by, such a set is refused."
        )
    )
    def simulate(
        files: list[str],
        top: str,
        testbench: str | None = None,
        simulator: str | None = None,
    ) -> dict[str, Any]:
        """simulate - run a testbench and report how it went

        @files: design sources, workspace-relative
        @top: unit to elaborate and run, normally the testbench
        @testbench: testbench source, if not already in @files
        @simulator: "verilator", "icarus" or "ghdl"; defaults by language

        Return: pass or fail, the tail of the log, any waveform written, and
        the sources excluded as belonging to the other language.
        """
        result = run_simulate(
            config.image,
            config.workspace,
            files,
            top,
            testbench=testbench,
            simulator=simulator,
            timeout=config.job_timeout_s,
        )
        return asdict(result)

    return server


class RequireToken:
    """Reject unauthenticated requests, leaving the lifespan channel alone.

    The SDK's RequireAuthMiddleware is written to wrap one route endpoint, so
    it answers every scope with an HTTP reply. Used as application middleware
    it answers the ASGI lifespan handshake with a 401 as well; the server then
    reports the lifespan protocol as unsupported and the session manager's
    task group never starts.
    """

    def __init__(self, app: Any):
        from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware

        self.app = app
        self._guard = RequireAuthMiddleware(app, required_scopes=[])

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """__call__ - guard requests and pass every other scope through."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        await self._guard(scope, receive, send)


def http_app(server: MCPServer, token: str):
    """http_app - the Streamable HTTP application, behind a bearer token

    @server: the built MCP server
    @token: the static token every request must present

    Return: the Starlette application, answering 401 without a valid token.
    """
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
    from starlette.middleware.authentication import AuthenticationMiddleware

    # The middleware goes into the app rather than around it, so the app stays
    # the ASGI entry point and its lifespan still runs.
    app = server.streamable_http_app()
    app.add_middleware(RequireToken)
    app.add_middleware(
        AuthenticationMiddleware, backend=BearerAuthBackend(StaticTokenVerifier(token))
    )
    return app


def config_path(argument: str | None) -> Path:
    """config_path - where the configuration comes from

    The command line wins, then WEFT_CONFIG, then the XDG-style default. The
    file is never searched for outside these three places.
    """
    if argument:
        return Path(argument).expanduser()
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV]).expanduser()
    return DEFAULT_CONFIG.expanduser()


def main(argv: list[str] | None = None) -> int:
    """main - command line entry point

    Return: 0 on a clean shutdown, 2 when the configuration is unusable.
    """
    parser = argparse.ArgumentParser(prog="weft", description="MCP server for the Quartus flow")
    parser.add_argument("--version", action="version", version=f"weft {__version__}")
    parser.add_argument("--config", help=f"configuration file (default: {DEFAULT_CONFIG})")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for a local client, http for Streamable HTTP on the LAN",
    )
    args = parser.parse_args(argv)

    config = load(config_path(args.config))
    server = build(config)

    if args.transport == "stdio":
        server.run("stdio")
        return 0

    if not config.http.token:
        parser.error("the http transport needs http.token in the configuration")

    import uvicorn

    uvicorn.run(
        http_app(server, config.http.token),
        host=config.http.host,
        port=config.http.port,
    )
    return 0
