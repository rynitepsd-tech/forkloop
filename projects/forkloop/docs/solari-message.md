# Follow-up to Solari — thanks for items 1–2, re-asking 3–5 and two new ones

Draft, 2026-09-03 (night). Same thread as the 2026-09-02 email (kept below). Everything is measured; ids and
error bodies are verbatim from `runs/logs/solari_verify.log`, `bench/reset_results_desktop_0903.jsonl` and the
run logs. DO NOT SEND until reviewed.

---

Hi Solari team,

Thank you — that was fast. I re-ran the same scripts the evening you said "all set" and again tonight on the
live golden, and both fixes hold:

**1. `revert()` works, and a refused revert no longer destroys the machine.**
- Desktop, snapshot of its own state, n=3: API call 17.6 s p50, guest reachable 2.5 s later, screen stable
  1.6 s after that — **21.5 s p50 end to end, state restored 3/3**. Running sandbox: reachable 19.0 s after the
  call. Paused sandbox: a clear 409 "revert needs a running sandbox — resume it first" and the machine survives.
- On our real workload — a fork of the 8.5 GB golden `snap_dl4e90g095y2`, `revert(golden)` between episodes —
  **10/10 reverts succeeded on one machine id** (`…vm_001996…`, 14:50–15:06 UTC-7 tonight). Full reset (revert +
  seed + health + baseline + first screen) p50 100.9 s, p95 151.7 s; the same reset via `create(from_snapshot)`
  the same half hour: p50 92.0 s, p95 169.4 s, also 10/10.
- The machine we reverted stayed alive after every call; the one refusal we saw (below) left it answering
  commands.
