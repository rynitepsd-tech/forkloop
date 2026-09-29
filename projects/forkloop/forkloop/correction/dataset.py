"""Export verified experience as immutable, provenance-preserving datasets (``forkloop dataset``).

Three record kinds, in separate files, never mixed:

* ``records.jsonl`` — **action demonstrations**: one record per step of a *verified* path, with
  the exact input the acting policy saw (instruction, its action history, its explicit memory,
  previous/current screenshot of that same path) and the teacher's visible reply as target.
  Sources: correction suffixes of verified repair branches (``origin: correction_suffix``) and
  verified teacher attempts from the initial state (``origin: initial_state_demo``).
* ``preferences.jsonl`` — **preference pairs** at a restart point: the first step of a verified
  branch (chosen) against the first step of a failed branch, or the failed attempt's own action
  there (rejected). Labelled; never used as demonstrations.
* ``diagnostics.jsonl`` — **controller-only labels** (restart points, reason codes, evidence).
  They may reference hidden task facts and must never be rendered into a policy input.

Screenshots are copied content-addressed into ``images/``. ``manifest.json`` records the sha256
of every file, every source trajectory's files, the selection rules, split membership, the
information-flow audit, the code version, and the dataset id (``ds-`` + records sha256 prefix).
Failed branches never enter ``records.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from ..actions import Action
from ..policies import action_parse as ap
from ..policies.student import _MEMORY_LINE_RE, extend_memory, memory_from_reply
from ..trajectories import _git_sha, load_episode
from .store import FINISHED, Store, file_sha256

SCHEMA = "forkloop.dataset.v1"
FORBIDDEN_SPLITS_DEFAULT = ("final_test", "heldout_final", "test")


def _refuse_final(task: Any, forbidden: set) -> None:
    """Final-test tasks never enter a dataset: by split name, sealed seed block or held-out structure."""
    if task.split in forbidden:
        raise ValueError(f"refusing to export {task.task_id}: split {task.split!r} is reserved for evaluation")
    if task.world == "claims-ops-v1":
        from ..splits import final_reasons
        why = final_reasons(task)
        if why:
            raise ValueError(f"refusing to export {task.task_id}: final-test task ({why})")


def reasoning_text(raw: str) -> str:
    """The visible reasoning of a reply: everything before the action, without Memory lines."""
    thoughts = ap.extract_thoughts(raw or "") or ""
    thoughts = _MEMORY_LINE_RE.sub("", thoughts)
    return " ".join(thoughts.split())


def render_target(target: dict[str, Any], *, screen: tuple[int, int], coords: tuple[int, int]) -> str:
    """Target text in the student's coordinate frame: reasoning, Memory lines, compact action."""
    action = ap.scale_coords(dict(target["action"]), screen, coords)
    lines = []
    if target.get("reasoning"):
        lines.append(target["reasoning"])
    lines += [f"Memory: {m}" for m in target.get("memory_written", [])]
    lines.append(ap.to_compact(action))
    return "\n".join(lines)


def _history_item(step: dict[str, Any]) -> str:
    if step.get("action"):
        return Action.parse(step["action"]).to_compact()
    return step.get("raw_action", "")


def _hidden_values(task: Any) -> list[str]:
    out = []
    for k, v in (task.expected or {}).items():
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and len(x) >= 5 and re.search(r"\d", x) and x not in task.instruction:
                out.append(x)
    return out


class _Images:
    def __init__(self, root: Path) -> None:
        self.dir = root / "images"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def add(self, src: Optional[Path]) -> Optional[dict[str, str]]:
        if src is None or not src.exists():
            return None
        sha = file_sha256(src)
        dst = self.dir / f"{sha}.png"
        if not dst.exists():
            shutil.copyfile(src, dst)
            self.n += 1
        return {"path": f"images/{sha}.png", "sha256": sha}


def _step_shot(ep_dir: Path, steps: list[dict], i: int) -> Optional[Path]:
    for s in steps:
        if s["i"] == i and s.get("shot_before"):
            return ep_dir / s["shot_before"]
    return None


