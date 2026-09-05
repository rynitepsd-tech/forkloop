"""World registry and base class (docs/contracts.md §4).

A world = ``worlds/<pkg>/world.yaml`` + a Python class that knows how to build
the golden snapshot, check health, open the initial screen, and describe its
databases to the oracle. ``load_world("claims-ops-v1")`` finds it by name.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import yaml

from .dbaccess import DbAccess
from .oracle import OracleContext
from .tasks import TaskInstance

if TYPE_CHECKING:  # pragma: no cover
    from .backends.base import Machine

WORLDS_DIR = Path(__file__).resolve().parent.parent / "worlds"


@dataclass
class HealthReport:
    ok: bool
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": self.checks}


@dataclass
class WorldConfig:
    name: str
    version: int
    resolution: str
    template: str
    golden_snapshot_env: str
    paths: dict[str, str]
    databases: dict[str, dict[str, Any]]
    apps: dict[str, dict[str, Any]]
    families: list[str]
    seed_module: str
    budget: dict[str, Any]
    forbidden_paths: list[str]
    oracle: dict[str, Any]
    module: str
    dir: Path
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        w, h = self.resolution.lower().split("x")
        return int(w), int(h)

    @staticmethod
    def load(path: Path) -> "WorldConfig":
        raw = yaml.safe_load(path.read_text())
        known = {"name", "version", "resolution", "template", "golden_snapshot_env", "paths", "databases", "apps",
                 "families", "seed_module", "budget", "forbidden_paths", "oracle", "module"}
        extra = {k: v for k, v in raw.items() if k not in known}
        return WorldConfig(
            name=raw["name"], version=int(raw.get("version", 1)), resolution=raw.get("resolution", "1280x720"),
            template=raw.get("template", "default"), golden_snapshot_env=raw.get("golden_snapshot_env", ""),
            paths=dict(raw.get("paths", {})), databases=dict(raw.get("databases", {})), apps=dict(raw.get("apps", {})),
            families=list(raw.get("families", [])), seed_module=raw.get("seed_module", ""),
            budget=dict(raw.get("budget", {"max_steps": 60, "max_seconds": 600})),
            forbidden_paths=list(raw.get("forbidden_paths", [])), oracle=dict(raw.get("oracle", {})),
            module=raw.get("module", ""), dir=path.parent, extra=extra,
        )


class World:
    """Base class. Subclasses override the hooks they need."""

    def __init__(self, config: WorldConfig) -> None:
        self.config = config
        self._seed_fn: Optional[Callable[..., TaskInstance]] = None

    # ------------------------------------------------------------ identity
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def size(self) -> tuple[int, int]:
        return self.config.size

    def golden_snapshot_id(self) -> Optional[str]:
        import os

        env = self.config.golden_snapshot_env
        return os.environ.get(env) if env else None

    # ------------------------------------------------------------- tasks
    def generate(self, family: str, seed: int, split: str = "train") -> TaskInstance:
        if family not in self.config.families:
            raise ValueError(f"unknown family {family!r}; world has {self.config.families}")
        if self._seed_fn is None:
            mod = importlib.import_module(self.config.seed_module)
            self._seed_fn = getattr(mod, "generate")
        task = self._seed_fn(family, seed, split)
        task.oracle.validate()
        return task

    # ---------------------------------------------------------- databases
    def databases(self, machine: "Machine") -> dict[str, DbAccess]:
        out: dict[str, DbAccess] = {}
        for name, cfg in self.config.databases.items():
            dialect = cfg["dialect"]
            if machine.backend_name == "fake" and dialect == "mysql":
                shim = cfg.get("shim_path")
                if not shim:
                    raise ValueError(f"database {name} needs shim_path for the fake backend")
                out[name] = DbAccess(machine, "sqlite", path=shim, name=name)
            elif dialect == "sqlite":
                out[name] = DbAccess(machine, "sqlite", path=cfg["path"], name=name)
            else:
                out[name] = DbAccess(machine, "mysql", database=cfg["database"], user=cfg["user"],
                                     password_file=cfg["password_file"], name=name)
        return out

    def oracle_context(self, dbs: dict[str, DbAccess], baseline: Any) -> OracleContext:
        o = self.config.oracle
        return OracleContext(
            dbs=dbs, baseline=baseline,
            primary_keys=dict(o.get("primary_keys", {})),
            exempt_tables=list(o.get("exempt_tables", [])),
            audit=dict(o.get("audit", {})),
            page_views=o.get("page_views"),
            forbidden_paths=list(self.config.forbidden_paths),
            audit_entity_names=dict(o.get("audit_entity_names", {})),
            audit_id_lookup=dict(o.get("audit_id_lookup", {})),
        )

    def checksum_tables(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.config.oracle.get("checksum_tables", {}).items()}

    def watermark_tables(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.config.oracle.get("watermark_tables", {}).items()}

    def ignore_columns(self) -> dict[str, list[str]]:
        """db → columns the app rewrites on its own (never counted as agent edits)."""
        return {k: list(v) for k, v in self.config.oracle.get("ignore_columns", {}).items()}

    def primary_keys(self) -> dict[str, str]:
        return dict(self.config.oracle.get("primary_keys", {}))

    # -------------------------------------------------------------- hooks
    async def build(self, machine: "Machine", *, log: Callable[[str], None] = print) -> str:
        """One-time world build on a fresh machine → golden snapshot id."""
        raise NotImplementedError(f"world {self.name} does not implement build()")

    async def health(self, machine: "Machine", dbs: dict[str, DbAccess]) -> HealthReport:
        checks: dict[str, Any] = {}
        ok = True
        for name, db in dbs.items():
            good = await db.ping()
            checks[f"db.{name}"] = good
            ok = ok and good
        if "http" in machine.capabilities:
            for app, cfg in self.config.apps.items():
                url = cfg["url"].rstrip("/") + cfg.get("health", "/")
                r = await machine.exec("curl", ["-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", url],
                                       timeout_ms=15_000)
                code = r.stdout.strip()
                checks[f"http.{app}"] = code
                ok = ok and code.startswith(("2", "3"))
        return HealthReport(ok=ok, checks=checks)

    async def open_initial_screen(self, machine: "Machine", screen: dict[str, Any]) -> None:
        """Default: focus the browser and navigate with ctrl+l (agent channel only)."""
        if "gui" not in machine.capabilities or machine.backend_name == "fake":
            return
        url = screen.get("url")
        if not url:
            return
        await machine.press(["ctrl", "l"])
        await machine.type_text(url)
        await machine.press(["Return"])

    async def diagnostics(self, machine: "Machine") -> dict[str, str]:
        """Text files worth keeping next to a finished episode (browser/kernel logs). Best effort."""
        return {}

    async def before_episode(self, machine: "Machine") -> None:
        """Hook after seeding, before the agent's first observation."""
        return None

    async def ui_milestones(self, dbs: dict[str, DbAccess], baseline: Any, task: TaskInstance) -> Optional[dict[str, Any]]:
        """Controller-side progress rungs read from the databases after an episode (which UI
        screens the agent reached), stored under ``verdict.details["ui_milestones"]``. Analysis
        only: never part of the reward. Worlds that cannot tell return None."""
        return None

    def gui_factory(self) -> Any:
        """Optional GuiSim factory for the fake backend."""
        return None


# ------------------------------------------------------------------ registry


def _iter_world_dirs(extra_dirs: Optional[list[Path]] = None):
    dirs = [WORLDS_DIR, *(extra_dirs or [])]
    for base in dirs:
        if not base.exists():
            continue
        for p in sorted(base.iterdir()):
            if (p / "world.yaml").exists():
                yield p


def list_worlds(extra_dirs: Optional[list[Path]] = None) -> list[str]:
    return [WorldConfig.load(p / "world.yaml").name for p in _iter_world_dirs(extra_dirs)]


def load_world(name: str, extra_dirs: Optional[list[Path]] = None) -> World:
    for p in _iter_world_dirs(extra_dirs):
        cfg = WorldConfig.load(p / "world.yaml")
        if cfg.name == name or p.name == name:
            return _instantiate(cfg)
    raise KeyError(f"unknown world {name!r}; available: {list_worlds(extra_dirs)}")


def _instantiate(cfg: WorldConfig) -> World:
    if not cfg.module:
        return World(cfg)
    mod_name, _, cls_name = cfg.module.partition(":")
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name) if cls_name else World
    return cls(cfg)


__all__ = ["World", "WorldConfig", "HealthReport", "load_world", "list_worlds", "WORLDS_DIR"]
