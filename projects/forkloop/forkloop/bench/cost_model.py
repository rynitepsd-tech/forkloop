"""Cost model for forkloop — pure functions over the verified Solari price list.

Prices: https://docs.getsolari.com/pricing (read 2026-09-01). Anything that is
not on that page is a parameter with a default of 0 and a note, never a
hard-coded guess. `python -m forkloop.bench.cost_model` prints the tables
that docs/cost.md embeds.
"""

from __future__ import annotations

from typing import Iterable

PRICING_URL = "https://docs.getsolari.com/pricing"

#: Plan facts. Pro's included credits are not stated on the pricing page → None.
PLANS: dict[str, dict] = {
    "free": {"monthly_usd": 0.0, "credits_usd": 3.0, "concurrent_vms": 1},
    "starter": {"monthly_usd": 20.0, "credits_usd": 20.0, "concurrent_vms": 2, "concurrent_browsers": 20},
    "pro": {"monthly_usd": 200.0, "credits_usd": None, "concurrent_vms": 10},
}

#: Compute, USD per hour, keyed by (vCPU, GB RAM) then plan.
COMPUTE_USD_PER_HOUR: dict[tuple[int, int], dict[str, float]] = {
    (1, 2): {"starter": 0.057, "pro": 0.040, "free": 0.086},
    (2, 4): {"starter": 0.114, "pro": 0.080, "free": 0.171},
    (4, 8): {"starter": 0.228, "pro": 0.160, "free": 0.342},
}

#: VMs (desktops) add this per hour for the live screen (VNC stream).
SCREEN_USD_PER_HOUR = 0.02

#: Snapshot storage, read off the Solari pricing page on 2026-09-04: the first 10 GB are free,
#: every further GB is $0.05 per GB-month.
SNAPSHOT_STORAGE_USD_PER_GB_MONTH = 0.05
SNAPSHOT_STORAGE_FREE_GB = 10.0

VM_SIZES: dict[str, tuple[int, int]] = {"small": (1, 2), "medium": (2, 4), "large": (4, 8)}

#: Reset methods. `local` runs on hardware you already own → $0 of Solari
#: compute. The others all burn VM-hours for the seconds the reset takes.
RESET_METHODS = ("revert", "from_snapshot", "rebuild", "local")


def _plan(plan: str) -> str:
    p = plan.lower()
    if p not in PLANS:
        raise ValueError(f"unknown plan {plan!r}; one of {sorted(PLANS)}")
    return p


def _size(vm_size: str | tuple[int, int]) -> tuple[int, int]:
    if isinstance(vm_size, str):
        if vm_size not in VM_SIZES:
            raise ValueError(f"unknown vm_size {vm_size!r}; one of {sorted(VM_SIZES)}")
        return VM_SIZES[vm_size]
    return (int(vm_size[0]), int(vm_size[1]))


def vm_hour_cost(plan: str, vcpu: int, mem_gb: int, screen: bool = True) -> float:
    """USD per hour for one desktop/sandbox of the given shape on `plan`."""
    key = (int(vcpu), int(mem_gb))
    if key not in COMPUTE_USD_PER_HOUR:
        raise ValueError(f"no published price for {vcpu} vCPU / {mem_gb} GB; sizes: {sorted(COMPUTE_USD_PER_HOUR)}")
    return COMPUTE_USD_PER_HOUR[key][_plan(plan)] + (SCREEN_USD_PER_HOUR if screen else 0.0)


def vm_hours_per_credit(plan: str, vcpu: int = 2, mem_gb: int = 4, screen: bool = True,
                        credits_usd: float | None = None) -> float:
    """How many VM-hours the plan's included credits (or `credits_usd`) buy."""
    credits = PLANS[_plan(plan)]["credits_usd"] if credits_usd is None else credits_usd
    if credits is None:
        raise ValueError(f"included credits for plan {plan!r} are not published; pass credits_usd")
    return credits / vm_hour_cost(plan, vcpu, mem_gb, screen)


def cost_per_1k_resets(method: str, plan: str, seconds_per_reset: float,
                       vm_size: str | tuple[int, int] = "medium", overhead_seconds: float = 0.0,
                       screen: bool = True) -> float:
    """USD to perform 1,000 resets. `seconds_per_reset` is the measured
    wall-clock (revert → healthy → screen stable, or fork boot → healthy);
    `overhead_seconds` is any per-reset controller work that keeps the VM
    billed (seeding, health checks, baseline checksums)."""
    if method not in RESET_METHODS:
        raise ValueError(f"unknown method {method!r}; one of {RESET_METHODS}")
    if seconds_per_reset < 0 or overhead_seconds < 0:
        raise ValueError("seconds must be >= 0")
    if method == "local":
        return 0.0
    vcpu, mem_gb = _size(vm_size)
    hours = 1000.0 * (seconds_per_reset + overhead_seconds) / 3600.0
    return hours * vm_hour_cost(plan, vcpu, mem_gb, screen)


