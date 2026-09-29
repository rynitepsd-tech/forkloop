"""Docker backend (forkloop/backends/docker.py) offline: the docker CLI is replaced by a recorder, and
the in-container agent (worlds/claims_ops_v1/docker/agent.py) runs as a local subprocess with a stub
``xdotool`` on PATH, so the real JSON-lines protocol is exercised without Docker. The one live test at
the end runs only with FORKLOOP_DOCKER_LIVE=1 on a host that has Docker and the golden image."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from forkloop.backends.base import BackendError, ConcurrencyError, apply_action
from forkloop.backends.docker import AGENT_PATH, DockerBackend, DockerCLI

ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = ROOT / "worlds" / "claims_ops_v1" / "docker"
AGENT_FILE = DOCKER_DIR / "agent.py"


class FakeCLI(DockerCLI):
    """Records docker invocations; ``exec -i`` spawns the real agent.py locally."""

    def __init__(self, tmp: Path, *, running: int = 0) -> None:
        super().__init__(None)
        self.tmp = tmp
        self.calls: list[tuple[str, ...]] = []
        self.containers: dict[str, dict] = {}
        self.extra_running = running
        self.run_dir = tmp / "run"
        self.run_dir.mkdir(exist_ok=True)
        (self.run_dir / "ready").write_text("")
        self.bin = tmp / "bin"
        self.bin.mkdir(exist_ok=True)
        self.xdo_log = tmp / "xdotool.log"
        stub = self.bin / "xdotool"
        stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {self.xdo_log}\n")
        stub.chmod(0o755)
        mariadb = self.bin / "mariadb"   # snapshot() runs FLUSH TABLES before committing
        mariadb.write_text(f"#!/bin/sh\nprintf 'mariadb %s\\n' \"$*\" >> {tmp / 'mariadb.log'}\n")
        mariadb.chmod(0o755)

    async def run(self, *args: str, timeout: float = 120.0, input=None, check: bool = True):
        self.calls.append(args)
        verb = args[0]
        if verb == "ps":
            names = [n for n, c in self.containers.items() if c["state"] == "running"]
            return 0, "\n".join([*names, *(f"other{i}" for i in range(self.extra_running))]), ""
        if verb == "run":
            name = args[args.index("--name") + 1]
            labels = dict(a.split("=", 1) for a in (args[i + 1] for i, x in enumerate(args) if x == "--label"))
            self.containers[name] = {"state": "running", "labels": labels, "image": args[-1]}
            return 0, "c0ffee\n", ""
        if verb == "rm":
            self.containers.pop(args[-1], None)
            return 0, "", ""
        if verb == "commit":
            return 0, "sha256:abc\n", ""
        if verb == "inspect":
            out = [{"Name": "/" + n, "State": {"Status": c["state"]}, "Config": {"Labels": c["labels"]},
                    "Created": "2026-09-28T00:00:00Z"} for n, c in self.containers.items() if n in args]
            return 0, json.dumps(out), ""
        if verb == "images":
            return 0, "sha256:1\nsha256:2\n", ""
        if verb == "image":
            return 0, json.dumps([
                {"Id": "sha256:1", "RepoTags": ["forkloop/claims-ops-v1:1"], "Size": 10, "Created": "t",
                 "Config": {"Labels": {"forkloop.kind": "golden", "forkloop.world": "claims-ops-v1"}}},
                {"Id": "sha256:2", "RepoTags": ["forkloop/claims-ops-v1:snap-1"], "Size": 20, "Created": "t",
                 "Config": {"Labels": {"forkloop.kind": "checkpoint", "forkloop.snapshot_name": "branch",
                                       "forkloop.parent": "forkloop/claims-ops-v1:1"}}}]), ""
        if verb == "rmi":
            return 0, "", ""
        raise AssertionError(f"unexpected docker call {args}")

    async def spawn(self, *args: str):
        assert args[:2] == ("exec", "-i") and args[-1] == AGENT_PATH, args
        env = {**os.environ, "FORKLOOP_AGENT_RUN_DIR": str(self.run_dir), "PATH": f"{self.bin}:{os.environ['PATH']}"}
        return await asyncio.create_subprocess_exec(sys.executable, "-u", str(AGENT_FILE), stdin=subprocess.PIPE,
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                                    limit=64 * 1024 * 1024)

    def xdo(self) -> list[str]:
        return self.xdo_log.read_text().splitlines() if self.xdo_log.exists() else []


def _backend(tmp_path: Path, **kw) -> tuple[DockerBackend, FakeCLI]:
    cli = FakeCLI(tmp_path, running=kw.pop("running", 0))
    kw.setdefault("image", "forkloop/claims-ops-v1:1")
    kw.setdefault("owner", "test-owner")
    return DockerBackend(cli=cli, **kw), cli


async def test_create_runs_the_image_with_labels_and_waits_for_the_agent(tmp_path):
    b, cli = _backend(tmp_path)
    m = await b.create(metadata={"run_id": "r1", "world": "claims-ops-v1"}, cpu=2, mem_mb=4096)
    try:
        run = next(c for c in cli.calls if c[0] == "run")
        assert run[-1] == "forkloop/claims-ops-v1:1" and "--pull" in run and run[run.index("--pull") + 1] == "never"
        labels = cli.containers[m.id]["labels"]
        assert labels["forkloop"] == "1" and labels["forkloop_owner"] == "test-owner"
        assert labels["forkloop.world_container"] == "1" and labels["forkloop.meta.run_id"] == "r1"
        assert "seccomp=unconfined" in run and run[run.index("--cpus") + 1] == "2" and run[run.index("--memory") + 1] == "4096m"
        assert m.backend_name == "docker" and m.snapshot_semantics == "filesystem" and b.snapshot_semantics == "filesystem"
        assert {"gui", "shell", "http"} <= m.capabilities and await m.display_size() == (1280, 720)
        assert await m.healthy()
        infos = await b.list_machines(metadata={"run_id": "r1"})
        assert [i.id for i in infos] == [m.id] and infos[0].state == "running" and infos[0].metadata["run_id"] == "r1"
        assert any(f"label=forkloop.meta.run_id=r1" == c for call in cli.calls if call[0] == "ps" for c in call)
    finally:
        await b.close()
    assert m.id not in cli.containers and not m.alive


async def test_controller_channel_exec_read_write_through_the_agent(tmp_path):
    b, _ = _backend(tmp_path)
    m = await b.create()
    try:
        r = await m.exec("sh", ["-c", "echo out; echo err >&2; exit 3"])
        assert (r.exit_code, r.stdout, r.stderr) == (3, "out\n", "err\n")
        r = await m.exec("sh", ["-c", "echo $FOO; pwd"], cwd=str(tmp_path), env={"FOO": "bar"})
        assert r.stdout.split() == ["bar", str(tmp_path.resolve())] or r.stdout.split()[0] == "bar"
        r = await m.exec("sleep", ["5"], timeout_ms=300)
        assert r.exit_code == 124
        assert (await m.exec("definitely-not-a-command-xyz")).exit_code == 127
        target = tmp_path / "vm" / "sub" / "f.bin"
        await m.write_file(str(target), b"\x00\x01binary", mode=0o600)
        assert await m.read_file(str(target)) == b"\x00\x01binary" and (target.stat().st_mode & 0o777) == 0o600
        await m.write_file(str(target), "text")
        assert await m.read_file(str(target)) == b"text"
        with pytest.raises(FileNotFoundError):
            await m.read_file(str(tmp_path / "missing"))
        # concurrent requests on one pipe are matched by id
        outs = await asyncio.gather(*(m.exec("echo", [str(i)]) for i in range(20)))
        assert [o.stdout.strip() for o in outs] == [str(i) for i in range(20)]
    finally:
        await b.close()


async def test_agent_channel_uses_the_solari_xdotool_semantics(tmp_path):
    from forkloop.actions import Action

    b, cli = _backend(tmp_path)
    m = await b.create()
    try:
        await m.click(10, 20)
        await m.click(10, 20, button="right")
        await m.double_click(5, 6)
        await m.move(7, 8)
        await m.press(["ctrl", "a"])            # chord = one xdotool string, as on the Solari guest
        await m.press(["Return"])
        await m.type_text("-rf hello")          # `--` keeps text that starts with a dash out of option parsing
        await m.scroll(640, 360, direction="down", amount=7)   # SolariMachine.scroll: round(7/3) = 2 pages
        await m.scroll(640, 360, direction="up", amount=1)     # at least one page
        await m.scroll(640, 360, direction="right", amount=2)  # horizontal: `amount` arrow presses
        await m.drag(0, 0, 100, 50)
        await apply_action(m, Action.parse({"type": "key", "keys": ["ctrl", "l"]}))
        await apply_action(m, Action.parse({"type": "right_click", "x": 1, "y": 2}))
        lines = cli.xdo()
        assert lines[:7] == ["mousemove 10 20 click --delay 0 1", "mousemove 10 20 click --delay 0 3",
                             "mousemove 5 6 click --repeat 2 --delay 80 1", "mousemove 7 8", "key ctrl+a",
                             "key Return", "type --delay 12 -- -rf hello"]
        assert lines[7:13] == ["mousemove 640 360", "key Page_Down Page_Down", "mousemove 640 360", "key Page_Up",
                               "mousemove 640 360", "key Right Right"]
        assert lines[13].startswith("mousemove 0 0 mousedown 1") and lines[13].endswith("mousemove 100 50 sleep 0.05 mouseup 1")
        assert lines[14:] == ["key ctrl+l", "mousemove 1 2 click --delay 0 3"]
    finally:
        await b.close()


async def test_snapshot_commits_paused_with_db_flush_and_revert_keeps_the_machine_id(tmp_path):
    b, cli = _backend(tmp_path)
    m = await b.create()
    try:
        sid = await m.snapshot("branch-1")
        assert (tmp_path / "mariadb.log").read_text() == "mariadb -e FLUSH TABLES\n"
        assert re.fullmatch(r"forkloop/claims-ops-v1:snap-[0-9a-f]{12}", sid)
        commit = next(c for c in cli.calls if c[0] == "commit")
        assert "--pause=true" in commit and commit[-2:] == (m.id, sid)
        changes = [commit[i + 1] for i, x in enumerate(commit) if x == "--change"]
        assert 'LABEL forkloop.kind="checkpoint"' in changes and 'LABEL forkloop.snapshot_semantics="filesystem"' in changes
        assert 'LABEL forkloop.snapshot_name="branch-1"' in changes and 'LABEL forkloop.world_container=""' in changes
        machine_id = m.id
        cli.calls.clear()
        await m.revert(sid)
        verbs = [c[0] for c in cli.calls]
        assert verbs[:2] == ["rm", "run"] and m.id == machine_id and cli.containers[m.id]["image"] == sid
        assert m.image == sid and await m.healthy()
        with pytest.raises(BackendError, match="Solari snapshot id"):
            await m.revert("snap_dlft9omnpkyw")
    finally:
        await b.close()


async def test_snapshot_stop_mode_restarts_mariadb(tmp_path, monkeypatch):
    b, cli = _backend(tmp_path, snapshot_db="stop")
    m = await b.create()
    seen: list[list[str]] = []
    real_exec = m.exec

    async def spy(cmd, args=None, **kw):
        seen.append([cmd, *(args or [])])
        return await real_exec("true")

    monkeypatch.setattr(m, "exec", spy)
    try:
        await m.snapshot()
        assert seen[0] == ["/usr/local/forkloop/svc.sh", "stop", "mariadb"]
        assert seen[-1] == ["/usr/local/forkloop/svc.sh", "start", "mariadb"]
    finally:
        await b.close()


async def test_concurrency_cap_and_solari_ids_are_refused(tmp_path):
    b, _ = _backend(tmp_path, concurrency_cap=2, running=2)
    with pytest.raises(ConcurrencyError):
        await b.create()
    with pytest.raises(BackendError, match="Solari snapshot id"):
        await b.create(from_snapshot="snap_dlft9omnpkyw")


async def test_snapshots_listing_and_golden_protection(tmp_path):
    b, cli = _backend(tmp_path)
    snaps = await b.list_snapshots()
    assert [(s.id, s.kind) for s in snaps] == [("forkloop/claims-ops-v1:1", "golden"),
                                               ("forkloop/claims-ops-v1:snap-1", "checkpoint")]
    assert snaps[1].name == "branch" and snaps[1].parent == "forkloop/claims-ops-v1:1"
    with pytest.raises(BackendError, match="golden"):
        await b.delete_snapshot("forkloop/claims-ops-v1:1")
    await b.delete_snapshot("forkloop/claims-ops-v1:snap-1")
    assert cli.calls[-1] == ("rmi", "forkloop/claims-ops-v1:snap-1")


def test_env_configuration_and_for_world(monkeypatch):
    from forkloop.world import load_world

    monkeypatch.setenv("FORKLOOP_DOCKER_IMAGE", "example/world:7")
    monkeypatch.setenv("FORKLOOP_DOCKER_CONCURRENCY", "16")
    monkeypatch.setenv("FORKLOOP_DOCKER_HOST", "ssh://ubuntu@box")
    monkeypatch.delenv("FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1", raising=False)
    world = load_world("claims-ops-v1")
    b = DockerBackend.for_world(world)
    assert (b.image, b.concurrency_cap, b.cli.env()["DOCKER_HOST"], b.snapshot_repo) == \
        ("example/world:7", 16, "ssh://ubuntu@box", "example/world")
    assert os.environ["FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1"] == "example/world:7"
    assert world.golden_snapshot_id() == "example/world:7"


def test_cli_and_comparison_config_accept_docker(tmp_path, monkeypatch):
    from forkloop.cli import _backend as cli_backend
    from forkloop.policy_config import load_config
    from forkloop.world import load_world

    monkeypatch.delenv("FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1", raising=False)
    assert cli_backend("docker", load_world("claims-ops-v1")).name == "docker"
    cfg = tmp_path / "c.yaml"
    cfg.write_text("version: 1\nbackend: docker\nfamily: resolve_denial\nseeds: [1]\nvariants:\n"
                   "  - {name: a, policy: scripted, options: {actions: []}}\n"
                   "  - {name: b, policy: scripted, options: {actions: []}}\n")
    config, _ = load_config(cfg, require_env=False)
    assert config["backend"] == "docker"


def test_png_encoder_round_trips_pixels():
    from PIL import Image

    spec = importlib.util.spec_from_file_location("fl_agent", AGENT_FILE)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    w, h = 7, 3
    bgrx = bytearray()
    for y in range(h):
        for x in range(w):
            bgrx += bytes([x * 30, y * 80, 200, 0])   # B, G, R, X
    png = agent.png_from_bgrx(w, h, bytes(bgrx), 1)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert im.size == (w, h) and im.getpixel((2, 1)) == (200, 80, 60)
    assert agent.png_from_bgrx(w, h, bytes(bgrx), 1) == png   # deterministic: wait_stable hashes PNG bytes


def test_image_launches_chrome_exactly_like_browser_setup():
    from worlds.claims_ops_v1.world import ClaimsOpsWorld

    entry = (DOCKER_DIR / "entrypoint.sh").read_text()
    setup = (ROOT / "worlds" / "claims_ops_v1" / "browser_setup.sh").read_text()

    def flags(text: str) -> list[str]:
        block = text[text.index("google-chrome "):]
        block = block[:block.index(">")]
        return [f.replace('"$PROFILE"', "/home/desktop/.config/forkloop-chrome")
                for f in re.findall(r"--[a-z-]+(?:=\S+)?", block)]

    expected = list(ClaimsOpsWorld.chrome_base_flags)
    assert flags(entry) == expected and flags(setup) == expected
    dockerfile = (DOCKER_DIR / "Dockerfile").read_text()
    assert "chrome_policy.json /etc/opt/chrome/policies/managed/forkloop.json" in dockerfile
    assert "Xvfb :0 -screen 0" in entry and 'SCREEN="${FORKLOOP_SCREEN:-1280x720}"' in entry


@pytest.mark.skipif(os.environ.get("FORKLOOP_DOCKER_LIVE") != "1" or shutil.which("docker") is None,
                    reason="live Docker test: set FORKLOOP_DOCKER_LIVE=1 on a host with Docker and the golden image")
async def test_live_container_screenshot_and_exec():  # pragma: no cover - needs Docker + the image
    from PIL import Image

    b = DockerBackend()
    m = await b.create(metadata={"run_id": "pytest-live"})
    try:
        png = await m.screenshot()
        assert Image.open(io.BytesIO(png)).size == (1280, 720)
        assert (await m.exec("id", ["-u"])).stdout.strip() == "0"
    finally:
        await b.close()


class _ClockMachine:
    """Just enough of a Machine for ClaimsOpsWorld.world_clock: `date -u +%s` answers ``now``."""

    def __init__(self, backend_name: str, now: int) -> None:
        self.backend_name, self.now = backend_name, now

    async def exec(self, cmd, args=None, **kw):
        from forkloop.types import ExecResult

        assert [cmd, *(args or [])] == ["date", "-u", "+%s"]
        return ExecResult(0, f"{self.now}\n", "")


async def test_world_clock_is_enforced_on_docker_only(monkeypatch):
    import datetime as dt

    from forkloop.world import load_world

    world = load_world("claims-ops-v1")
    anchor = int(dt.datetime(2026, 9, 7, 9, tzinfo=dt.timezone.utc).timestamp())
    monkeypatch.delenv("FORKLOOP_WORLD_CLOCK", raising=False)
    ok, note = await world.world_clock(_ClockMachine("docker", anchor + 600))
    assert ok and note == "2026-09-07T09:10:00Z"
    ok, note = await world.world_clock(_ClockMachine("docker", anchor + 22 * 86400))   # the real date, unfaked
    assert not ok and "not within 1 day" in note
    ok, note = await world.world_clock(_ClockMachine("solari", anchor + 8 * 86400))    # Solari: snapshot clock
    assert ok and "not enforced" in note
    monkeypatch.setenv("FORKLOOP_WORLD_CLOCK", "2026-10-01T09:00:00Z")
    ok, _ = await world.world_clock(_ClockMachine("docker", anchor))
    assert not ok
    monkeypatch.setenv("FORKLOOP_WORLD_CLOCK", "real")
    ok, _ = await world.world_clock(_ClockMachine("docker", anchor + 22 * 86400))
    assert ok


async def test_health_fails_on_an_unfaked_docker_clock(monkeypatch):
    import datetime as dt

    from forkloop.world import HealthReport, World, load_world

    world = load_world("claims-ops-v1")
    monkeypatch.delenv("FORKLOOP_WORLD_CLOCK", raising=False)

    async def base_ok(self, machine, dbs):
        return HealthReport(ok=True, checks={})

    monkeypatch.setattr(World, "health", base_ok)

    class Db:
        async def scalar(self, sql, params=()):
            return 40

    m = _ClockMachine("docker", int(dt.datetime(2026, 9, 29, tzinfo=dt.timezone.utc).timestamp()))
    rep = await world.health(m, {"portal": Db(), "openemr": Db()})
    assert not rep.ok and rep.checks["world_clock"].startswith("failed")


def test_image_fakes_the_clock_for_every_process_and_chrome_gets_the_nopthread_build():
    runtime = (DOCKER_DIR / "Dockerfile.runtime").read_text()
    full = (DOCKER_DIR / "Dockerfile").read_text()
    for text in (runtime, full):
        assert "ENV LD_PRELOAD=/usr/local/lib/faketime/libfaketime.so.1 FAKETIME_DONT_FAKE_MONOTONIC=1" in text
        assert 'FAKETIME_COMPILE_CFLAGS="-UFAKE_PTHREAD"' in text and "install -m 755 /usr/local/forkloop/chrome-wrapper.sh /usr/bin/google-chrome" in text
    wrapper = (DOCKER_DIR / "chrome-wrapper.sh").read_text()
    assert "libfaketime-nopthread.so.1" in wrapper and "exec /opt/google/chrome/google-chrome" in wrapper
    entry = (DOCKER_DIR / "entrypoint.sh").read_text()
    # the offset lives in /etc/faketimerc; exporting FAKETIME made php-fpm workers fall back to the real clock
    assert "/etc/faketimerc" in entry and "export FAKETIME=" not in entry
