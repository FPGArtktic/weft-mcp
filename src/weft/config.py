# SPDX-License-Identifier: GPL-2.0-only
"""TOML configuration: loading and validation.

The layout follows PROJECT.md 5.1. Quartus install paths always come from
here; nothing in WEFT hardcodes them or searches PATH for them.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IMAGE = "weft-tools"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8080
DEFAULT_JOB_TIMEOUT_S = 7200

#: State WEFT keeps inside the workspace.
STATE_DIR = ".weft"


class ConfigError(ValueError):
    """The configuration file is missing, malformed, or self-contradictory."""


@dataclass(frozen=True)
class Quartus:
    """One Quartus installation.

    @root: install root, the directory holding bin/quartus_sh
    @env: extra environment for every Quartus invocation, used to pass FlexLM
          variables through to the Pro edition
    """

    root: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Http:
    """Streamable HTTP transport settings.

    @token: static bearer token; without it the HTTP transport refuses to start
    """

    host: str = DEFAULT_HTTP_HOST
    port: int = DEFAULT_HTTP_PORT
    token: str | None = None


@dataclass(frozen=True)
class Config:
    """The whole configuration.

    @workspace: sandbox root; every client-supplied path resolves inside it
    @image: name of the locally built weft-tools image
    @quartus: installations by edition name, empty when no Quartus is set up
    @edition: edition used when a tool does not name one
    @jobs_db: SQLite file holding job state across restarts
    @job_timeout_s: wall-clock limit for a single compilation
    @rag_db: sqlite-vec store for documents, code, and generated docs
    @rag_model: local BGE-M3 weights; None disables the RAG tools
    @http: HTTP transport settings
    """

    workspace: Path
    image: str
    quartus: dict[str, Quartus]
    edition: str | None
    jobs_db: Path
    job_timeout_s: int
    rag_db: Path
    rag_model: Path | None
    http: Http


def load(path: str | Path) -> Config:
    """load - read and validate a configuration file

    @path: TOML file to read

    Return: a fully validated Config.

    Raises ConfigError if the file is missing or unreadable, if it contains
    unknown keys, if the workspace root or a configured Quartus root does not
    exist, or if the default edition names an unconfigured installation.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read configuration: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"malformed configuration: {e}") from e

    _reject_unknown(raw, {"workspace", "container", "quartus", "jobs", "rag", "http"}, "top level")

    _reject_unknown(_table(raw, "workspace"), {"root"}, "workspace")
    workspace = _directory(_require(raw, "workspace", "root"), "workspace.root")
    state = workspace / STATE_DIR

    container = _table(raw, "container")
    _reject_unknown(container, {"image"}, "container")

    quartus, edition = _quartus(_table(raw, "quartus"))

    jobs = _table(raw, "jobs")
    _reject_unknown(jobs, {"database", "timeout_s"}, "jobs")

    rag = _table(raw, "rag")
    _reject_unknown(rag, {"database", "model_path"}, "rag")

    http = _table(raw, "http")
    _reject_unknown(http, {"host", "port", "token"}, "http")

    return Config(
        workspace=workspace,
        image=container.get("image", DEFAULT_IMAGE),
        quartus=quartus,
        edition=edition,
        jobs_db=Path(jobs.get("database", state / "jobs.sqlite")),
        job_timeout_s=int(jobs.get("timeout_s", DEFAULT_JOB_TIMEOUT_S)),
        rag_db=Path(rag.get("database", state / "rag.sqlite")),
        rag_model=Path(rag["model_path"]) if "model_path" in rag else None,
        http=Http(
            host=http.get("host", DEFAULT_HTTP_HOST),
            port=int(http.get("port", DEFAULT_HTTP_PORT)),
            token=http.get("token"),
        ),
    )


def _quartus(table: dict) -> tuple[dict[str, Quartus], str | None]:
    """_quartus - build the per-edition installation map

    A machine without Quartus simply omits the section; the fast loop and the
    RAG tools do not need it. An edition that *is* configured is checked, so a
    mistyped path fails at startup rather than in the middle of a compilation.
    """
    edition = table.get("edition")
    _reject_unknown(table, {"edition", "lite", "pro"}, "quartus")

    installs = {}
    for name in ("lite", "pro"):
        if name not in table:
            continue
        entry = table[name]
        _reject_unknown(entry, {"root", "env"}, f"quartus.{name}")
        root = _directory(_require(table, name, "root"), f"quartus.{name}.root")
        if not (root / "bin" / "quartus_sh").is_file():
            raise ConfigError(f"quartus.{name}.root holds no bin/quartus_sh: {root}")
        installs[name] = Quartus(root=root, env=dict(entry.get("env", {})))

    if edition is not None and edition not in installs:
        raise ConfigError(f"quartus.edition names an unconfigured edition: {edition}")
    if edition is None and len(installs) == 1:
        edition = next(iter(installs))

    return installs, edition


def _table(raw: dict, name: str) -> dict:
    """_table - fetch an optional table, defaulting to empty."""
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _require(raw: dict, table: str, key: str) -> str:
    """_require - fetch a mandatory string from a mandatory table."""
    if table not in raw:
        raise ConfigError(f"missing [{table}] section")
    if key not in raw[table]:
        raise ConfigError(f"missing {table}.{key}")
    return raw[table][key]


def _directory(value: str, what: str) -> Path:
    """_directory - resolve a configured path that must already exist."""
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ConfigError(f"{what} is not a directory: {value}")
    return path


def _reject_unknown(table: dict, known: set[str], where: str) -> None:
    """_reject_unknown - refuse silently ignored keys, so typos surface."""
    unknown = sorted(set(table) - known)
    if unknown:
        raise ConfigError(f"unknown key(s) at {where}: {', '.join(unknown)}")
