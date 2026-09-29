"""train/parity.py with a real cached Qwen3.5-family processor (Holo-3.1-9B by default).

No weights, no GPU, no network: skipped unless torch/transformers are installed and the processor
files are in the local HF cache (forkloop-dev: HF_HOME=/lambda/nfs/forkloop-usw3/hf). Uses the
tiny dataset.v1 builder of tests/test_train_dataset.py (memory-bearing, two-image records)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from tests.test_train_dataset import MEMORY_PROMPT, make_dataset  # noqa: E402
from train import parity  # noqa: E402
from train import train_lora as t  # noqa: E402

MODEL = os.environ.get("FORKLOOP_PARITY_MODEL", "Hcompany/Holo-3.1-9B")


@pytest.fixture(scope="module")
def processor():
    try:
        return transformers.AutoProcessor.from_pretrained(MODEL, local_files_only=True)
    except Exception:
        pytest.skip(f"{MODEL} processor is not cached locally")


def _check(rec, processor, **kw):
    args = dict(processor=processor, style="compact", coord_space="norm1000", history_k=8, max_image_side=1280,
                system_template=MEMORY_PROMPT, template_kwargs={"enable_thinking": False}, max_seq_len=16384)
    args.update(kw)
    return parity.check_record(rec, **args)


def test_every_parity_check_passes_on_memory_records(tmp_path, processor):
    root = make_dataset(tmp_path / "ds")
    t.verify_dataset_dir(root)
    for rec in t.load_dataset_records(root):
        res = _check(rec, processor)
        assert res["ok"], {k: v for k, v in res["checks"].items() if not v["ok"]}
        c = res["checks"]
        assert c["image_grid_equal"]["training"] == [[1, 44, 80], [1, 44, 80]]  # 1280x720 -> 1280x704 per image
        assert c["no_truncation"]["image_tokens"] == 2 * 880
        assert c["labels_mask"]["decoded_target"].endswith("<|im_end|>")
        assert set(c) >= {"messages_equal", "images_equal", "prompt_ids_offline", "image_grid_equal", "thinking_disabled",
                          "boundary", "labels_mask", "concat_stable", "no_truncation"}


def test_parity_detects_a_serving_request_without_the_thinking_switch(tmp_path, processor, monkeypatch):
    rec = t.load_dataset_records(make_dataset(tmp_path / "ds"))[1]
    from forkloop.policies import student

    orig = student.StudentPolicy.build_request

    def no_kwargs(self, obs, n=1):
        body, ctx = orig(self, obs, n)
        body.pop("chat_template_kwargs", None)
        return body, ctx

    monkeypatch.setattr(student.StudentPolicy, "build_request", no_kwargs)
    res = _check(rec, processor)
    assert not res["ok"]
    assert not res["checks"]["prompt_ids_offline"]["ok"] and not res["checks"]["thinking_disabled"]["ok"]


def test_parity_detects_a_training_side_image_resize(tmp_path, processor):
    rec = t.load_dataset_records(make_dataset(tmp_path / "ds"))[0]
    res = _check(rec, processor, max_image_side=1279)  # both sides resize alike: still identical
    assert res["ok"]
    ex = t.SFTExamples([rec], max_image_side=640, style="compact", history_k=8, coord_space="norm1000",
                       system_prompt_template=MEMORY_PROMPT)[0]
    assert ex["images"][-1].size == (640, 360)


def test_collate_refuses_to_truncate(tmp_path, processor):
    rec = t.load_dataset_records(make_dataset(tmp_path / "ds"))[0]
    ex = t.SFTExamples([rec], max_image_side=1280, style="compact", history_k=8, coord_space="norm1000",
                       system_prompt_template=MEMORY_PROMPT)[0]
    collate = t.make_collate(processor, template_kwargs={"enable_thinking": False}, max_seq_len=512)
    with pytest.raises(ValueError, match="refusing to truncate"):
        collate([ex])
    assert Path(rec["_root"]).exists()


def test_parity_at_the_student_image_scale(tmp_path, processor):
    """Student config: image_scale 1.5, image_max_side 1920 -> 1920x1080 PNGs -> a 68x120 grid (2040 tokens)."""
    rec = t.load_dataset_records(make_dataset(tmp_path / "ds"))[1]
    res = _check(rec, processor, image_scale=1.5, max_image_side=1920)
    assert res["ok"], {k: v for k, v in res["checks"].items() if not v["ok"]}
    assert res["checks"]["images_equal"]["sizes"] == [[1920, 1080], [1920, 1080]]
    assert res["checks"]["image_grid_equal"]["training"] == [[1, 68, 120], [1, 68, 120]]
    assert res["checks"]["no_truncation"]["image_tokens"] == 2 * 2040
