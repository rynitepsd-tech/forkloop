"""Tests for the train/ ladder scripts: make_sft, wilson, plot --demo, train_lora (torch-free), eval, bakeoff."""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TYPES = {"click", "double_click", "right_click", "move", "scroll", "drag", "type", "key", "wait", "done"}


class InvalidAction(Exception):
    pass


class FakeAction:
    def __init__(self, d: dict) -> None:
        self.d = dict(d)

    @classmethod
    def parse(cls, obj):
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except ValueError as e:
                raise InvalidAction(str(e)) from e
        if not isinstance(obj, dict) or obj.get("type") not in _TYPES:
            raise InvalidAction(f"bad action {obj!r}")
        return cls(obj)

    def to_dict(self) -> dict:
        return dict(self.d)


def _install_forkloop_stubs() -> None:
    pkg = ROOT / "forkloop"
    stubs = {
        "forkloop.actions": {"Action": FakeAction, "InvalidAction": InvalidAction},
        "forkloop.env": {"Env": object, "Observation": object, "make": lambda *a, **k: None},
        "forkloop.oracle": {"Check": object, "OracleSpec": object, "Verdict": object},
        "forkloop.tasks": {"Seeding": object, "SeedFile": object, "TaskInstance": object},
    }
    for modname, attrs in stubs.items():
        if modname in sys.modules or (pkg / (modname.split(".")[1] + ".py")).exists():
            continue
        mod = types.ModuleType(modname)
        mod.__dict__.update(attrs)
        sys.modules[modname] = mod
    import forkloop  # noqa: F401


_install_forkloop_stubs()

from train import make_sft  # noqa: E402
from train.wilson import wilson_interval, wilson_summary, format_rate  # noqa: E402


# --------------------------------------------------------------------------- #
# synthetic run directory per contracts.md §10
# --------------------------------------------------------------------------- #

def _png_bytes(w: int = 32, h: int = 18) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


def make_run(root: Path, run_id: str, episodes: list[dict]) -> Path:
    """episodes: dicts with task_id, family, split, seed, reward, actions (list of dicts), valid (list[bool] | None)."""
    run_dir = root / "runs" / run_id
    (run_dir / "episodes").mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"backend": "fake", "world": "claims-ops-v1", "policy": "teacher"}))
    for idx, ep in enumerate(episodes):
        ep_dir = run_dir / "episodes" / f"ep_{idx:03d}_{ep['task_id']}"
        (ep_dir / "shots").mkdir(parents=True)
        manifest = {
            "world": "claims-ops-v1", "family": ep["family"], "seed": ep["seed"], "split": ep["split"],
            "task_id": ep["task_id"], "instruction": f"Do task {ep['task_id']}",
            "initial_screen": {"app": "portal", "url": "http://localhost:8080/claims"},
            "budget": {"max_steps": 60, "max_seconds": 600}, "difficulty": {}, "world_version": 1,
        }
        (ep_dir / "manifest.json").write_text(json.dumps(manifest))
        (ep_dir / "verdict.json").write_text(json.dumps({
            "reward": ep["reward"], "milestones": 1.0 if ep["reward"] == 1.0 else 0.5,
            "reason_code": "OK" if ep["reward"] == 1.0 else "WRONG_VALUE", "failed": [], "details": {}}))
        valid = ep.get("valid") or [True] * len(ep["actions"])
        with (ep_dir / "steps.jsonl").open("w") as f:
            for i, (action, ok) in enumerate(zip(ep["actions"], valid)):
                before = f"shots/{i:03d}_before.png"
                after = f"shots/{i:03d}_after.png"
                (ep_dir / before).write_bytes(_png_bytes())
                (ep_dir / after).write_bytes(_png_bytes())
                raw = make_sft.to_compact(action) if ok else "fly(1, 2)"
                line = {"i": i, "t_wall": 1.0 * i, "action": action if ok else None, "raw_action": raw, "valid": ok,
                        "shot_before": before, "shot_after": after, "model_latency_s": 0.5,
                        "tokens": {"in": 100, "out": 10}, "milestones": 0.0, "policy_note": ""}
                f.write(json.dumps(line) + "\n")
    return run_dir


