"""Structured evidence that heldout_seeds 100328-100351 (and their claim-number mirrors, train 328-351)
were never used: task ids, episode manifests, config seed lists, SFT datasets. Writes seed-evidence.json."""
import json, os, re, glob
from pathlib import Path
import yaml
CANDS = list(range(100328, 100352))
MIRRORS = [s - 100000 for s in CANDS]  # train seeds with the same claim number (60000 + seed % 50000)
out = {"candidates": CANDS, "mirrored_train_seeds": MIRRORS}
# 1. every episode manifest anywhere under runs/ and docs/ (split, seed)
used = {}
n_manifests = 0
for p in glob.glob("runs/**/manifest.json", recursive=True) + glob.glob("docs/**/manifest.json", recursive=True):
    try: m = json.load(open(p))
    except Exception: continue
    if not isinstance(m, dict) or "seed" not in m or "split" not in m: continue
    n_manifests += 1
    used.setdefault(m["split"], set()).add(int(m["seed"]))
out["episode_manifests_scanned"] = n_manifests
out["heldout_seeds_ever_run"] = sorted(used.get("heldout_seeds", set()))
out["candidate_overlap_with_episodes"] = sorted(set(CANDS) & used.get("heldout_seeds", set()))
out["mirror_overlap_with_train_episodes"] = sorted(set(MIRRORS) & used.get("train", set()))
# 2. task ids in any file or directory name, and inside text files
tid = re.compile(r"heldout_seeds-(\d{6})")
names, contents = set(), set()
for dp, dn, fn in os.walk("."):
    if "/venv" in dp or dp.startswith("./venv") or "/.git" in dp: continue
    for x in dn + fn:
        for m in tid.findall(x): names.add(int(m))
    for f in fn:
        if f.endswith((".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".log", ".csv")):
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) > 300_000_000: continue
                for m in tid.findall(open(p, errors="ignore").read()): contents.add(int(m))
            except Exception: pass
out["heldout_task_ids_in_names"] = sorted(names)
out["heldout_task_ids_in_contents"] = sorted(contents)
out["candidate_task_id_hits"] = sorted(set(CANDS) & (names | contents))
# 3. seeds lists in every YAML config (repo configs and run directories)
cfg_seeds = {}
for p in glob.glob("configs/**/*.y*ml", recursive=True) + glob.glob("runs/**/*.y*ml", recursive=True):
    try: c = yaml.safe_load(open(p))
    except Exception: continue
    if isinstance(c, dict) and isinstance(c.get("seeds"), list):
        cfg_seeds[p] = (c.get("split"), c["seeds"])
out["configs_scanned"] = len(cfg_seeds)
out["candidate_config_hits"] = sorted(p for p, (split, s) in cfg_seeds.items() if split == "heldout_seeds" and set(s) & set(CANDS))
out["mirror_config_hits"] = sorted(p for p, (split, s) in cfg_seeds.items() if split in (None, "train") and set(s) & set(MIRRORS))
out["heldout_config_seeds"] = sorted({x for p, (split, s) in cfg_seeds.items() if split == "heldout_seeds" for x in s})
# 4. SFT / collection datasets
data_hits = []
for p in glob.glob("data/**/*.jsonl", recursive=True) + glob.glob("data/**/*.json", recursive=True):
    s = open(p, errors="ignore").read()
    if "heldout_seeds" in s: data_hits.append(p)
out["datasets_scanned"] = len(glob.glob("data/**/*.json*", recursive=True))
out["datasets_mentioning_heldout_seeds"] = data_hits
json.dump(out, open("runs/session-20260924/seed-evidence.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k not in ("candidates", "mirrored_train_seeds")}, indent=1))
