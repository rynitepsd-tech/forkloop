"""train_lora's forkloop.dataset.v1 input: verification, mixing, rendering parity, label masking (torch-free)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from forkloop.correction.dataset import render_target
from forkloop.policies.student import StudentPolicy
from forkloop.types import Observation
from train import train_lora as t

ROOT = Path(__file__).resolve().parents[1]
MEMORY_PROMPT = (ROOT / "forkloop/policies/prompts/agent_memory_v1.md").read_text(encoding="utf-8")


def _png(color, size=(1280, 720)) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", size, color)
    im.putpixel((5, 5), (1, 2, 3))
    im.save(buf, format="PNG")
    return buf.getvalue()


def make_dataset(root: Path, *, n: int = 3) -> Path:
    """A tiny dataset.v1 directory in the exporter's layout (content-addressed images, hashed manifest)."""
    (root / "images").mkdir(parents=True)
    shots = []
    for color in ("red", "green", "blue", "white"):
        data = _png(color)
        sha = hashlib.sha256(data).hexdigest()
        (root / "images" / f"{sha}.png").write_bytes(data)
        shots.append({"path": f"images/{sha}.png", "sha256": sha})
    history = ['click(640, 360)', 'type("admin")', 'key("Return")']
    recs = []
    for i in range(n):
        step = i + 1
        memory = [] if i == 0 else ["Authorization number AUTH-36G14538"]
        written = ["Authorization number AUTH-36G14538"] if i == 0 else []
        action = {"type": "click", "x": 1279, "y": 719, "button": "left"} if i != 2 else {"type": "type", "text": "AUTH-36G14538"}
        recs.append({"schema": t.DATASET_SCHEMA, "kind": "action_demonstration", "origin": "correction_suffix",
                     "record_id": f"r{i}",
                     "input": {"instruction": "File the appeal with the authorization number.", "history": history[:step],
                               "memory": memory, "step": step, "screen": [1280, 720],
                               "current_shot": shots[i + 1], "previous_shot": shots[i]},
                     "target": {"reasoning": "The letter shows the number.", "memory_written": written, "action": action,
                                "raw_reply": "x"},
                     "source": {"path_id": "p", "step": step}, "audit": {}})
    with (root / "records.jsonl").open("w") as fh:
        for r in recs:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    for name in ("preferences.jsonl", "diagnostics.jsonl"):
        (root / name).write_text("")
    files = {n_: hashlib.sha256((root / n_).read_bytes()).hexdigest()
             for n_ in ("records.jsonl", "preferences.jsonl", "diagnostics.jsonl")}
    (root / "manifest.json").write_text(json.dumps({"schema": t.DATASET_SCHEMA, "dataset_id": "ds-" + files["records.jsonl"][:12],
                                                    "files": files, "counts": {"records": n}, "splits": ["train"]}))
    return root


def test_verify_dataset_dir_accepts_and_rejects_tampering(tmp_path):
    root = make_dataset(tmp_path / "ds")
    info = t.verify_dataset_dir(root)
    assert info["records"] == 3 and info["images_verified"] == 4 and info["dataset_id"].startswith("ds-")
    assert info["manifest_sha256"] == hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    img = next((root / "images").glob("*.png"))
    img.write_bytes(_png("black"))
    with pytest.raises(ValueError, match="sha256"):
        t.verify_dataset_dir(root)
    root2 = make_dataset(tmp_path / "ds2")
    with (root2 / "records.jsonl").open("a") as fh:
        fh.write("\n")
    with pytest.raises(ValueError, match="records.jsonl"):
        t.verify_dataset_dir(root2)