CLICK = {"type": "click", "x": 640, "y": 360, "button": "left"}
TYPE = {"type": "type", "text": "C-1042"}
KEY = {"type": "key", "keys": ["Return"]}
DONE = {"type": "done", "success": True}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    episodes = [
        {"task_id": "resolve_denial-train-000003", "family": "resolve_denial", "split": "train", "seed": 3,
         "reward": 1.0, "actions": [CLICK, TYPE, KEY, DONE]},
        {"task_id": "resolve_denial-train-000001", "family": "resolve_denial", "split": "train", "seed": 1,
         "reward": 1.0, "actions": [CLICK, DONE]},
        {"task_id": "resolve_denial-train-000002", "family": "resolve_denial", "split": "train", "seed": 2,
         "reward": 0.0, "actions": [CLICK, CLICK, DONE]},                                   # failed: excluded
        {"task_id": "resolve_denial-heldout_seeds-100000", "family": "resolve_denial", "split": "heldout_seeds",
         "seed": 100000, "reward": 1.0, "actions": [CLICK, DONE]},                          # heldout: excluded by pattern
        {"task_id": "update_insurance_reconcile-train-000005", "family": "update_insurance_reconcile", "split": "train",
         "seed": 5, "reward": 1.0, "actions": [CLICK, TYPE, DONE], "valid": [True, False, True]},  # one invalid step
    ]
    return make_run(tmp_path, "teacher_v1", episodes)


def test_make_sft_records_and_stats(run_dir: Path):
    records, stats = make_sft.build_sft_records([run_dir], history_k=8)
    # kept: 000001 (2 steps), 000003 (4 steps), heldout (2 steps), update_insurance (2 valid of 3) = 10
    assert stats.episodes_seen == 5 and stats.episodes_failed == 1 and stats.episodes_kept == 4
    assert stats.records == len(records) == 10
    assert stats.steps_invalid_skipped == 1
    rec = next(r for r in records if r["task_id"] == "resolve_denial-train-000003" and r["step"] == 2)
    assert rec["target"] == 'key("Return")'
    assert rec["history"] == ["click(640, 360)", 'type("C-1042")']
    assert rec["instruction"] == "Do task resolve_denial-train-000003"
    assert rec["family"] == "resolve_denial" and rec["seed"] == 3 and rec["split"] == "train"
    assert len(rec["images"]) == 1 and Path(rec["images"][0]).is_absolute() and Path(rec["images"][0]).exists()
    assert rec["images"][0].endswith("shots/002_before.png")
    # the invalid step's raw text still appears in the following step's history
    upd = [r for r in records if r["family"] == "update_insurance_reconcile"]
    assert [r["step"] for r in upd] == [0, 2]
    assert upd[1]["history"] == ["click(640, 360)", "fly(1, 2)"]
    assert stats.per_family == {"resolve_denial": 3, "update_insurance_reconcile": 1}


def test_make_sft_limit_exclude_families_and_cli(run_dir: Path, tmp_path: Path):
    records, stats = make_sft.build_sft_records([run_dir], limit=2, exclude_splits=["heldout_*"])
    # sorted by task_id: resolve_denial-train-000001, resolve_denial-train-000003, update_insurance...
    assert {r["task_id"] for r in records} == {"resolve_denial-train-000001", "resolve_denial-train-000003"}
    assert stats.episodes_filtered_split == 1 and stats.episodes_dropped_by_limit == 1 and stats.records == 6

    records, stats = make_sft.build_sft_records([run_dir], families=["update_insurance_reconcile"])
    assert stats.episodes_kept == 1 and stats.episodes_filtered_family == 4

    out = tmp_path / "out" / "sft.jsonl"
    rc = make_sft.main(["--run-dir", str(run_dir), "--out", str(out), "--limit", "1", "--exclude-split", "heldout_*",
                        "--history-k", "1"])
    assert rc == 0
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 2 and all(l["task_id"] == "resolve_denial-train-000001" for l in lines)
    assert lines[1]["history"] == ["click(640, 360)"]
    stats_json = json.loads((tmp_path / "out" / "sft.jsonl.stats.json").read_text())
    assert stats_json["records"] == 2 and stats_json["episodes_kept"] == 1

    rc = make_sft.main(["--runs-root", str(run_dir.parent), "--run-id", "teacher_v1", "--out", str(out),
                        "--exclude-split", "heldout_*", "--exclude-split", "train"])
    assert rc == 0 and out.read_text() == ""


def test_make_sft_tolerates_broken_episode(tmp_path: Path):
    run_dir = make_run(tmp_path, "r", [{"task_id": "t-train-000001", "family": "f", "split": "train", "seed": 1,
                                        "reward": 1.0, "actions": [CLICK, DONE]}])
    broken = run_dir / "episodes" / "zz_broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json")
    unverified = run_dir / "episodes" / "zz_unverified"
    unverified.mkdir()
    (unverified / "manifest.json").write_text(json.dumps({"task_id": "u", "family": "f", "split": "train", "seed": 9}))
    (unverified / "steps.jsonl").write_text("")
    records, stats = make_sft.build_sft_records([run_dir])
    assert stats.episodes_seen == 3 and stats.episodes_missing_files == 1 and stats.episodes_unverified == 1
    assert stats.records == 2


