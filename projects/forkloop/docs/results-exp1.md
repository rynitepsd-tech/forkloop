# exp1 results (generated)

Source: `scripts/exp1/report.py` over /home/ubuntu/programs/exp1/forkloop.sqlite, /home/ubuntu/programs/exp1/aux-copy/forkloop.sqlite.

## Collection

- **exp1-warmstart**: `{"compose_claims": {"failed": 9, "unscored": 2, "verified": 9}, "reschedule_constrained": {"failed": 19, "unscored": 1}, "resolve_denial": {"failed": 5, "verified": 15}, "update_insurance_reconcile": {"verified": 18, "failed": 2}}`
- **exp1-demos**: `{"compose_claims": {"failed": 27, "verified": 13, "unscored": 3}, "reschedule_constrained": {"failed": 40, "unscored": 3}, "resolve_denial": {"verified": 34, "failed": 6}, "update_insurance_reconcile": {"verified": 31, "unscored": 3, "failed": 9}}`
- **exp1-round1**: `{"compose_claims": {"unscored": 40, "failed": 36, "verified": 4}, "reschedule_constrained": {"unscored": 33, "failed": 40}, "resolve_denial": {"unscored": 26, "verified": 29, "failed": 11}, "update_insurance_reconcile": {"unscored": 33, "verified": 6, "failed": 34}}`
- **repairs:checkpoint**: `{"counted_by_family": {"compose_claims": {"verified": 7, "void_repairs": 29, "pending": 24, "unrepaired": 5}, "reschedule_constrained": {"unrepaired": 11, "void_repairs": 41, "pending": 27, "exhausted": 2}, "resolve_denial": {"verified": 8, "void_repairs": 4, "pending": 3}, "update_insurance_reconcile": {"exhausted": 2, "void_repairs": 22, "verified": 16, "pending": 16}}, "branches_counted_repairs": {"finished": 136, "verified": 65}, "branches_all": {"restore_failed": 197, "finished": 427, "infra_error": 920, "verified": 89, "interrupted": 82}, "repairs_all": 382}`
- **repairs:full_restart**: `{"counted_by_family": {"compose_claims": {"unrepaired": 15, "void_repairs": 18, "verified": 4, "pending": 17}, "reschedule_constrained": {"unrepaired": 36, "void_repairs": 12, "pending": 4}, "resolve_denial": {"verified": 8, "void_repairs": 5, "pending": 2, "unrepaired": 1}, "update_insurance_reconcile": {"verified": 11, "void_repairs": 25, "unrepaired": 5, "pending": 18}}, "branches_counted_repairs": {"finished": 190, "verified": 50}, "branches_all": {"finished": 246, "infra_error": 627, "verified": 75, "restore_failed": 25, "interrupted": 11}, "repairs_all": 328}`

## Matched collection cost

Budget B = $27.27 (smallest arm total). Rates: `{"world_usd_per_hour": 0.35, "student_usd_per_step": 0.001, "note": "main box $22.32/h / 64 worlds; 7 A100 replicas \u2248 $19.53/h at \u2248 6 steps/s"}`

| dataset | units | verified paths | records | cost $ | teacher $ | world h | student steps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A1-b025 | 40 | 19 | 986 | 6.80 | 3.40 | 9.70 | 0 |
| A1-b050 | 80 | 38 | 1846 | 13.54 | 6.85 | 19.10 | 0 |
| A1-b100 | 160 | 78 | 4167 | 27.27 | 13.71 | 38.74 | 0 |
| A2-b025 | 3 | 4 | 210 | 6.55 | 1.21 | 14.48 | 272 |
| A2-b050 | 7 | 8 | 426 | 13.03 | 2.34 | 28.63 | 667 |
| A2-b100 | 17 | 21 | 1014 | 25.29 | 4.42 | 55.09 | 1592 |
| A3-b025 | 5 | 8 | 430 | 5.98 | 1.07 | 12.56 | 512 |
| A3-b050 | 12 | 18 | 957 | 13.16 | 2.35 | 27.47 | 1196 |
| A3-b100 | 25 | 24 | 1197 | 27.08 | 4.81 | 56.98 | 2336 |