def episode_cost(plan: str, vm_size: str | tuple[int, int], seconds: float,
                 teacher_tokens_in: int, teacher_tokens_out: int,
                 teacher_price_in_per_M: float, teacher_price_out_per_M: float,
                 screen: bool = True) -> dict[str, float]:
    """USD for one episode: VM time + teacher-model tokens. Returns
    {"env", "teacher", "total"}. Token prices are inputs (the teacher's price
    list is outside this model's verified scope)."""
    vcpu, mem_gb = _size(vm_size)
    env = seconds / 3600.0 * vm_hour_cost(plan, vcpu, mem_gb, screen)
    teacher = (teacher_tokens_in * teacher_price_in_per_M + teacher_tokens_out * teacher_price_out_per_M) / 1e6
    return {"env": env, "teacher": teacher, "total": env + teacher}


def snapshot_storage_cost(gb: float, months: float = 1.0,
                          usd_per_gb_month: float = SNAPSHOT_STORAGE_USD_PER_GB_MONTH,
                          free_gb: float = SNAPSHOT_STORAGE_FREE_GB) -> float:
    """Snapshot storage: the first ``free_gb`` are free, the rest is billed per GB-month."""
    return max(0.0, gb - free_gb) * months * usd_per_gb_month


def budget_table(solari_promo_month: bool = True) -> list[dict]:
    """The §10 all-in budget for the whole build. Every row is an ESTIMATE
    except the Solari plan price; ranges are the plan's, and the total is the
    sum of the ranges (80–350)."""
    starter = PLANS["starter"]
    vm_hours = vm_hours_per_credit("starter", 2, 4)
    env_low = 0.0 if solari_promo_month else starter["monthly_usd"]
    rows = [
        {"item": "environment (Solari)", "low_usd": env_low, "high_usd": 180.0,
         "basis": (f"Starter ${starter['monthly_usd']:.0f}/mo (month-1 promo $0) ≈ {vm_hours:.0f} VM-hours "
                   f"at 2vCPU/4GB+screen; high = Starter + ~$160 credit top-ups (or Pro) — estimate")},
        {"item": "teacher API", "low_usd": 30.0, "high_usd": 80.0,
         "basis": "teacher-policy tokens for SFT data + eval — estimate"},
        {"item": "GPU rental", "low_usd": 30.0, "high_usd": 80.0,
         "basis": "LoRA fine-tune of the student — estimate"},
        {"item": "local box", "low_usd": 0.0, "high_usd": 10.0,
         "basis": "electricity / Docker baseline runs — estimate"},
        {"item": "snapshot storage", "low_usd": round(snapshot_storage_cost(40.0, 1.0), 2),
         "high_usd": round(snapshot_storage_cost(70.0, 2.0), 2),
         "basis": "10 GB free then $0.05/GB-month (pricing page, 2026-09-04): 40 GB for a month to 70 GB for two"},
    ]
    rows.append({"item": "total", "low_usd": sum(r["low_usd"] for r in rows),
                 "high_usd": sum(r["high_usd"] for r in rows), "basis": "sum of the rows above — estimate"})
    return rows


# --- markdown output ------------------------------------------------------------------


def _md(headers: Iterable[str], rows: Iterable[Iterable]) -> str:
    h = list(headers)
    out = ["| " + " | ".join(h) + " |", "| " + " | ".join("---" for _ in h) + " |"]
    for r in rows:
        out.append("| " + " | ".join(_cell(c) for c in r) + " |")
    return "\n".join(out)


def _cell(c) -> str:
    return f"{c:.3f}" if isinstance(c, float) else str(c)


def main() -> None:
    print(f"Source: {PRICING_URL} (Sept 2026)\n")
    print("### VM price per hour (compute + $0.02 screen)\n")
    print(_md(["shape", "starter", "pro", "free"],
              [[f"{c}vCPU/{m}GB", vm_hour_cost("starter", c, m), vm_hour_cost("pro", c, m), vm_hour_cost("free", c, m)]
               for (c, m) in sorted(COMPUTE_USD_PER_HOUR)]))
    print("\n### VM-hours per plan's included credits (2vCPU/4GB + screen)\n")
    rows = []
    for p, info in PLANS.items():
        if info["credits_usd"] is None:
            rows.append([p, "not published", "n/a", info["concurrent_vms"]])
        else:
            rows.append([p, info["credits_usd"], round(vm_hours_per_credit(p)), info["concurrent_vms"]])
    print(_md(["plan", "credits $", "VM-hours", "concurrent VMs"], rows))
    print("\n### USD per 1,000 resets (Starter, 2vCPU/4GB, no overhead)\n")
    secs = [2, 5, 10, 20, 30, 60, 120, 300]
    print(_md(["seconds/reset", *[str(s) for s in secs]],
              [["revert", *[cost_per_1k_resets("revert", "starter", s) for s in secs]],
               ["from_snapshot", *[cost_per_1k_resets("from_snapshot", "starter", s) for s in secs]],
               ["rebuild", *[cost_per_1k_resets("rebuild", "starter", s) for s in secs]],
               ["local", *[cost_per_1k_resets("local", "starter", s) for s in secs]]]))
    print("\n### All-in budget (estimates except the plan price)\n")
    print(_md(["item", "low $", "high $", "basis"],
              [[r["item"], r["low_usd"], r["high_usd"], r["basis"]] for r in budget_table()]))


if __name__ == "__main__":
    main()