- One thing on our side, for context: in two longer runs tonight a revert was accepted but the guest took more than
  90 s to answer again (our client's post-revert window), so we treated it as failed and replaced the machine. That
  is the slow restore mode from the next paragraph; we have raised the window to 240 s.

**2. `snapshot()` on a `from_snapshot` machine works.** 20.8 s on a fork of the golden, and it deleted cleanly.
We used it tonight for the first time in anger: best-of-2 search checkpoints a running episode, forks each
candidate from the checkpoint, and adopts the winner — 3/3 episodes verified through a real branch point.

One thing we noticed while measuring, in case it is useful: **restores of the 8.5 GB snapshot are bimodal, and
the two modes are the same for `revert()` and for `create(from_snapshot)`** — either ≈ 22 s (7 of 20 tonight) or
70–160 s (13 of 20; 37, 72, 73, 78, 82, 84, 103, 105, 105, 112, 122, 126, 161 s). Over 90 forks in a longer run
the split was 33 under 30 s, 50 over 60 s, 6 over 190 s (max 353 s). Since the revert path never creates a
machine, this is not our client backing off; is it snapshot locality (warm vs cold host), and is there anything
we can do on our side — pin a host, pre-warm, or a smaller snapshot — to stay in the fast mode?

The three items that are unchanged, re-measured 2026-09-03 with the same scripts:

**3. `recordingUrl` still never populates.** `record.start()`/`record.stop()` succeed on a plain desktop, on a
`from_snapshot` desktop created with `record=True`, and on one created without it; each leaves an in-VM mp4
(126–145 KB for a few seconds; `/tmp/solari-rec-<ts>.mp4`) and `recordingUrl` is `None` in all three cases. New
since the fix: on the plain desktop, `record.stop` after a `revert()` **timed out after 30 000 ms** (the
`record.start` right after the revert had succeeded). Is the presigned upload expected on `POST /sandboxes` with
`kind: "desktop"`, and is a recorder that was running across a revert expected to survive it?

**4. `disk_gb` is still ignored, and so are `cpu`/`mem_mb` on `from_snapshot` creates.** Sandbox `/dev/root 3.9G`
(42 % used on `base`); a fork of the golden `3.9G` at 86 % with 535 MB free. A fork requested at
`cpu=4, mem_mb=8192` reports `nproc` 2 and 4031 MB in `free -m`. We understand the shape may be fixed by the
snapshot; is there a template or a create-time option that gives a larger disk or RAM, and would a golden built
on such a template keep its shape when forked?

**5. Ancestor snapshots.** `snap_dl4driq97904` → `snap_dl4e05ciyt1p` → `snap_dl4e90g095y2` (the live golden) are
still on the account and `delete_snapshot` refuses while a descendant exists. Is there a way to flatten a
lineage, or to delete ancestors of a snapshot we keep? And what does snapshot storage cost per GB-month (five to
eight of these, 6–8.5 GB each)?

**6. (new) 503 on `revert()` to the golden from a fork.** One call, 2026-09-03 evening, on a fresh fork of
`snap_dl4e90g095y2`:
`503 — no desktop host has capacity right now; retry in a minute: Revert failed: the host could not restore this
snapshot in time`. The machine stayed alive and answered commands afterwards (good). Tonight the same call
succeeded 10/10, so this looks like the slow mode from the bimodality note hitting a timeout on your side. Is
there a `retry-after` we should honour, and does the host-side timeout scale with snapshot size?

**7. (new) checkpoint snapshots that could not be deleted right after use.** Three snapshots named
`cp-resolve_denial-train-00000{0,1,2}-…` were taken on a running fork tonight, each forked once, and the
`DELETE /snapshots/:id` right after the branch fork was killed was refused (we did not capture the body; the
next run will). If a snapshot counts a just-killed child as live for a while, how long is that window?

Happy to run anything else on the account; the scripts for each item are in our public fork of your cookbook
(`projects/forkloop/docs/solari-repro.md`).

Thanks again,
Ryder

---

# Message to Solari — revert() and fork snapshots refused, recordingUrl, disk size, snapshot pricing

Draft, 2026-09-02. Post in the Solari Discord (#support) or email support. Everything below is measured; ids and bodies are verbatim from our logs.

---

Hi Solari team,

I'm building a snapshot-native RL environment on your desktops (Ubuntu desktop + OpenEMR + a payer portal, snapshot once, reset every episode). The fork route works well for us — `create(from_snapshot=...)` restores RAM, processes and windows in about 17–20 s and two forks of one snapshot run side by side on Starter. Five things would make the product materially better, and I'd appreciate a pointer on each.

**1. `revert()` returns 409 `Not revertable` on every machine we've tried, and a failed revert on a running machine destroys it.**
Account: Starter (upgraded from Free on 2026-09-02); the behaviour was identical on Free.
- `POST /sandboxes/:id/revert` on a running *sandbox* → 409 `Not revertable`; the sandbox was `Not found` afterwards.
- Same call on a *paused* sandbox → 409 `Not revertable`; the machine survived and `resume()` worked.
- Same call on a running *desktop* created with `create_desktop(...)`, snapshotting via `snapshot()` first → 409 `Not revertable`; afterwards the control WebSocket answered HTTP 404 for 120 s and the machine never came back.
- `snapshot()` on a paused machine → `Not snapshottable`.
Snapshot ids involved: `snap_dl4cngznmvr7` (sandbox golden), `snap_dl4driq97904` → `snap_dl4e05ciyt1p` → `snap_dl4e90g095y2` (desktop golden lineage). Client: `solari-sandbox` 0.2.0, `base_url=https://api.getsolari.com`.
The snapshots docs say `revert(snapshotId)` "rewinds the same machine back to a snapshot" and that snapshots "work the same way for both VMs and sandboxes". Is `revert()` gated by plan, by template, by snapshot type, or is it currently disabled? If it can be enabled for this account, our headline reset number and one whole chart depend on it (revert keeps the machine id, so the VNC stream and control channel survive a reset). If it stays unavailable, could the failed call at least leave the machine intact?

**2. `snapshot()` is refused on any machine created with `from_snapshot`.**
`POST /sandboxes/:id/snapshots` on a running desktop that was created from a snapshot returns 409 `Not snapshottable` (three attempts over 40 s, state `running` throughout); the identical call on a freshly created desktop succeeds in seconds. Together with (1) this means a snapshot lineage can only be extended on the original machine: a fork can be used but never checkpointed, so branching search (snapshot at an uncertain step, try several continuations) is impossible for us today, and rebuilding a golden image means rebuilding from scratch rather than forking, patching and re-snapshotting. Is this a deliberate restriction?

**3. `recordingUrl` never populates through the unified `/sandboxes` route.**
`record.start()` / `record.stop()` work on a plain desktop, on a `from_snapshot` desktop created with `record=True`, and on one created without the flag — each writes an in-VM mp4 (~150 KB for a few seconds). But `recordingUrl` stays empty in all three cases, so we pull the file out with `files.read`. Is the presigned upload expected to fire for desktops created via `POST /sandboxes` with `kind: "desktop"`, or only via the legacy desktops route?

**4. `disk_gb` is ignored: every machine has a 4 GB disk.**
We request `disk_gb: 10` on both sandboxes and desktops and the block device stays 4 GB. OpenEMR + MariaDB + Chrome fit only after purging VS Code and LibreOffice from the default template, and a fork that fills the disk breaks MariaDB ("table 'log' is full"). Is there a template or plan where a larger disk is honoured?

**5. Snapshot storage pricing and lineage.**
Our account holds four snapshots of roughly 6–8 GB each. The pricing page doesn't list snapshot storage, and `delete_snapshot` refuses while a descendant exists (`snap_dl4driq97904` cannot be deleted while `snap_dl4e05ciyt1p` and `snap_dl4e90g095y2` exist). What does storage cost per GB-month, and is there a way to flatten or delete ancestors?

Three small things while I'm here, in case they're useful to you: the `default` desktop template has no working GPU process (`Failed to send GpuControl.CreateCommandBuffer`), so Chrome's renderer dies with "Aw, Snap! Error code: 5" on GPU-composited pages unless it is started with `--disable-gpu`; the SDK presses a key list sequentially, so `keyboard.press(["ctrl", "a"])` types the letter `a` — a chord has to be the single string `"ctrl+a"`; and `mouse.scroll` takes a button code rather than a direction and amount, so we emulate scrolling with Page_Down/Page_Up.

One more data point from this afternoon (≈ 11:00–11:20 UTC on 2026-09-02): three `from_snapshot` desktops in a row never became ready (the control WebSocket closed with code 1000 during restore) and the next one returned 503 `No sandbox host available` after 52 s, while a fresh `default` desktop created in the same minute was ready in 0.7 s. If snapshot restores need warm hosts that are scarcer than fresh ones, a `retry-after` hint on the 503 would let us back off sensibly.

And later the same day (≈ 13:50–14:30 UTC): `POST /sandboxes` for both `from_snapshot` and fresh `default` desktops returned 429 `Too many concurrent sessions` while `list_all(kind="desktop")` and `list_all(kind="sandbox")` both returned zero sessions, after several machines had lost their control channel (close code 1000) and one create call had hung for over five minutes. It looks like sessions whose channel died are still counted against the Starter cap of two. Could you check the account for stuck sessions, and is there a way for us to see or clear them?

Happy to share the scripts that reproduce all of the above (they're in a public fork of your cookbook) or to run anything you'd like on the account.

Thanks,
Ryder