## Checkpoint vs full-restart repairs (secondary, descriptive, per failure)

- **window**: 50 failures; both modes scored on 41. Repaired only from the checkpoint: 7; only from the start: 1; exact sign test p = 0.0703. `{"ckpt+/restart-": 7, "ckpt-/restart-": 15, "ckpt+/restart+": 18, "not both scored": 9, "ckpt-/restart+": 1}`
- **outside_window_settled**: 71 failures; both modes scored on 2. Repaired only from the checkpoint: 1; only from the start: 0; exact sign test p = 1. `{"ckpt+/restart-": 1, "ckpt+/restart+": 1}`

## All-work cost per arm by cause (review 2, B1; registered rates)

The matched-cost datasets above charge counted (clean) work only (protocol 15:28). Everything spent:

- **A1** total $29.53: demonstration attempts, infra_error 9 ($2.26); demonstration attempts, scored 160 ($27.27)
- **A2** total $374.63: repairs, counted 47 ($71.19); repairs, void: exception: AttributeError 1 ($2.60); repairs, void: exception: BackendError 10 ($11.64); repairs, void: key name (xdotool) 19 ($43.74); repairs, void: operator stop / interrupted 43 ($15.79); repairs, void: other infrastructure 1 ($2.65); repairs, void: provider refusal (429) 212 ($86.30); repairs, void: provider transport 34 ($45.83); repairs, void: restore failure 15 ($15.07); student attempts, infra_error 95 ($30.77); student attempts, interrupted 37 ($0.00); student attempts, scored 160 ($49.06)
- **A3** total $270.58: repairs, counted 80 ($89.51); repairs, void: exception: BackendError 23 ($22.30); repairs, void: key name (xdotool) 11 ($12.01); repairs, void: operator stop / interrupted 7 ($1.23); repairs, void: other infrastructure 1 ($1.42); repairs, void: provider refusal (429) 156 ($32.54); repairs, void: provider transport 40 ($42.36); repairs, void: restore failure 10 ($5.83); student attempts, infra_error 95 ($24.29); student attempts, interrupted 37 ($0.00); student attempts, scored 160 ($39.10)

Under all-work accounting B would be $29.53, selecting (descriptive; not trained): A1 169 units, 78 verified paths from 78 tasks; A2 13 units, 11 verified paths from 5 tasks; A3 22 units, 15 verified paths from 6 tasks

## Repair-mode views (exploratory)

- **first 50 failures (selection order; window chosen after the first batch — exploratory)** (50 failures; split {"checkpoint successes only from the step-0 fallback": 4, "checkpoint successes from an evidence point": 21}; 9 unsettled):
  - counted repairs, any restart point: both scored 41, checkpoint only 7, restart only 1, both 18, neither 15, sign test p = 0.0703
  - counted repairs, evidence point only (k = 3 vs 3): both scored 41, checkpoint only 5, restart only 3, both 16, neither 17, sign test p = 0.727
  - first repair started, unscored branches as failures: both scored 50, checkpoint only 12, restart only 1, both 11, neither 26, sign test p = 0.00342
- **failures inside A2's budget window** (14 failures; split {"checkpoint successes only from the step-0 fallback": 2, "checkpoint successes from an evidence point": 8}; 0 unsettled):
  - counted repairs, any restart point: both scored 14, checkpoint only 3, restart only 0, both 7, neither 4, sign test p = 0.25
  - counted repairs, evidence point only (k = 3 vs 3): both scored 14, checkpoint only 2, restart only 1, both 6, neither 5, sign test p = 1
  - first repair started, unscored branches as failures: both scored 14, checkpoint only 5, restart only 0, both 3, neither 6, sign test p = 0.0625

