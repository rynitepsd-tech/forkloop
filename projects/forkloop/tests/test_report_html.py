"""The shared evidence file must preserve verdicts without executing or leaking artifacts."""
from __future__ import annotations
import asyncio

import json
import shutil
from html.parser import HTMLParser

import pytest
from PIL import Image, PngImagePlugin

from forkloop import cli
from forkloop.controls import _fixture_seed, _record
from forkloop.report import episode_report, run_report
from forkloop.report_html import html_report



class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


@pytest.fixture(scope="module")
def control(tmp_path_factory):
    """Generate report evidence without publishing private recorded episodes."""
    base = tmp_path_factory.mktemp("report-control")
    ep, verdict = asyncio.run(_record(base, "wrong_authorization", _fixture_seed(), base / "fake"))
    # Simulate partial retention in this disposable control, never in source evidence.
    steps_path = ep / "steps.jsonl"
    steps = [json.loads(line) for line in steps_path.read_text().splitlines()]
    steps.append({
        "i": 1, "action": {"type": "type", "text": verdict.details["appeal_auth_number"]["actual"]},
        "raw_action": "Constructed missing-frame example", "valid": True,
        "shot_before": "shots/missing_before.png", "shot_after": "shots/missing_after.png",
    })
    steps_path.write_text("\n".join(json.dumps(step) for step in steps) + "\n")
    return ep.parent.parent, ep


@pytest.fixture
def copied(tmp_path, control):
    source, episode = control
    root = tmp_path / "recorded"
    shutil.copytree(source, root)
    return root, root / "episodes" / episode.name


def test_verdict_and_partial_frames_survive_export(copied, tmp_path):
    _, ep = copied
    output = tmp_path / "shared.html"
    assert cli.main(["report", str(ep), "--format", "html", "--out", str(output)]) == 0
    document = Document(output.read_text())
    text = " ".join(document.text)
    detail = json.loads((ep / "verdict.json").read_text())["details"]["appeal_auth_number"]
    for value in ("WRONG_VALUE", detail["expected"], detail["actual"], "CONSTRUCTED CONTROL", "2 of 4"):
        assert value in text
    assert "RECORDED LIVE" not in text
    instruction = json.loads((ep / "manifest.json").read_text())["instruction"]
    assert instruction in text  # Local application URLs and their punctuation are task evidence.
    images = [attrs for tag, attrs in document.tags if tag == "img"]
    assert len(images) == 2
    assert all(attrs["src"].startswith("data:image/png;base64,") and attrs.get("alt") for attrs in images)
    assert not any(tag in ("script", "iframe", "link") for tag, _ in document.tags)
    assert all(attrs.get("href", "#").startswith("#") for _, attrs in document.tags)
    assert "/Users/" not in text and "session_ledger" not in text


def test_untrusted_model_text_is_text_not_markup_and_infrastructure_is_omitted(copied):
    root, ep = copied
    payload = '<script>window.forkloopInjected=true</script><img src=x onerror="window.forkloopInjected=true">'
    steps_file = ep / "steps.jsonl"
    steps = [json.loads(row) for row in steps_file.read_text().splitlines()]
    steps[-1]["raw_action"] = (payload + ' https://private.example/preview?token=capability /Users/private/secret.txt sk-secretvalue'
                              ' http://localhost:8080/preview?token=LOCAL_SECRET http://USER_SECRET:pass@127.0.0.1/'
                              ' authorization: AUTH-36G14538; Authorization: Bearer BEARER_SECRET; Authorization: Basic BASIC_SECRET')
    steps_file.write_text("\n".join(json.dumps(row) for row in steps))
    meta = json.loads((root / "run.json").read_text())
    meta["model"] = payload
    meta["private_key"] = "DO_NOT_EXPORT_THIS_FIELD"
    (root / "run.json").write_text(json.dumps(meta))
    source = html_report(ep)
    document = Document(source)
    assert payload in " ".join(document.text)
    assert "authorization: AUTH-36G14538" in " ".join(document.text)
    assert not any(tag == "script" or "onerror" in attrs for tag, attrs in document.tags)
    assert all("data:image/png;base64," in attrs["src"] for tag, attrs in document.tags if tag == "img")
    for forbidden in ("private.example", "/Users/private", "sk-secretvalue", "DO_NOT_EXPORT_THIS_FIELD", "LOCAL_SECRET", "USER_SECRET", "BEARER_SECRET", "BASIC_SECRET"):
        assert forbidden not in source