def _path_records(*, task: Any, path_steps: list[dict], path_dir: Path, prefix_steps: list[dict], prefix_dir: Optional[Path],
                  start_history: list[str], origin: str, source: dict[str, Any], images: _Images,
                  hidden: list[str], screen: tuple[int, int]) -> tuple[list[dict], dict[str, int]]:
    """Records for the policy-acted steps of one verified path (replayed prefix steps excluded)."""
    audit = {"memory_provenance_ok": 0, "memory_provenance_bad": 0, "hidden_in_text_input": 0,
             "type_target_needs_screen": 0, "type_target_from_memory": 0}
    acted = [s for s in path_steps if not (s.get("search") or {}).get("replayed")]
    history = list(start_history)
    memory_fold: Optional[list[str]] = None
    out = []
    for s in acted:
        i = s["i"]
        mem_before = list((s.get("agent") or {}).get("memory_before", []))
        if memory_fold is None:
            memory_fold = mem_before
        ok = mem_before == memory_fold
        audit["memory_provenance_ok" if ok else "memory_provenance_bad"] += 1
        cur = images.add(path_dir / s["shot_before"]) if s.get("shot_before") else None
        prev_src = _step_shot(path_dir, path_steps, i - 1) or (_step_shot(prefix_dir, prefix_steps, i - 1) if prefix_dir else None)
        prev = images.add(prev_src) if i > 0 else None
        raw = s.get("raw_action", "")
        written = list((s.get("agent") or {}).get("memory_written", []) or memory_from_reply(raw))
        text_in = " ".join([task.instruction, *history, *mem_before])
        leaks = [h for h in hidden if h in text_in]
        audit["hidden_in_text_input"] += bool(leaks)
        rec_audit = {"memory_provenance_ok": ok, "hidden_values_in_text_input": len(leaks)}
        if s.get("action") and s["action"].get("type") == "type":
            typed = s["action"].get("text", "")
            needs = [h for h in hidden if h in typed]
            if needs:
                from_mem = all(any(h in m for m in mem_before) for h in needs)
                audit["type_target_from_memory" if from_mem else "type_target_needs_screen"] += 1
                rec_audit["type_value_source"] = "memory" if from_mem else "current_screen (unverified)"
        if s.get("action") and s.get("valid", True) and cur is not None:
            out.append({
                "schema": SCHEMA, "kind": "action_demonstration", "origin": origin,
                "record_id": hashlib.sha256(f"{source['path_id']}:{i}".encode()).hexdigest()[:16],
                "input": {"instruction": task.instruction, "history": list(history), "memory": mem_before,
                          "step": i, "screen": list(screen), "current_shot": cur, "previous_shot": prev},
                "target": {"reasoning": reasoning_text(raw), "memory_written": written, "action": s["action"],
                           "raw_reply": raw},
                "source": {**source, "step": i},
                "audit": rec_audit,
            })
        history.append(_history_item(s))
        memory_fold = extend_memory(memory_fold, written)
    return out, audit


