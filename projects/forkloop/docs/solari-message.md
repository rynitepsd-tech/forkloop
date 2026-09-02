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