# --------------------------------------------------------------------------- #
# wilson
# --------------------------------------------------------------------------- #

def test_wilson_sanity():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 20)
    assert low == 0.0 and 0.0 < high < 0.2
    low, high = wilson_interval(20, 20)
    assert high == 1.0 and 0.8 < low < 1.0
    low, high = wilson_interval(50, 100)
    assert abs(low - 0.4038) < 0.002 and abs(high - 0.5962) < 0.002
    # narrower with more data, same rate
    l1, h1 = wilson_interval(5, 10)
    l2, h2 = wilson_interval(50, 100)
    assert (h2 - l2) < (h1 - l1)
    s = wilson_summary(31, 50)
    assert s["k"] == 31 and s["n"] == 50 and abs(s["rate"] - 0.62) < 1e-9 and s["low"] < 0.62 < s["high"]
    assert format_rate(31, 50).startswith("62.0% [")
    assert wilson_interval(150, 100) == wilson_interval(100, 100)  # clamped


# --------------------------------------------------------------------------- #
# plot --demo
# --------------------------------------------------------------------------- #

def test_plot_demo_writes_two_pngs(tmp_path: Path):
    from train import plot

    rc = plot.main(["--demo", "--out-dir", str(tmp_path)])
    assert rc == 0
    c1 = tmp_path / "chart1_synthetic.png"
    c2 = tmp_path / "chart2_synthetic.png"
    assert c1.exists() and c1.stat().st_size > 5000
    assert c2.exists() and c2.stat().st_size > 5000
    curve = json.loads((tmp_path / "curve_synthetic.json").read_text())
    bench = json.loads((tmp_path / "bench_synthetic.json").read_text())
    assert curve["synthetic"] is True and bench["synthetic"] is True
    assert "SYNTHETIC PLACEHOLDER" in curve["title"] and "SYNTHETIC PLACEHOLDER" in bench["title"]
    with Image.open(c1) as im:
        assert im.width > 600 and im.height > 300


def test_plot_functions_with_minimal_json(tmp_path: Path):
    from train import plot

    curve = {"title": "t", "x": [0, 25, "all"],
             "series": {"base": {"rate": [0.2], "low": [0.1], "high": [0.3]},
                        "SFT": {"rate": [0.2, 0.4, 0.6], "low": [0.1, 0.3, 0.5], "high": [0.3, 0.5, 0.7]}}}
    out1 = tmp_path / "c1.png"
    plot.chart1(curve, out1)
    assert out1.exists() and out1.stat().st_size > 1000
    bench = {"title": "b", "methods": [
        {"name": "revert()", "p50_s": 1.0, "p95_s": 2.0, "p99_s": 3.0, "failure_rate": 0.0, "cost_per_1k_usd": 1.5, "n": 10},
        {"name": "fresh VM", "p50_s": 10.0, "p95_s": 20.0, "p99_s": 30.0, "failure_rate": 0.1, "cost_per_1k_usd": 15.0, "n": 10}]}
    out2 = tmp_path / "c2.png"
    plot.chart2(json.dumps(bench), out2)  # JSON text accepted too
    assert out2.exists() and out2.stat().st_size > 1000
    (tmp_path / "curve.json").write_text(json.dumps(curve))
    assert plot.main(["chart1", str(tmp_path / "curve.json"), str(tmp_path / "c3.png")]) == 0
    assert (tmp_path / "c3.png").exists()


# --------------------------------------------------------------------------- #
# train_lora without torch
# --------------------------------------------------------------------------- #