```json
{
 "branch_rates": {
  "checkpoint \u00b7 evidence (latest)": {
   "branches": 108,
   "scored": 32,
   "verified": 5
  },
  "checkpoint \u00b7 evidence (origin)": {
   "branches": 78,
   "scored": 15,
   "verified": 9
  },
  "checkpoint \u00b7 evidence (stall)": {
   "branches": 899,
   "scored": 346,
   "verified": 61
  },
  "checkpoint \u00b7 step 0": {
   "branches": 630,
   "scored": 123,
   "verified": 14
  },
  "full_restart \u00b7 step 0": {
   "branches": 984,
   "scored": 321,
   "verified": 75
  }
 },
 "void_share_by_length": {
  "checkpoint \u00b7 0-29 steps": {
   "void": 787,
   "scored": 40,
   "void_share": 0.952
  },
  "checkpoint \u00b7 30-59 steps": {
   "scored": 112,
   "void": 24,
   "void_share": 0.176
  },
  "checkpoint \u00b7 60-89 steps": {
   "scored": 148,
   "void": 29,
   "void_share": 0.164
  },
  "checkpoint \u00b7 90+ steps": {
   "scored": 216,
   "void": 80,
   "void_share": 0.27
  },
  "full_restart \u00b7 0-29 steps": {
   "void": 484,
   "scored": 5,
   "void_share": 0.99
  },
  "full_restart \u00b7 30-59 steps": {
   "scored": 112,
   "void": 36,
   "void_share": 0.243
  },
  "full_restart \u00b7 60-89 steps": {
   "scored": 83,
   "void": 25,
   "void_share": 0.231
  },
  "full_restart \u00b7 90+ steps": {
   "scored": 121,
   "void": 82,
   "void_share": 0.404
  }
 },
 "end_reasons_by_depth": {
  "checkpoint \u00b7 restart step 0": {
   "max_steps": 70,
   "done": 36,
   "max_seconds": 17
  },
  "checkpoint \u00b7 restart step 1-29": {
   "done": 116,
   "max_steps": 85,
   "max_seconds": 139
  },
  "checkpoint \u00b7 restart step 30-69": {
   "max_steps": 4,
   "done": 3,
   "max_seconds": 19
  },
  "checkpoint \u00b7 restart step 70+": {
   "max_seconds": 25,
   "done": 2
  },
  "full_restart \u00b7 restart step 0": {
   "max_steps": 107,
   "done": 100,
   "max_seconds": 114
  }
 }
}
```

## Checkpoints (collection only)

```json
{
 "exp1-round1": {
  "capture_seconds": {
   "reset": {
    "n": 292,
    "p50": 14.728634222999972,
    "p90": 30.24292986999717,
    "mean": 16.90684870100336
   },
   "replay": {
    "n": 7604,
    "p50": 26.47597468299864,
    "p90": 35.30030909299967,
    "mean": 25.508493643519703
   }
  },
  "restore": {
   "replay": {
    "seconds": {
     "n": 1608,
     "p50": 60.973,
     "p90": 403.344,
     "mean": 195.69769465174122
    },
    "ok_rate": 0.5323383084577115,
    "tables_equal_rate": 0.9148009950248757,
    "n": 1608
   },
   "reset": {
    "seconds": {
     "n": 591,
     "p50": 9.501,
     "p90": 103.645,
     "mean": 32.33534010152286
    },
    "ok_rate": 0.9813874788494078,
    "tables_equal_rate": 0.9813874788494078,
    "n": 591
   }
  },
  "replay_seconds_per_step": 8.145210304854261,
  "recovery_by_reason": {
   "checkpoint:latest": {
    "verified": 5,
    "scored": 32,
    "branches": 108
   },
   "checkpoint:origin": {
    "verified": 9,
    "scored": 15,
    "branches": 78
   },
   "checkpoint:stall": {
    "verified": 61,
    "scored": 346,
    "branches": 899
   },
   "checkpoint:start": {
    "verified": 14,
    "scored": 123,
    "branches": 630
   }
  },
  "recovery_by_restart_step": {
   "checkpoint:0": {
    "verified": 14,
    "scored": 123
   },
   "checkpoint:1-9": {
    "verified": 28,
    "scored": 187
   },
   "checkpoint:10-29": {
    "verified": 45,
    "scored": 153
   },
   "checkpoint:30+": {
    "verified": 2,
    "scored": 53
   }
  }
 },
 "exp1-restart": {
  "capture_seconds": {},
  "restore": {
   "reset": {
    "seconds": {
     "n": 1120,
     "p50": 59.136,
     "p90": 244.28,
     "mean": 98.06457053571424
    },
    "ok_rate": 0.8464285714285714,
    "tables_equal_rate": 0.8464285714285714,
    "n": 1120
   }
  },
  "replay_seconds_per_step": null,
  "recovery_by_reason": {
   "full_restart:full_restart": {
    "verified": 75,
    "scored": 321,
    "branches": 984
   }
  },
  "recovery_by_restart_step": {
   "full_restart:0": {
    "verified": 75,
    "scored": 321
   }
  }
 }
}
```