def export_dataset(store: Store, world: Any, out_dir: str | Path, *, experiment_id: Optional[str] = None,
                   include_corrections: bool = True, include_demos: bool = True,
                   repair_ids: Optional[Iterable[str]] = None, attempt_ids: Optional[Iterable[str]] = None,
                   forbidden_splits: Iterable[str] = FORBIDDEN_SPLITS_DEFAULT, name: Optional[str] = None) -> dict[str, Any]:
    from .repair import task_for

    out = Path(out_dir)
    tmp = out.with_name(out.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    images = _Images(tmp)
    screen = tuple(world.size)
    forbidden = set(forbidden_splits)
    records, prefs, diags, sources = [], [], [], []
    audit_total: dict[str, int] = {}

    def add_audit(a: dict[str, int]) -> None:
        for k, v in a.items():
            audit_total[k] = audit_total.get(k, 0) + v

    def source_files(d: Path) -> dict[str, str]:
        return {n: file_sha256(d / n) for n in ("manifest.json", "steps.jsonl", "verdict.json") if (d / n).exists()}

    where = {"experiment_id": experiment_id} if experiment_id else {}
    if include_corrections:
        repairs = store.repairs(**where)
        if repair_ids is not None:
            keep = set(repair_ids)
            repairs = [r for r in repairs if r["repair_id"] in keep]
        for rep in repairs:
            attempt = store.attempt(rep["attempt_id"])
            task = task_for(store, attempt["task_id"], world)
            _refuse_final(task, forbidden)
            hidden = _hidden_values(task)
            att_dir = Path(attempt["run_dir"])
            att_ep = load_episode(att_dir)
            branches = store.branches(repair_id=rep["repair_id"])
            diags.append({"kind": "controller_diagnostic", "repair_id": rep["repair_id"], "attempt_id": attempt["attempt_id"],
                          "task_id": task.task_id, "attempt_reason": attempt["reason_code"],
                          "restart_points": rep["config"].get("restart_points", []),
                          "branches": [{k: b[k] for k in ("branch_id", "ckpt_id", "idx", "status", "reward", "reason_code")}
                                       for b in branches]})
            by_ckpt: dict[str, list[dict]] = {}
            for b in branches:
                by_ckpt.setdefault(b["ckpt_id"], []).append(b)
            for ckpt_id, bs in by_ckpt.items():
                ckpt = store.checkpoint(ckpt_id)
                good = [b for b in bs if b["status"] == FINISHED and (b["reward"] or 0) >= 1.0]
                bad = [b for b in bs if b["status"] == FINISHED and (b["reward"] or 0) < 1.0]
                first_steps = {}
                for b in bs:
                    bdir = Path(b["run_dir"])
                    if not (bdir / "steps.jsonl").exists():
                        continue
                    bep = load_episode(bdir)
                    acted = [s for s in bep["steps"] if not (s.get("search") or {}).get("replayed")]
                    if acted:
                        first_steps[b["branch_id"]] = (bdir, bep, acted[0])
                for b in good:
                    bdir = Path(b["run_dir"])
                    bep = load_episode(bdir)
                    src = {"path_id": b["branch_id"], "branch_id": b["branch_id"], "repair_id": rep["repair_id"],
                           "attempt_id": attempt["attempt_id"], "ckpt_id": ckpt_id, "ckpt_step": ckpt["step"],
                           "ckpt_strategy": ckpt["strategy"], "task_id": task.task_id, "family": task.family,
                           "split": task.split, "seed": task.seed, "repair_mode": rep["mode"]}
                    recs, a = _path_records(task=task, path_steps=bep["steps"], path_dir=bdir, prefix_steps=att_ep["steps"],
                                            prefix_dir=att_dir, start_history=list(ckpt["history"]),
                                            origin="correction_suffix" if ckpt["step"] > 0 else "restart_demo",
                                            source=src, images=images, hidden=hidden, screen=screen)
                    records += recs
                    add_audit(a)
                    sources.append({"kind": "branch", "id": b["branch_id"], "task_id": task.task_id, "split": task.split,
                                    "files": source_files(bdir), "prefix_attempt": attempt["attempt_id"],
                                    "prefix_files": source_files(att_dir), "ckpt_step": ckpt["step"]})
                # preference pairs: verified vs failed first steps, and vs the failed attempt's own step
                for g in good:
                    if g["branch_id"] not in first_steps:
                        continue
                    gdir, gep, gstep = first_steps[g["branch_id"]]
                    rejected = []
                    for b in bad:
                        if b["branch_id"] in first_steps:
                            _, _, bstep = first_steps[b["branch_id"]]
                            rejected.append(("failed_branch", b["branch_id"], bstep))
                    orig = next((s for s in att_ep["steps"] if s["i"] == ckpt["step"]), None)
                    if orig is not None:
                        rejected.append(("failed_attempt_step", attempt["attempt_id"], orig))
                    for rkind, rid, rstep in rejected:
                        if (rstep.get("action") or {}) == (gstep.get("action") or {}):
                            continue
                        prefs.append({"schema": SCHEMA, "kind": "preference_pair", "ckpt_id": ckpt_id,
                                      "ckpt_step": ckpt["step"], "task_id": task.task_id, "split": task.split,
                                      "input_from": g["branch_id"],
                                      "chosen": {"branch_id": g["branch_id"], "action": gstep.get("action"),
                                                 "raw_reply": gstep.get("raw_action", "")},
                                      "rejected": {"kind": rkind, "id": rid, "action": rstep.get("action"),
                                                   "raw_reply": rstep.get("raw_action", "")}})
    if include_demos:
        atts = [a for a in store.attempts(**where) if a["status"] == FINISHED and (a["reward"] or 0) >= 1.0
                and a["info"].get("role") == "teacher"]
        if attempt_ids is not None:
            keep = set(attempt_ids)
            atts = [a for a in atts if a["attempt_id"] in keep]
        for att in atts:
            task = task_for(store, att["task_id"], world)
            _refuse_final(task, forbidden)
            adir = Path(att["run_dir"])
            aep = load_episode(adir)
            src = {"path_id": att["attempt_id"], "attempt_id": att["attempt_id"], "task_id": task.task_id,
                   "family": task.family, "split": task.split, "seed": task.seed}
            recs, a = _path_records(task=task, path_steps=aep["steps"], path_dir=adir, prefix_steps=[], prefix_dir=None,
                                    start_history=[], origin="initial_state_demo", source=src, images=images,
                                    hidden=_hidden_values(task), screen=screen)
            records += recs
            add_audit(a)
            sources.append({"kind": "attempt", "id": att["attempt_id"], "task_id": task.task_id, "split": task.split,
                            "files": source_files(adir)})

    def write_jsonl(name: str, rows: list[dict]) -> str:
        p = tmp / name
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
        return file_sha256(p)

    files = {"records.jsonl": write_jsonl("records.jsonl", records),
             "preferences.jsonl": write_jsonl("preferences.jsonl", prefs),
             "diagnostics.jsonl": write_jsonl("diagnostics.jsonl", diags)}
    dataset_id = "ds-" + files["records.jsonl"][:12]
    splits = sorted({s["split"] for s in sources})
    manifest = {
        "schema": SCHEMA, "dataset_id": dataset_id, "name": name, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code_git_sha": _git_sha(), "world": world.name, "experiment_id": experiment_id,
        "selection": {"include_corrections": include_corrections, "include_demos": include_demos,
                      "repair_ids": sorted(repair_ids) if repair_ids is not None else None,
                      "attempt_ids": sorted(attempt_ids) if attempt_ids is not None else None,
                      "verified_only": True, "forbidden_splits": sorted(forbidden)},
        "counts": {"records": len(records), "preferences": len(prefs), "diagnostics": len(diags),
                   "sources": len(sources), "images": len(list(images.dir.glob("*.png"))),
                   "by_origin": {o: sum(1 for r in records if r["origin"] == o) for o in sorted({r["origin"] for r in records})}},
        "splits": splits, "files": files, "sources": sources, "audit": audit_total,
        "notes": ["records.jsonl holds only steps of verified paths; failed branches appear only in preferences/diagnostics",
                  "diagnostics.jsonl is controller-only and must never be rendered into a policy input",
                  "type_target_needs_screen counts typed hidden values not present in memory: the value had to be read from the current screenshot"],
    }
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    if out.exists():
        existing = json.loads((out / "manifest.json").read_text()) if (out / "manifest.json").exists() else {}
        if existing.get("files", {}).get("records.jsonl") != files["records.jsonl"]:
            raise FileExistsError(f"{out} exists with different content; datasets are immutable (choose a new directory)")
        shutil.rmtree(tmp)
    else:
        tmp.rename(out)
    for p in out.rglob("*"):
        if p.is_file():
            p.chmod(0o444)
    store.put_dataset(dataset_id=dataset_id, kind="sft+preferences", path=str(out), sha256=files["records.jsonl"],
                      n_records=len(records), manifest={k: manifest[k] for k in ("counts", "splits", "files", "selection")})
    return manifest


__all__ = ["export_dataset", "render_target", "reasoning_text", "SCHEMA"]