@pytest.mark.parametrize("reference", ["../outside.png", "shots/../../outside.png", "symlink_file", "symlink_directory"])
def test_screenshot_references_cannot_escape_evidence(copied, tmp_path, reference):
    _, ep = copied
    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2), "red").save(outside)
    if reference == "symlink_file":
        (ep / "shots/escape.png").symlink_to(outside)
        reference = "shots/escape.png"
    elif reference == "symlink_directory":
        shutil.rmtree(ep / "shots")
        (ep / "shots").symlink_to(tmp_path, target_is_directory=True)
        reference = "shots/outside.png"
    steps = [{"i": 0, "action": {"type": "done"}, "shot_before": reference}]
    (ep / "steps.jsonl").write_text(json.dumps(steps[0]) + "\n")
    document = Document(html_report(ep))
    assert not any(tag == "img" for tag, _ in document.tags)
    assert "rejected" in " ".join(document.text)


def test_png_metadata_is_not_shared(copied):
    _, ep = copied
    info = PngImagePlugin.PngInfo()
    info.add_text("infrastructure", "DO_NOT_SHARE_PNG_METADATA")
    Image.new("RGB", (2, 2)).save(ep / "shots/000_before.png", pnginfo=info)
    document = Document(html_report(ep))
    import base64
    for tag, attrs in document.tags:
        if tag == "img":
            assert b"DO_NOT_SHARE_PNG_METADATA" not in base64.b64decode(attrs["src"].split(",", 1)[1])


def test_sharing_crop_removes_pixels_without_changing_originals(copied, tmp_path):
    import base64
    import io

    _, ep = copied
    original = ep / "shots/000_before.png"
    image = Image.new("RGB", (4, 5), "green")
    image.paste("red", (0, 0, 4, 2))
    image.save(original)
    before = original.read_bytes()
    output = tmp_path / "cropped.html"
    assert cli.main(["report", str(ep), "--format", "html", "--crop-top", "2", "--out", str(output)]) == 0
    document = Document(output.read_text())
    images = [attrs for tag, attrs in document.tags if tag == "img"]
    shared = Image.open(io.BytesIO(base64.b64decode(images[0]["src"].split(",", 1)[1])))
    assert shared.size == (4, 3)
    assert shared.tobytes() == bytes((0, 128, 0)) * 12
    assert "top 2 pixels" in " ".join(document.text)
    assert original.read_bytes() == before

    # A crop that consumes a frame must omit it, never fall back to the original.
    document = Document(html_report(ep, crop_top=5))
    assert "crop removes the entire frame" in " ".join(document.text)
    assert len([tag for tag, _ in document.tags if tag == "img"]) == 1


@pytest.mark.parametrize("missing", ["absent", "raised"])
def test_missing_invariants_never_become_clean_side_effect_evidence(copied, missing):
    _, ep = copied
    verdict_path = ep / "verdict.json"
    verdict = json.loads(verdict_path.read_text())
    if missing == "absent":
        del verdict["details"]["no_collateral"]
    else:
        verdict["details"]["no_collateral"] = {"passed": False, "error": "database unavailable"}
        verdict["failed"].append("no_collateral")
    verdict_path.write_text(json.dumps(verdict))
    text = episode_report(ep)
    side_effect_line = next(line for line in text.splitlines() if line.startswith("side effects"))
    assert "none detected" not in side_effect_line
    assert "DETECTED" not in side_effect_line
    assert "no_collateral" in side_effect_line and "INCOMPLETE" in side_effect_line
    if missing == "raised":
        error_line = next(line for line in text.splitlines() if "check raised" in line)
        assert error_line.lstrip().startswith("????")
        assert "COLLATERAL_EDIT" not in error_line
    document = Document(html_report(ep))
    assert "UNAVAILABLE" in " ".join(document.text)
    assert "Check evidence is incomplete" in " ".join(document.text)