## Training runs

| run | seed | host | steps | first loss | final loss | GPU h | datasets |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| A1-s1 | 1 | forkloop-main (A100-80) | 100 | 1.673 | 0.338 | 1.685 | ds-ff04067bf950, ds-7b346e2c7915 |
| A1-s2 | 2 | forkloop-main (A100-80) | 100 | 1.542 | 0.328 | 1.674 | ds-ff04067bf950, ds-7b346e2c7915 |
| A1-s3 | 3 | forkloop-main (A100-80) | 100 | 1.610 | 0.318 | 1.674 | ds-ff04067bf950, ds-7b346e2c7915 |
| A2-s1 | 1 | forkloop-main (A100-80) | 100 | 1.511 | 0.531 | 1.702 | ds-ff04067bf950, ds-3d69d44d56e4 |
| A2-s2 | 2 | forkloop-main (A100-80) | 100 | 1.716 | 0.398 | 1.69 | ds-ff04067bf950, ds-3d69d44d56e4 |
| A2-s3 | 3 | forkloop-main (A100-80) | 100 | 1.497 | 0.429 | 1.688 | ds-ff04067bf950, ds-3d69d44d56e4 |
| A3-s1 | 1 | forkloop-main (A100-80) | 100 | 1.529 | 0.475 | 1.677 | ds-ff04067bf950, ds-4c71fe661490 |
| A3-s2 | 2 | forkloop-main (A100-80) | 100 | 1.646 | 0.428 | 1.7 | ds-ff04067bf950, ds-4c71fe661490 |
| A3-s3 | 3 | forkloop-dev (1×H100) | 100 | 1.049 | 0.443 | 1.171 | ds-ff04067bf950, ds-4c71fe661490 |
| sw-seed0 | 0 | forkloop-main (A100-80) | 50 | 1.843 | 0.580 | 0.875 | ds-ff04067bf950 |

## Final evaluation (student alone, final_test)

| arm | runs | scored tasks | unscored cells | success (family-balanced) | per family |
| --- | --- | ---: | ---: | ---: | --- |
| A0 | [0] | 150 | 0 | 0.033 | compose_claims: 0.00; reschedule_constrained: 0.00; resolve_denial: 0.00; update_insurance_reconcile: 0.13 |
| A1 | [1, 2, 3] | 150 | 0 | 0.268 | compose_claims: 0.11; reschedule_constrained: 0.00; resolve_denial: 0.43; update_insurance_reconcile: 0.53 |
| A2 | [1, 2, 3] | 150 | 0 | 0.260 | compose_claims: 0.12; reschedule_constrained: 0.00; resolve_denial: 0.40; update_insurance_reconcile: 0.52 |
| A3 | [1, 2, 3] | 150 | 0 | 0.269 | compose_claims: 0.10; reschedule_constrained: 0.00; resolve_denial: 0.38; update_insurance_reconcile: 0.60 |
| S_W | [0] | 150 | 0 | 0.208 | compose_claims: 0.07; reschedule_constrained: 0.00; resolve_denial: 0.30; update_insurance_reconcile: 0.47 |

