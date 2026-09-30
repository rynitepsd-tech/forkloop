"""Assemble the exp1 release bundle (run on forkloop-main after ``report.py --final``).

    python scripts/exp1/package_release.py --out ~/programs/exp1/release \
        --store ~/programs/exp1/forkloop.sqlite --store <copy of aux's ~/programs/exp1aux/forkloop.sqlite> \
        --datasets ~/programs/exp1/datasets/W --datasets ~/programs/exp1/datasets/budget-v2 \
        --adapters ~/programs/exp1/adapters-v2 --adapters ~/programs/exp1/adapters/sw-seed0 \
        --results docs/results-exp1.json --results docs/results-exp1.md

Void material is never packaged: ``datasets/budget`` and the v1 runs in ``adapters/`` (protocol 2026-09-29
15:28) are listed in ``release.json`` under ``void_not_included``.

Layout of ``--out``:

  datasets/<name>.tar      every training dataset exactly as exported (records, images, manifest)
  adapters/<run>.tar       LoRA adapter (``final/``), training args and training log
  eval/cells.csv           one row per final-test *attempt*, including unscored and replaced ones,
                           with ``counted`` = the attempt the registered rule scores for its cell
  eval/<results files>     the generated report
  stores/<n>.sqlite        consistent copies of the program stores (attempts, checkpoints, repairs,
                           branches, charges, datasets); screenshots of trajectories are not included
  release.json             provenance (image digest, model revision, protocol commit, sizes)
  SHA256SUMS               sha256 of every file above

All content is synthetic (generated patients, claims, payers).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tarfile
import time
from pathlib import Path

from forkloop.correction import Store
from forkloop.correction.store import FINISHED

IMAGE = "forkloop/claims-ops-v1:3@sha256:7324af036519fd11dcedff3af257aca04f67ef9e3a6ed9026076d4675c51b0d7"
MODEL = "Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
PROTOCOL = "docs/protocol-learning-experiment.md (registered fe41105; deviations appended)"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tar_dir(src: Path, dst: Path, arcname: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dst, "w") as t:
        t.add(src, arcname=arcname)


def copy_store(src: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    with d:
        s.backup(d)
    s.close()
    d.close()


def cells_csv(stores: list[Store], out: Path, experiment_id: str = "exp1-eval") -> int:
    rows = []
    for st in stores:
        for a in st.attempts(experiment_id=experiment_id):
            role = a["info"].get("role", "")
            if role.startswith("eval:") and "-final_test-" in a["task_id"]:
                rows.append(a)
    by_cell: dict[str, list[dict]] = {}
    for a in rows:
        by_cell.setdefault(a["cell"], []).append(a)
    counted = set()
    for atts in by_cell.values():
        atts.sort(key=lambda a: a["started_at"])
        scored = [a for a in atts if a["status"] == FINISHED]
        counted.add((scored[0] if scored else atts[-1])["attempt_id"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "task_id", "family", "cell", "attempt_no", "status", "reward", "reason_code",
                    "steps", "wall_s", "end_reason", "void_reason", "counted", "attempt_id"])
        for a in sorted(rows, key=lambda a: (a["info"]["role"], a["task_id"], a["started_at"])):
            w.writerow([a["info"]["role"][len("eval:"):], a["task_id"], a["task_id"].rsplit("-", 2)[0], a["cell"],
                        a["info"].get("attempt_no"), a["status"], a["reward"], a["reason_code"], a["n_steps"],
                        a["info"].get("wall_s"), a["info"].get("end_reason"), a["info"].get("void_reason"),
                        int(a["attempt_id"] in counted), a["attempt_id"]])
    return len(rows)


def main(a: argparse.Namespace) -> None:
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    info: dict = {"created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "world_image": IMAGE,
                  "student_base": MODEL, "protocol": PROTOCOL, "datasets": {}, "adapters": {}, "stores": []}
    info["void_not_included"] = ["datasets/budget (first matched-cost datasets)", "adapters/A?-s? v1 training runs",
                                 "experiment exp1-final (pre-review A0 run)"]
    for root in (Path(x).expanduser() for x in a.datasets):
        for d in sorted(root.rglob("manifest.json")):
            _dataset(d, root, out, info)
    for root in (Path(x).expanduser() for x in a.adapters):
        for run in ([root] if (root / "final").is_dir() else sorted(p for p in root.iterdir() if (p / "final").is_dir())):
            tar_dir(run, out / "adapters" / f"{run.name}.tar", run.name)
            info["adapters"][run.name] = {"files": sorted(str(p.relative_to(run)) for p in run.rglob("*") if p.is_file())}
    _finish(a, out, info)


def _dataset(d: Path, root: Path, out: Path, info: dict) -> None:
    ds = d.parent
    name = "__".join([root.name, *ds.relative_to(root).parts]) if ds != root else root.name
    tar_dir(ds, out / "datasets" / f"{name}.tar", name)
    m = json.loads(d.read_text())
    info["datasets"][name] = {"dataset_id": m.get("dataset_id"), "records": m.get("counts", {}).get("records"),
                              "manifest_sha256": sha256(d)}


def _finish(a: argparse.Namespace, out: Path, info: dict) -> None:
    stores = [Store(p) for p in a.store]
    for i, p in enumerate(a.store):
        copy_store(p, out / "stores" / f"store{i}.sqlite")
        info["stores"].append({"file": f"stores/store{i}.sqlite", "source": p})
    info["eval_attempt_rows"] = cells_csv(stores, out / "eval" / "cells.csv")
    for r in a.results or []:
        (out / "eval" / Path(r).name).write_bytes(Path(r).read_bytes())
    (out / "release.json").write_text(json.dumps(info, indent=2))
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.relative_to(out)}\n" for p in files))
    total = sum(p.stat().st_size for p in files)
    print(f"{out}: {len(files)} files, {total / 1e9:.2f} GB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", action="append", required=True)
    ap.add_argument("--datasets", action="append", required=True, help="dataset dir or dir of datasets (repeatable)")
    ap.add_argument("--adapters", action="append", required=True, help="run dir or dir of runs (repeatable)")
    ap.add_argument("--results", action="append")
    main(ap.parse_args())
