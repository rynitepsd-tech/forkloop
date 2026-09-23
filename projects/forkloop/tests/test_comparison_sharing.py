"""Sharing must preserve evidence without copying raw controller material."""
from __future__ import annotations

import base64
import hashlib
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from forkloop.backends.fake import FakeBackend
from forkloop.cli import main
from forkloop.comparison import PolicyVariant, run_comparison
from forkloop.policies.scripted import ScriptedPolicy
from forkloop.world import load_world


async def test_bundle_crops_copies_and_keeps_all_cell_links(tmp_path):
    world = load_world("toy-counter")
    backend = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=1, gui_factory=world.gui_factory())
    source = tmp_path / "comparison"
    variants = [PolicyVariant(label, {"policy": "scripted", "version": "test", "options": {}},
                              lambda: ScriptedPolicy([])) for label in ("A", "B")]
    try:
        await run_comparison(world, backend, variants, [11], output=source, settle_s=0)
    finally:
        backend.cleanup()
    (source / "private-controller.txt").write_text("PRIVATE_CONTROLLER_SENTINEL")
    shots = list(source.glob("runs/*/episodes/*/shots/*.png"))
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in shots}
    bundle = tmp_path / "share"
    assert main(["compare-report", str(source), "--format", "html", "--bundle", str(bundle), "--crop-top", "10"]) == 0
    files = [p for p in bundle.rglob("*") if p.is_file()]
    assert {p.relative_to(bundle).as_posix() for p in files} == {
        "comparison.html", "runs/A/episodes/A-000000/report.html", "runs/B/episodes/B-000000/report.html"
    }
    summary = (bundle / "comparison.html").read_text()
    for arm in ("A", "B"):
        relative = f"runs/{arm}/episodes/{arm}-000000/report.html"
        assert f'href="{relative}"' in summary
        report = (bundle / relative).read_text()
        embedded = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', report)
        original = next(p for p in shots if p.parts[-4] == "episodes" and p.parts[-3] == f"{arm}-000000")
        with Image.open(original) as image:
            expected_size = (image.width, image.height - 10)
        assert embedded
        for data in embedded:
            with Image.open(io.BytesIO(base64.b64decode(data))) as image:
                assert image.size == expected_size
    assert originals == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in shots}
    assert all("PRIVATE_CONTROLLER_SENTINEL" not in p.read_text() for p in files)
    # A second export must not replace a user's reviewed bundle.
    assert main(["compare-report", str(source), "--format", "html", "--bundle", str(bundle)]) == 4
    assert (bundle / "comparison.html").read_text() == summary


def test_crop_without_bundle_is_rejected_before_reading_evidence(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["compare-report", str(tmp_path / "missing"), "--format", "html", "--crop-top", "114"])
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())