| comparison | tasks | difference | 95% CI | A better | B better | sign-test p |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| A2 − A0 | 150 | +0.226 | [+0.165, +0.290] | 35 | 0 | 5.82e-11 |
| A2 − A1 | 150 | -0.008 | [-0.042, +0.022] | 4 | 6 | 0.754 |
| A2 − A3 | 150 | -0.010 | [-0.064, +0.054] | 8 | 8 | 1 |
| A1 − A0 | 150 | +0.235 | [+0.171, +0.300] | 38 | 0 | 7.28e-12 |
| A3 − A0 | 150 | +0.236 | [+0.157, +0.314] | 41 | 0 | 9.09e-13 |
| S_W − A0 | 150 | +0.175 | [+0.117, +0.237] | 23 | 0 | 2.38e-07 |

Intervals for trained-arm contrasts are conditional on one collected dataset per arm (review 2, M4).

**Sensitivity — unscored cells scored as failures:** A0 0.033; A1 0.268; A2 0.260; A3 0.269; S_W 0.208
| comparison | tasks | difference | 95% CI | sign-test p |
| --- | ---: | ---: | --- | ---: |
| A2 − A0 | 150 | +0.226 | [+0.165, +0.290] | 5.82e-11 |
| A2 − A1 | 150 | -0.008 | [-0.042, +0.022] | 0.754 |
| A2 − A3 | 150 | -0.010 | [-0.064, +0.054] | 1 |
| A1 − A0 | 150 | +0.235 | [+0.171, +0.300] | 7.28e-12 |
| A3 − A0 | 150 | +0.236 | [+0.157, +0.314] | 9.09e-13 |
| S_W − A0 | 150 | +0.175 | [+0.117, +0.237] | 2.38e-07 |

**Sensitivity — key-name failures scored as policy failures:** A0 0.033; A1 0.268; A2 0.260; A3 0.269; S_W 0.208
| comparison | tasks | difference | 95% CI | sign-test p |
| --- | ---: | ---: | --- | ---: |
| A2 − A0 | 150 | +0.226 | [+0.165, +0.290] | 5.82e-11 |
| A2 − A1 | 150 | -0.008 | [-0.042, +0.022] | 0.754 |
| A2 − A3 | 150 | -0.010 | [-0.064, +0.054] | 1 |
| A1 − A0 | 150 | +0.235 | [+0.171, +0.300] | 7.28e-12 |
| A3 − A0 | 150 | +0.236 | [+0.157, +0.314] | 9.09e-13 |
| S_W − A0 | 150 | +0.175 | [+0.117, +0.237] | 2.38e-07 |

**Sensitivity — A3 without seed 3 (trained on the H100 box):** A0 0.033; A1 0.268; A2 0.260; A3 0.300; S_W 0.208
| comparison | tasks | difference | 95% CI | sign-test p |
| --- | ---: | ---: | --- | ---: |
| A2 − A0 | 150 | +0.226 | [+0.165, +0.290] | 5.82e-11 |
| A2 − A1 | 150 | -0.008 | [-0.042, +0.022] | 0.754 |
| A2 − A3 | 150 | -0.040 | [-0.079, -0.004] | 0.0352 |
| A1 − A0 | 150 | +0.235 | [+0.171, +0.300] | 7.28e-12 |
| A3 − A0 | 150 | +0.267 | [+0.204, +0.329] | 1.82e-12 |
| S_W − A0 | 150 | +0.175 | [+0.117, +0.237] | 2.38e-07 |

