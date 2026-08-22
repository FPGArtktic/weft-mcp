# SPDX-License-Identifier: GPL-3.0-only
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
from .index import indexer
from .index.store import SymbolStore
from .jobs import JobStore
from .quartus import flow, project, reports
from .quartus.install import Install, InstallError, probe
from .sandbox import resolve

#: A tool result stays small enough for a client to read without paging.
MAX_DIAGNOSTICS = 100

#: Default number of log lines get_job_log hands back.
DEFAULT_LOG_TAIL = 100

#: Default number of search hits returned.
DEFAULT_SEARCH_HITS = 20

#: State WEFT keeps inside the workspace, matching config.STATE_DIR.
STATE_DIR = ".weft"

CONFIG_ENV = "WEFT_CONFIG"
DEFAULT_CONFIG = Path("~/.config/weft/weft.toml")


class Installs:
    """The Quartus installations named in the configuration.

    Probing runs quartus_sh, which takes about a second, so each edition is
    identified once and remembered. Probing lazily also lets a machine with no
    Quartus start the server and use the fast loop.
    """

    def __init__(self, config: Config):
        self._config = config
        self._known: dict[str, Install] = {}

    def get(self, edition: str | None = None) -> Install:
        """get - the installation for @edition, or the configured default

        Raises InstallError when no edition is configured, or when the one
        asked for is not.
        """
        name = edition or self._config.edition
        if name is None:
            raise InstallError(
                "no Quartus edition configured; set quartus.edition, or configure exactly one"
            )
        if name not in self._known:
            entry = self._config.quartus.get(name)
            if entry is None:
                raise InstallError(f"no [quartus.{name}] section in the configuration")
            self._known[name] = probe(entry.root, entry.env)
        return self._known[name]


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

    installs = Installs(config)
    store = JobStore(config.jobs_db)

    def located(name: str) -> tuple[Path, str]:
        """located - the directory and revision behind a project reference

        @name: workspace-relative path to a .qpf, as list_projects returns
               them, or the bare project name

        Return: the resolved directory and the revision name.
        """
        reference = name if name.endswith(".qpf") else f"{name}/{name}.qpf"
        qpf = resolve(config.workspace, reference)
        return qpf.parent, qpf.stem

    @server.tool(
        description=(
            "Create a Quartus project: writes the .qpf and .qsf under a directory "
            "of the same name in the workspace."
        )
    )
    def create_project(
        name: str,
        family: str,
        part: str,
        top: str | None = None,
        edition: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """create_project - write a fresh project

        @name: project and revision name
        @family: device family, e.g. "MAX 10"
        @part: device part number
        @top: top-level entity; defaults to @name
        @edition: which configured Quartus to use; defaults to the configured one
        @overwrite: replace an existing project of the same name

        Return: the project path, relative to the workspace.
        """
        directory = resolve(config.workspace, name)
        qpf = project.create_project(
            installs.get(edition), directory, name, family, part, top=top, overwrite=overwrite
        )
        return {"project": str(qpf.relative_to(config.workspace)), "revision": name}

    @server.tool(
        description=(
            "Apply assignments to a project. Each assignment is "
            '{"kind": "global"|"location"|"instance"|"parameter", "name", "value", "to"}; '
            "a location carries its pin in value, and only a global needs no target."
        )
    )
    def set_assignments(
        project_ref: str,
        assignments: list[dict[str, str]],
        edition: str | None = None,
    ) -> dict[str, Any]:
        """set_assignments - edit a project's .qsf through the Tcl API

        @project_ref: workspace-relative .qpf path, or the project name
        @assignments: the assignments to apply, in order
        @edition: which configured Quartus to use

        Return: the rewritten .qsf, relative to the workspace.
        """
        directory, revision = located(project_ref)
        wanted = [
            project.Assignment(
                kind=a.get("kind", ""),
                value=a.get("value", ""),
                name=a.get("name", ""),
                to=a.get("to", ""),
            )
            for a in assignments
        ]
        qsf = project.set_assignments(installs.get(edition), directory, revision, wanted)
        return {"qsf": str(qsf.relative_to(config.workspace)), "applied": len(wanted)}

    @server.tool(description="List the Quartus projects in the workspace.")
    def list_projects() -> dict[str, Any]:
        """list_projects - every .qpf under the workspace root."""
        found = project.list_projects(config.workspace)
        return {"count": len(found), "projects": found}

    @server.tool(
        description=(
            "What a project is set up to build: device, top entity, and its sources "
            "grouped by language. Read from the .qsf, so it needs no Quartus."
        )
    )
    def get_project_info(project_ref: str) -> dict[str, Any]:
        """get_project_info - a project's device, top entity and sources

        @project_ref: workspace-relative .qpf path, or the project name

        Return: the assignments that describe what the project builds.
        """
        directory, revision = located(project_ref)
        return project.project_info(directory, revision)

    @server.tool(
        description=(
            "Start a compilation as a background job. Stage is full, syn, fit, asm or sta. "
            "Returns a job_id; poll it with get_job_status."
        )
    )
    def start_compile(
        project_ref: str, stage: str = flow.FULL, edition: str | None = None
    ) -> dict[str, Any]:
        """start_compile - run a stage, or the whole flow, in the background

        @project_ref: workspace-relative .qpf path, or the project name
        @stage: "full", "syn", "fit", "asm" or "sta"
        @edition: which configured Quartus to use

        Return: the job id and what it is running.
        """
        directory, revision = located(project_ref)
        job = flow.start_compile(store, installs.get(edition), directory, revision, stage=stage)
        return {"job_id": job.id, "revision": revision, "stage": stage, "status": job.status}

    @server.tool(description="Status of a compilation job, reconciled against the real process.")
    def get_job_status(job_id: str) -> dict[str, Any]:
        """get_job_status - where a job has got to

        @job_id: identifier from start_compile

        Return: status, exit code, the phase reached, and timestamps.
        """
        job = store.get(job_id)
        return {
            "job_id": job.id,
            "status": job.status,
            "stage": job.flow,
            "progress": flow.progress(job),
            "exit_code": job.exit_code,
            "revision": job.revision,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
        }

    @server.tool(description="Tail of a compilation job's log.")
    def get_job_log(job_id: str, tail: int = DEFAULT_LOG_TAIL) -> dict[str, Any]:
        """get_job_log - the end of the log a job is writing

        @job_id: identifier from start_compile
        @tail: how many trailing lines to return

        Return: the lines, and how many the log holds in total.
        """
        job = store.get(job_id)
        try:
            lines = Path(job.log_path).read_text(errors="replace").splitlines()
        except OSError:
            return {"job_id": job.id, "total_lines": 0, "lines": []}
        return {
            "job_id": job.id,
            "total_lines": len(lines),
            "lines": lines[-max(tail, 0) :],
        }

    @server.tool(description="Ask a running compilation to stop.")
    def cancel_job(job_id: str) -> dict[str, Any]:
        """cancel_job - signal a job and report what it became."""
        job = store.cancel(job_id)
        return {"job_id": job.id, "status": job.status}

    @server.tool(
        description=(
            "Parse a finished compilation's reports into resources, timing per clock "
            "domain, and the warnings and errors, worst first."
        )
    )
    def parse_reports(project_ref: str) -> dict[str, Any]:
        """parse_reports - what the last compilation produced

        @project_ref: workspace-relative .qpf path, or the project name

        Return: the parsed reports; raw logs stay on disk.
        """
        directory, revision = located(project_ref)
        return asdict(reports.parse_reports(directory, revision))

    symbols = SymbolStore(config.workspace / STATE_DIR / "symbols.sqlite")

    @server.tool(
        description=(
            "Index a directory of HDL: modules and entities, their ports, parameters "
            "and instantiations, from Verible and GHDL. On demand only; nothing "
            "watches the filesystem. Files whose content has not changed are skipped."
        )
    )
    def index_project(directory: str) -> dict[str, Any]:
        """index_project - read a directory of sources into the symbol index

        @directory: workspace-relative directory

        Return: how many modules were found and which files were read,
        skipped, dropped or rejected by a parser.
        """
        report = indexer.index_project(config.image, config.workspace, directory, symbols)
        return asdict(report) | {"indexed": symbols.stats()}

    @server.tool(
        description=(
            "Search the symbol index by name or declared type. Matching is textual "
            "over module, port, parameter and instance names; it does not rank by "
            "meaning."
        )
    )
    def search_code(query: str, top_k: int = DEFAULT_SEARCH_HITS) -> dict[str, Any]:
        """search_code - find where a name or type appears

        @query: substring, matched without regard to case
        @top_k: how many hits to return

        Return: the hits, and how many were returned.
        """
        hits = symbols.search(query, limit=top_k)
        return {"count": len(hits), "hits": [asdict(h) for h in hits]}

    @server.tool(
        description=(
            "Ports, parameters, instantiations and definition location of one module "
            "or entity, together with the modules that instantiate it."
        )
    )
    def get_module_info(name: str) -> dict[str, Any]:
        """get_module_info - everything indexed about one module

        @name: module or entity name, matched without regard to case, because
               GHDL folds VHDL identifiers and Verilog keeps its own

        Return: the module and its dependents, or a not-indexed marker.
        """
        module = symbols.module(name)
        if module is None:
            return {"name": name, "indexed": False}
        return asdict(module) | {"indexed": True, "instantiated_by": symbols.dependents(name)}

    @server.tool(
        description=(
            "The instance tree below a module. An instance whose module is not "
            "indexed is reported with resolved false rather than dropped."
        )
    )
    def get_hierarchy(top: str) -> dict[str, Any]:
        """get_hierarchy - the instance tree under @top

        @top: module or entity name

        Return: the tree, or a not-indexed marker.
        """
        tree = symbols.hierarchy(top)
        if tree is None:
            return {"top": top, "indexed": False}
        return tree

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
