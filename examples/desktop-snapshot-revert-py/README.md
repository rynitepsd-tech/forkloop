# Desktop snapshot, revert, fork (Python)

Freeze a running desktop with `snapshot()`, put it back with `revert()`, and boot independent copies with `create_desktop(from_snapshot=...)`. The example types in mousepad, snapshots, types more, reverts (and prints how long that took), then forks a second VM and shows a file written there never appears in the original.

Desktops need a paid plan (a `402 FeatureRequiresPlan` on create means the key is on Free). The original plus one fork is two live VMs — the Starter cap.

## Run

```bash
cd examples/desktop-snapshot-revert-py
pip install -r requirements.txt
export SOLARI_API_KEY=slr_live_...   # https://console.getsolari.com
python main.py
```

Source: [`main.py`](main.py)