```json
{
 "per_shard_success": {
  "main (shard 0/2)": {
   "A0": 0.03333333333333333,
   "A1": 0.21944444444444444,
   "A2": 0.20555555555555555,
   "A3": 0.22777777777777775,
   "S_W": 0.15833333333333333
  },
  "aux (shard 1/2)": {
   "A0": 0.03333333333333333,
   "A1": 0.31666666666666665,
   "A2": 0.3138888888888889,
   "A3": 0.3111111111111111,
   "S_W": 0.2583333333333333
  }
 },
 "unscored_cells_by_cause": {},
 "attempts_per_cell": {
  "A0 \u00b7 aux (shard 1/2)": {
   "1": 9,
   "2": 66
  },
  "A0 \u00b7 main (shard 0/2)": {
   "1": 37,
   "2": 9,
   "3": 29
  },
  "A1-s1 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A1-s1 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A1-s2 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A1-s2 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A1-s3 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A1-s3 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A2-s1 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A2-s1 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A2-s2 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A2-s2 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A2-s3 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A2-s3 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A3-s1 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A3-s1 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A3-s2 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A3-s2 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "A3-s3 \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "A3-s3 \u00b7 main (shard 0/2)": {
   "1": 75
  },
  "S_W \u00b7 aux (shard 1/2)": {
   "1": 75
  },
  "S_W \u00b7 main (shard 0/2)": {
   "1": 41,
   "2": 4,
   "3": 30
  }
 },
 "end_reasons": {
  "A0 \u00b7 aux (shard 1/2)": {
   "max_steps": 73,
   "done": 2
  },
  "A0 \u00b7 main (shard 0/2)": {
   "max_steps": 72,
   "done": 2,
   "max_seconds": 1
  },
  "A1 \u00b7 aux (shard 1/2)": {
   "max_steps": 158,
   "done": 67
  },
  "A1 \u00b7 main (shard 0/2)": {
   "max_steps": 181,
   "done": 44
  },
  "A2 \u00b7 aux (shard 1/2)": {
   "max_steps": 158,
   "done": 67
  },
  "A2 \u00b7 main (shard 0/2)": {
   "max_steps": 183,
   "done": 42
  },
  "A3 \u00b7 aux (shard 1/2)": {
   "max_steps": 156,
   "done": 69
  },
  "A3 \u00b7 main (shard 0/2)": {
   "max_steps": 174,
   "done": 51
  },
  "S_W \u00b7 aux (shard 1/2)": {
   "max_steps": 56,
   "done": 19
  },
  "S_W \u00b7 main (shard 0/2)": {
   "max_steps": 62,
   "done": 13
  }
 },
 "model_latency_s": {
  "A0 \u00b7 aux (shard 1/2)": {
   "n": 8786,
   "p50": 11.7849,
   "p90": 18.4676
  },
  "A0 \u00b7 main (shard 0/2)": {
   "n": 8804,
   "p50": 18.328,
   "p90": 30.1976
  },
  "A1 \u00b7 aux (shard 1/2)": {
   "n": 21031,
   "p50": 16.0699,
   "p90": 20.1156
  },
  "A1 \u00b7 main (shard 0/2)": {
   "n": 23205,
   "p50": 18.5827,
   "p90": 23.8607
  },
  "A2 \u00b7 aux (shard 1/2)": {
   "n": 20955,
   "p50": 15.801,
   "p90": 19.81
  },
  "A2 \u00b7 main (shard 0/2)": {
   "n": 23325,
   "p50": 18.435,
   "p90": 23.4579
  },
  "A3 \u00b7 aux (shard 1/2)": {
   "n": 21289,
   "p50": 15.5648,
   "p90": 19.505
  },
  "A3 \u00b7 main (shard 0/2)": {
   "n": 23036,
   "p50": 18.4221,
   "p90": 23.4356
  },
  "S_W \u00b7 aux (shard 1/2)": {
   "n": 7261,
   "p50": 11.4583,
   "p90": 15.8677
  },
  "S_W \u00b7 main (shard 0/2)": {
   "n": 7881,
   "p50": 18.6613,
   "p90": 24.3369
  }
 }
}
```