def test_missing_checksum_digest_is_unknown_not_zero_coverage(copied):
    _, ep = copied
    digest = ep / "baseline-digest.json"
    text = " ".join(Document(html_report(ep)).text)
    assert "Checksum scope: unavailable" in text
    assert "0 recorded tables" not in text
    assert "checksum table scope not recorded" in episode_report(ep)

    digest.write_text(json.dumps({"tables": {}}))
    assert "Checksum scope: 0 recorded tables" in " ".join(Document(html_report(ep)).text)
    assert "0 tables checksummed" in episode_report(ep)


def test_missing_verdict_and_origin_remain_unavailable_not_a_failure_rate(copied):
    root, ep = copied
    (root / "run.json").unlink()
    (ep / "verdict.json").unlink()
    source = html_report(root, all_episodes=True)
    text = " ".join(Document(source).text)
    assert "ORIGIN UNAVAILABLE" in text and "NO_VERDICT" in text
    assert "RECORDED LIVE" not in text and "Task accepted" not in text
    assert "rate unavailable" in run_report(root)


def test_html_destination_cannot_overwrite_verdict(copied):
    _, ep = copied
    verdict = ep / "verdict.json"
    before = verdict.read_bytes()
    with pytest.raises(SystemExit):
        cli.main(["report", str(ep), "--format", "html", "--out", str(verdict)])
    assert verdict.read_bytes() == before


def test_value_check_links_reach_unpictured_actions_and_preserved_frames(copied):
    _, ep = copied
    actual = json.loads((ep / "verdict.json").read_text())["details"]["appeal_auth_number"]["actual"]
    steps = [
        {"i": 7, "action": {"type": "type", "text": actual},
         "raw_action": '<script>untrusted_early_turn</script>',
         "shot_before": "shots/missing.png", "shot_after": "shots/000_before.png"},
        {"i": 8, "action": {"type": "done"}},
    ]
    (ep / "steps.jsonl").write_text("\n".join(json.dumps(step) for step in steps))
    source = html_report(ep, turns=0)
    document = Document(source)
    targets = {attrs["id"] for _, attrs in document.tags if "id" in attrs}
    links = [attrs["href"][1:] for tag, attrs in document.tags if tag == "a"]
    assert all(target in targets for target in links)
    # Follow the check's actual-value hint to its action, even without a before shot.
    check_start = source.index("<h2>Effects</h2>")
    trace_start = source.index("<h2>Recorded action trace</h2>")
    checks = Document(source[check_start:trace_start])
    step_target = next(attrs["href"][1:] for tag, attrs in checks.tags
                       if tag == "a" and "-step-" in attrs.get("href", ""))
    action_start = source.index(f'id="{step_target}"')
    action = Document(source[action_start:source.index("</li>", action_start)])
    assert actual in " ".join(action.text)
    assert "Before: Screenshot not preserved" in " ".join(action.text)
    assert "<script>untrusted_early_turn</script>" in " ".join(action.text)
    assert not any(tag == "script" for tag, _ in document.tags)
    assert any(tag == "a" and "-frame-" in attrs.get("href", "") for tag, attrs in action.tags)
    assert len([tag for tag, _ in document.tags if tag == "img"]) == 1


def test_constructed_control_cannot_be_presented_as_live_policy_performance(copied):
    root, ep = copied
    meta = json.loads((root / "run.json").read_text())
    meta["backend"] = "solari"  # A provider label must not override constructed provenance.
    meta["evidence_kind"] = "constructed_control"
    (root / "run.json").write_text(json.dumps(meta))
    source = html_report(ep)
    arrival = " ".join(Document(source[:source.index('<section')]).text)
    assert "CONSTRUCTED CONTROL" in arrival
    assert "NOT POLICY PERFORMANCE" in arrival
    assert "RECORDED LIVE" not in arrival