def test_train_lora_imports_without_torch(monkeypatch, tmp_path: Path, capsys):
    for name in ("torch", "transformers", "peft"):
        monkeypatch.setitem(sys.modules, name, None)  # importing raises ImportError
    sys.modules.pop("train.train_lora", None)
    mod = importlib.import_module("train.train_lora")
    assert "estimate" in mod.__doc__.lower() and "VRAM" in mod.__doc__
    with pytest.raises(SystemExit) as exc:
        mod.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--lora-r" in out and "--smoke" in out and "--max-image-side" in out and "--output-dir" in out

    # torch-free helpers
    path = mod.make_synthetic_records(tmp_path / "syn", n=4)
    recs = mod.load_records(path)
    assert len(recs) == 4 and all(Path(r["images"][0]).exists() for r in recs)
    prompt, full, target = mod.build_messages(recs[1], "compact", 8, (1280, 720))
    assert [m["role"] for m in prompt] == ["system", "user"] and full[-1]["role"] == "assistant"
    assert full[-1]["content"][0]["text"] == 'type("C-1042")' == target
    assert any(p.get("type") == "image" for p in prompt[1]["content"])
    _, _, fara_target = mod.build_messages(recs[0], "fara", 8, (1280, 720))
    assert fara_target.startswith("<tool_call>") and '"left_click"' in fara_target and "[500, 500]" in fara_target
    _, _, json_target = mod.build_messages(recs[0], "json", 8, (1280, 720))
    assert json_target.startswith("```json") and '"x": 640' in json_target
    ds = mod.SFTExamples(recs, max_image_side=320, style="compact", history_k=8)
    ex = ds[0]
    # coord_space "image": the target is rescaled into the 320x180 image the model actually sees
    assert ex["image"].size == (320, 180) and ex["target"] == "click(160, 90)"
    ds_full = mod.SFTExamples(recs, max_image_side=1280, style="compact", history_k=8)
    assert ds_full[0]["target"] == "click(640, 360)"
    with pytest.raises(ImportError):
        mod.make_collate(object())
    with pytest.raises(SystemExit):
        mod.build_parser().parse_args([])  # --model and --output-dir are required


# --------------------------------------------------------------------------- #
# eval with fake env + policy
# --------------------------------------------------------------------------- #

class FakeEnv:
    def __init__(self, family: str, split: str, succeed_seeds: set[int]) -> None:
        self.family = family
        self.split = split
        self.succeed_seeds = succeed_seeds
        self.seed = None
        self.steps = 0
        self.closed = False

    async def reset(self, seed: int):
        self.seed = seed
        self.steps = 0
        obs = types.SimpleNamespace(screenshot=_png_bytes(), instruction=f"task {seed}", step=0, history=[],
                                    width=1280, height=720)
        return obs, {"task_id": f"{self.family}-{self.split}-{seed:06d}"}

    async def step(self, action, *, meta=None):
        self.steps += 1
        assert meta is None or isinstance(meta, dict)
        done = (not isinstance(action, str)) and action.to_dict()["type"] == "done"
        obs = types.SimpleNamespace(screenshot=_png_bytes(), instruction="", step=self.steps, history=[],
                                    width=1280, height=720)
        terminated = done
        truncated = self.steps >= 5
        reward = 1.0 if (terminated and self.seed in self.succeed_seeds) else 0.0
        return obs, reward, terminated, truncated, {}

    async def verify(self):
        ok = self.seed in self.succeed_seeds and self.steps < 5
        return {"reward": 1.0 if ok else 0.0, "milestones": 1.0 if ok else 0.3,
                "reason_code": "OK" if ok else "NOT_DONE", "failed": [] if ok else ["x"], "details": {}}

    async def close(self):
        self.closed = True


class FakePolicy:
    """Clicks once, emits an invalid action once, then done()."""

    def __init__(self, *a, **k) -> None:
        self.calls = 0

    async def act(self, obs):
        self.calls += 1
        i = obs.step
        if i == 0:
            return FakeAction({"type": "click", "x": 1, "y": 2, "button": "left"}), {"raw_action": "click(1, 2)", "tokens": {"in": 10, "out": 2}, "model_latency_s": 0.1}
        if i == 1:
            return None, {"raw_action": "fly(1, 2)", "note": "parse error", "tokens": {"in": 10, "out": 2}, "model_latency_s": 0.1}
        return FakeAction({"type": "done", "success": True}), {"raw_action": "done()", "tokens": {"in": 10, "out": 1}, "model_latency_s": 0.1}


def make_fake_env_factory(cfg):
    async def factory(family: str):
        return FakeEnv(family, cfg.split, succeed_seeds={100000, 100002})
    return factory


def test_run_eval_with_fakes():
    from train import eval as ev

    cfg = ev.EvalConfig(families=["resolve_denial", "reschedule_constrained"], split="heldout_seeds",
                        seeds=[100000, 100001, 100002, 100003], n_seeds=2, concurrency=3, tag="t")
    results: list[dict] = []
    summary = asyncio.run(ev.run_eval(cfg, make_fake_env_factory(cfg), lambda r: FakePolicy(), on_result=results.append))
    assert summary["n_episodes"] == 16 == len(results)
    assert summary["n_success"] == 8 and abs(summary["success_rate"] - 0.5) < 1e-9
    assert summary["success"]["low"] < 0.5 < summary["success"]["high"]
    assert summary["median_steps"] == 3 and abs(summary["invalid_action_rate"] - 1 / 3) < 1e-9
    assert summary["reason_codes"] == {"OK": 8, "NOT_DONE": 8}
    assert set(summary["per_family"]) == {"resolve_denial", "reschedule_constrained"}
    assert summary["per_repeat"]["0"]["n"] == 8 and summary["per_repeat"]["1"]["n"] == 8
    assert abs(summary["tokens_per_step"] - 35 / 3) < 1e-6
    r = next(x for x in results if x["seed"] == 100000 and x["family"] == "resolve_denial" and x["repeat"] == 0)
    assert r["success"] is True and r["task_id"] == "resolve_denial-heldout_seeds-100000" and r["steps"] == 3


