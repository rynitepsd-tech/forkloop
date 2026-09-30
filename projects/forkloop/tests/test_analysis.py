"""Paired analysis: family-balanced success, pairwise exclusion of unscored cells, bootstrap CI, sign test."""
from forkloop.correction.analysis import arm_success, outcomes, paired, sign_test
from forkloop.correction.store import Store


class T:
    def __init__(self, fam, seed):
        self.task_id, self.world, self.family, self.split, self.seed = f"{fam}-final_test-{seed:06d}", "w", fam, "final_test", seed

    def to_dict(self):
        return {"task_id": self.task_id}


def test_paired_analysis(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    pid = s.put_policy("eval", {"x": 1})
    arms = {"base": ("A0", 0), "fl-s1": ("A2", 1), "fl-s2": ("A2", 2)}
    n = 0
    for fam in ("f1", "f2"):
        for seed in range(10):
            t = T(fam, seed)
            s.put_task(t)
            for label, (arm, run) in arms.items():
                n += 1
                aid = f"a{n}"
                s.start_attempt(attempt_id=aid, task_id=t.task_id, policy_id=pid, run_dir="x", strategy="replay",
                                experiment_id="e", cell=f"{t.task_id}/eval:{label}/r1", info={"role": f"eval:{label}"})
                if fam == "f2" and seed == 0 and label == "fl-s2":
                    s.finish_attempt(aid, status="infra_error", reward=None, reason_code=None, n_steps=3)
                    continue
                win = (arm == "A2" and seed < 6) or (arm == "A0" and seed < 2)
                s.finish_attempt(aid, status="finished", reward=1.0 if win else 0.0,
                                 reason_code="OK" if win else "NOT_DONE", n_steps=10)
    res = outcomes(s, "e", arms)
    a0, a2 = arm_success(res, "A0"), arm_success(res, "A2")
    assert a0["success_balanced"] == 0.2 and a2["unscored_cells"] == 1 and a2["scored_tasks"] == 19
    p = paired(res, "A2", "A0", n_boot=500)
    assert p["tasks"] == 19 and p["tasks_a_better"] == 8 and p["ci95"][0] > 0
    assert sign_test(7, 0) < 0.05 and sign_test(3, 3) == 1.0


def test_outcome_sensitivity_options():
    from forkloop.correction.analysis import outcomes

    class _S:
        def __init__(self, rows): self.rows = rows
        def attempts(self, **w): return [r for r in self.rows if r["experiment_id"] == w["experiment_id"]]

    def att(i, cell, status, reward, t, err=""):
        return {"attempt_id": f"a{i}", "experiment_id": "ev", "cell": cell, "task_id": cell.split("/")[0],
                "status": status, "reward": reward, "reason_code": "OK" if reward else None, "n_steps": 3,
                "started_at": t, "run_dir": "", "info": {"role": "eval:m", "error": err}}
    rows = [att(1, "fam-final_test-000001/eval:m/r1", "infra_error", None, 1, "key"),
            att(2, "fam-final_test-000001/eval:m/r1", "finished", 1.0, 2),
            att(3, "fam-final_test-000002/eval:m/r1", "infra_error", None, 1)]
    arms = {"m": ("M", 1)}
    base = outcomes(_S(rows), "ev", arms)["table"]["M"][1]
    assert base == {"fam-final_test-000001": 1, "fam-final_test-000002": None}
    assert outcomes(_S(rows), "ev", arms, unscored_as_failure=True)["table"]["M"][1]["fam-final_test-000002"] == 0
    pf = outcomes(_S(rows), "ev", arms, policy_failure=lambda a: a["info"]["error"] == "key")
    assert pf["table"]["M"][1]["fam-final_test-000001"] == 0            # the key failure came first: it counts
    assert pf["meta"][("M", 1, "fam-final_test-000001")]["reason"] == "POLICY_BACKEND_FAILURE"
