"""scripts/exp1/package_release.py: the per-attempt evaluation table and the bundle (review 2, M6)."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

from forkloop.correction import Store

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts" / "exp1" / "package_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bundle_with_eval_attempts(tmp_path):
    pr = _load()
    store = Store(tmp_path / "s" / "forkloop.sqlite")
    pid = store.put_policy("student", {"name": "m"})
    from forkloop.world import load_world
    task = load_world("toy-counter").generate("reach_target", 1, "train")
    store.put_task(task)
    cell = "fam-final_test-000001/eval:m/r1"
    for n, status in ((1, "interrupted"), (2, "finished")):
        store.start_attempt(attempt_id=f"att-{n}", task_id="fam-final_test-000001", policy_id=pid, run_dir=str(tmp_path),
                            strategy="reset", experiment_id="exp1-eval", cell=cell,
                            info={"role": "eval:m", "attempt_no": n})
        store.finish_attempt(f"att-{n}", status=status, reward=1.0 if status == "finished" else None,
                             reason_code="OK" if status == "finished" else None, n_steps=4)
    store.annotate_attempt("att-1", void_reason="operator_stop")
    ds = tmp_path / "datasets" / "W"
    ds.mkdir(parents=True)
    (ds / "manifest.json").write_text(json.dumps({"dataset_id": "ds-x", "counts": {"records": 3}}))
    ad = tmp_path / "adapters" / "A1-s1" / "final"
    ad.mkdir(parents=True)
    (ad / "adapter_config.json").write_text("{}")

    class A:
        out = str(tmp_path / "rel"); store = [str(tmp_path / "s" / "forkloop.sqlite")]
        datasets = [str(tmp_path / "datasets" / "W")]; adapters = [str(tmp_path / "adapters")]; results = None
    pr.main(A)
    rel = tmp_path / "rel"
    rows = list(csv.DictReader((rel / "eval" / "cells.csv").open()))
    assert [(r["attempt_no"], r["status"], r["void_reason"], r["counted"]) for r in rows] == [
        ("1", "interrupted", "operator_stop", "0"), ("2", "finished", "", "1")]
    info = json.loads((rel / "release.json").read_text())
    assert "W" in info["datasets"] and "A1-s1" in info["adapters"] and info["void_not_included"]
    sums = (rel / "SHA256SUMS").read_text()
    assert "eval/cells.csv" in sums and "release.json" in sums
