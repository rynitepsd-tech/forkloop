# Protocol: why does gpt-6-luna misread? (exploratory, frozen 2026-09-24)

Committed before any request. **Exploratory**: chosen after the confirmatory results
([reading study](reading-model-upgrade-results.md), [live comparison](live-model-upgrade-comparison.md)),
so it can suggest a cause, not establish one.

**Question.** `gpt-6-luna` typed the exact authorization on 10 of 20 frozen screens where
`gpt-5.6-luna` got 20. Is that the prompt (written for `gpt-5.6-luna`) or perception?

**Arms** (all `gpt-6-luna`, the same 20 authorization states, image detail high, one request each,
arm order rotated by state; 60 requests):

| Arm | Change from the reading study's `gpt-6-luna` request |
| --- | --- |
| `base` | None. Byte-identical request bodies (asserted at freeze); measures run-to-run noise |
| `careful` | One paragraph appended to the v5 system prompt: read identifiers one character at a time, write every character in the reasoning, check repeated characters, never shorten |
| `tiles` | The Sept 16 magnified views appended: 2 × 2 tiles of both screenshots, enlarged 2× (nearest neighbour) |

**Metric.** Exact typed authorization per arm (out of 20); each variant vs `base` by exact McNemar,
reported as exploratory. Wrong values listed with the reading study's pattern classes.

**How to read it.** If `careful` recovers most misses, the regression is largely prompt fit. If only
`tiles` does, it is resolution or perception. If neither does, the gap is in the model. A prompt fix
found here would still need its own pre-registered live comparison.

**Rules.** One transport retry per cell (as in the reading study); any other failure stops the run.
Results are written once and never rerun. Ledger `runs/session-20260924/session-ledger.sqlite`;
projected cost about $0.05 (the `tiles` arm sends ten images per request).

Runner `runs/gpt6-reading-probe-20260924.py` SHA-256 `1a51b79e44f7c907c7a10dcbcb1cc312051f13d65c1717031ba0b1bcbef132c4`;
`protocol.json` SHA-256 `87b448f0801060da1442f334dc931161faa202b8ff3cc41c0e810000cb206a1f`.