def test_eval_cli_with_env_factory(tmp_path: Path):
    from train import eval as ev

    rc = ev.main(["--env-factory", "tests.test_train:make_fake_env_factory", "--policy", "tests.test_train:FakePolicy",
                  "--families", "resolve_denial", "--split", "heldout_seeds", "--seeds", "100000:100004",
                  "--n-seeds", "1", "--out-dir", str(tmp_path), "--tag", "fake", "--checkpoint", "ck"])
    assert rc == 0
    summary = json.loads((tmp_path / "fake" / "eval_summary.json").read_text())
    assert summary["n_episodes"] == 4 and summary["n_success"] == 2 and summary["checkpoint"] == "ck"
    lines = (tmp_path / "fake" / "episodes.jsonl").read_text().strip().splitlines()
    assert len(lines) == 4
    assert ev.parse_seeds("1,2,3", "train", 0) == [1, 2, 3]
    assert ev.parse_seeds("5-7", "train", 0) == [5, 6, 7]
    assert ev.parse_seeds(None, "heldout_compositions", 2) == [200000, 200001]


def test_eval_survives_env_errors():
    from train import eval as ev

    def bad_factory(cfg):
        async def factory(family):
            raise RuntimeError("no VM")
        return factory

    cfg = ev.EvalConfig(families=["f"], seeds=[1, 2], n_seeds=1, tag="e")
    summary = asyncio.run(ev.run_eval(cfg, bad_factory(cfg), lambda r: FakePolicy()))
    assert summary["n_episodes"] == 2 and summary["n_success"] == 0 and summary["n_errors"] == 2
    assert summary["reason_codes"] == {"EVAL_ERROR": 2}


# --------------------------------------------------------------------------- #
# bakeoff
# --------------------------------------------------------------------------- #

def test_bakeoff_markdown_and_nvidia_fallback(monkeypatch, tmp_path: Path, capsys):
    from train import bakeoff

    monkeypatch.setattr(bakeoff.shutil, "which", lambda name: None)
    assert bakeoff.read_nvidia_smi() == "n/a"
    rows = [{"name": "a", "model": "m|a", "prompt_style": "fara", "base_success": "50.0% [30.0%, 70.0%] n=20",
             "validity": "97.0%", "tokens_per_step": "1500", "latency_s": "1.20", "smoke": "ok (2 steps)",
             "s_per_step": "2.1", "vram": "n/a", "notes": ""}]
    md = bakeoff.render_markdown(rows, bakeoff.EXAMPLE_CONFIG)
    assert md.startswith("# Student bake-off") and "| a | m\\|a | fara |" in md and "Base success" in md

    cfg = dict(bakeoff.EXAMPLE_CONFIG)
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))
    rc = bakeoff.main(["--config", str(cfg_path), "--out-dir", str(tmp_path / "bo"), "--dry-run", "--only", "fara-1.5-4b"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "train.eval" in out and "train.train_lora" in out and "--smoke" in out
    assert (tmp_path / "bo" / "bakeoff.md").exists() and (tmp_path / "bo" / "bakeoff.json").exists()
    assert bakeoff.main(["--example-config"]) == 0


def test_make_sft_with_reasoning_and_v2_target():
    from train.make_sft import reasoning_from_raw
    from train.train_lora import target_text

    assert reasoning_from_raw('I will open the tab.  \nkey("ctrl+t")') == "I will open the tab."
    assert reasoning_from_raw('key("ctrl+t")') == ""
    assert reasoning_from_raw("The number is **AUTH-1**.\nSwitch tabs.\nclick(1, 2)") == "The number is **AUTH-1**. Switch tabs."
    rec = {"target": "click(640, 360)", "reasoning": "The number is AUTH-1."}
    out = target_text(rec, "fara")
    assert out.startswith("The number is AUTH-1.\n<tool_call>") and out.endswith("</tool_call>")
    assert target_text({"target": "click(640, 360)"}, "fara").startswith("<tool_call>")
    assert target_text(dict(rec, reasoning=""), "compact") == "click(640, 360)"