def test_mix_records_weights_are_explicit_and_deterministic():
    a = [{"id": f"a{i}"} for i in range(4)]
    b = [{"id": f"b{i}"} for i in range(4)]
    flat, info = t.mix_records([a, b], None, seed=0)
    assert flat == a + b and [x["drawn"] for x in info] == [4, 4]
    mixed, info = t.mix_records([a, b], [3, 1], seed=0)
    assert [x["drawn"] for x in info] == [6, 2] and len(mixed) == 8
    assert sum(r["id"].startswith("a") for r in mixed) == 6
    assert {r["id"] for r in mixed if r["id"].startswith("a")} == {r["id"] for r in a}  # whole pass before repeats
    assert t.mix_records([a, b], [3, 1], seed=0)[0] == mixed
    assert len(t.mix_records([a, b], [1, 1], seed=0, samples=20)[0]) == 20
    with pytest.raises(ValueError):
        t.mix_records([a, b], [1], seed=0)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_dataset_record_renders_exactly_the_serving_request(tmp_path, index):
    root = make_dataset(tmp_path / "ds")
    rec = t.load_dataset_records(root)[index]
    ex = t.SFTExamples([rec], max_image_side=1280, style="compact", history_k=8, coord_space="norm1000",
                       system_prompt_template=MEMORY_PROMPT)[0]
    inp = rec["input"]
    pol = StudentPolicy("http://127.0.0.1:1/v1", "m", prompt_style="compact", coord_space="norm1000", history_k=8,
                        prev_screenshot=True, memory=True, system_prompt=MEMORY_PROMPT,
                        extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    pol._memory = list(inp["memory"])
    obs = Observation((root / inp["current_shot"]["path"]).read_bytes(), inp["instruction"], inp["step"], inp["history"],
                      1280, 720, (root / inp["previous_shot"]["path"]).read_bytes())
    body, _ = pol.build_request(obs)
    asyncio.run(pol.aclose())
    from train.parity import canonical_http
    assert canonical_http(body["messages"]) == ex["prompt_messages"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False} == t.template_kwargs_for("auto", "compact")
    sent = [Image.open(io.BytesIO(base64.b64decode(p["image_url"]["url"].split(",", 1)[1]))).convert("RGB")
            for p in body["messages"][1]["content"] if p["type"] == "image_url"]
    assert [im.tobytes() for im in sent] == [im.tobytes() for im in ex["images"]]  # previous, then current
    text = ex["prompt_messages"][1]["content"][0]["text"]
    if inp["memory"]:
        assert "- Authorization number AUTH-36G14538" in text
    else:
        assert "Memory: empty" in text
    assert "click(500, 500)" in text  # history in the student's 0-1000 frame
    assert ex["target"] == render_target(rec["target"], screen=(1280, 720), coords=(1000, 1000))
    assert ex["target"].splitlines()[-1] in ("click(999, 999)", 'type("AUTH-36G14538")')
    assert ("Memory: Authorization number AUTH-36G14538" in ex["target"]) == (index == 0)
    assert "AUTH-36G14538" not in json.dumps(body["messages"]) or inp["memory"]  # only memory carries the value


def test_supervise_masks_prompt_and_everything_after_end_of_turn():
    ids, labels = t.supervise([1, 2, 3], [7, 8, 99, 10], eos_id=99)
    assert ids == [1, 2, 3, 7, 8, 99] and labels == [-100, -100, -100, 7, 8, 99]
    with pytest.raises(ValueError):
        t.supervise([1], [7, 8], eos_id=99)
    with pytest.raises(ValueError):
        t.supervise([1], [99], eos_id=99)
    with pytest.raises(ValueError):
        t.supervise([1], [], eos_id=99)


def test_template_kwargs_and_lora_targets():
    assert t.template_kwargs_for("auto", "fara") == {}
    assert t.template_kwargs_for("off", "fara") == {"enable_thinking": False}
    names = ["model.language_model.layers.0.self_attn.q_proj", "model.language_model.layers.1.linear_attn.in_proj_qkv",
             "model.language_model.layers.1.linear_attn.out_proj", "model.language_model.layers.1.mlp.down_proj",
             "model.visual.blocks.0.attn.qkv", "model.visual.blocks.0.attn.proj", "model.visual.merger.linear_fc1",
             "lm_head", "mtp.layers.0.self_attn.q_proj"]
    spec = t.resolve_target_modules("auto", names)
    rx = re.compile(spec)
    assert [n for n in names if rx.fullmatch(n)] == names[:4]
    # peft wraps the model: names gain a base_model.model. prefix and must still match
    assert rx.fullmatch("base_model.model.model.language_model.layers.3.mlp.gate_proj")
    assert t.resolve_target_modules("auto", ["encoder.q_proj"]) == t.DEFAULT_TARGET_MODULES
    assert t.resolve_target_modules("q_proj,v_proj", names) == ["q_proj", "v_proj"]
    args = t.build_parser().parse_args(["--model", "m", "--output-dir", "o", "--dataset", "a", "--dataset", "b",
                                        "--dataset-weights", "3,1", "--max-steps", "20", "--seed", "1", "--lr", "2e-4",
                                        "--epochs", "3"])
    assert args.dataset == ["a", "b"] and args.dataset_weights == "3,1" and args.max_steps == 20


def test_training_refuses_final_test_sources():
    ok = {"record_id": "a", "source": {"task_id": "resolve_denial-train-000020"}}
    final = {"record_id": "b", "source": {"family": "resolve_denial", "split": "final_test", "seed": 350000}}
    sealed = {"record_id": "c", "source": {"task_id": "resolve_denial-heldout_seeds-100510"}}
    assert t.record_task(ok) == ("resolve_denial", "train", 20)
    assert t.record_task(final) == ("resolve_denial", "final_test", 350000)
    assert t.assert_no_final_test([ok])["tasks_checked"] == 1
    for bad in (final, sealed):
        with pytest.raises(SystemExit, match="final-test"):
            t.assert_no_final_test([ok, bad])
    with pytest.raises(SystemExit, match="no source task"):
        t.assert_no_final_test([{"record_id": "d", "source": {}}])


def test_training_images_use_the_serving_transform(tmp_path):
    root = make_dataset(tmp_path / "ds")
    rec = t.load_dataset_records(root)[1]
    ex = t.SFTExamples([rec], max_image_side=1920, style="compact", history_k=8, coord_space="norm1000",
                       system_prompt_template=MEMORY_PROMPT, image_scale=1.5)[0]
    inp = rec["input"]
    pol = StudentPolicy("http://127.0.0.1:1/v1", "m", prompt_style="compact", coord_space="norm1000", history_k=8,
                        prev_screenshot=True, memory=True, system_prompt=MEMORY_PROMPT, image_scale=1.5,
                        image_max_side=1920)
    pol._memory = list(inp["memory"])
    obs = Observation((root / inp["current_shot"]["path"]).read_bytes(), inp["instruction"], inp["step"], inp["history"],
                      1280, 720, (root / inp["previous_shot"]["path"]).read_bytes())
    body, _ = pol.build_request(obs)
    asyncio.run(pol.aclose())
    sent = [Image.open(io.BytesIO(base64.b64decode(p["image_url"]["url"].split(",", 1)[1]))).convert("RGB")
            for p in body["messages"][1]["content"] if p["type"] == "image_url"]
    assert [im.size for im in ex["images"]] == [(1920, 1080)] * 2
    assert [im.tobytes() for im in sent] == [im.tobytes() for im in ex["images"]]
