import json
import sys
from types import SimpleNamespace

import pytest
import yaml

from forkloop.policy_config import configure_policy, load_config


def test_keys_are_read_at_construction_and_never_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_MODEL_KEY", "private-value")
    policy = configure_policy({
        "name": "local", "policy": "student", "api_key_env": "LOCAL_MODEL_KEY",
        "options": {"base_url": "http://localhost:8000/v1", "model": "local-model"},
    }, tmp_path)
    assert "private-value" not in json.dumps(policy.identity)
    assert "LOCAL_MODEL_KEY" not in json.dumps(policy.identity)


@pytest.mark.parametrize("url", ["https://name:secret@example.com/v1", "https://example.com/v1?key=secret"])
def test_credential_bearing_endpoint_is_rejected_before_construction(tmp_path, url):
    with pytest.raises(ValueError):
        configure_policy({"name": "unsafe", "policy": "student", "options": {
            "base_url": url, "model": "local-model",
        }}, tmp_path)


def test_duplicate_seeds_cannot_inflate_comparison_denominator(tmp_path):
    config = {"version": 1, "backend": "fake", "seeds": [200, 200], "variants": [
        {"name": "a", "policy": "scripted"}, {"name": "b", "policy": "scripted"},
    ]}
    path = tmp_path / "comparison.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="Duplicate seeds"):
        load_config(path)


def test_prompt_file_content_not_filename_identifies_policy(tmp_path):
    path = tmp_path / "prompt.txt"
    spec = {"name": "model", "policy": "student", "system_prompt_file": "prompt.txt",
            "options": {"base_url": "http://localhost:8000/v1", "model": "local"}}
    path.write_text("First prompt")
    first = configure_policy(spec, tmp_path)
    path.write_text("Changed prompt")
    second = configure_policy(spec, tmp_path)
    assert first.identity["configuration_sha256"] != second.identity["configuration_sha256"]
    assert first.identity["options"]["system_prompt"] == "First prompt"


@pytest.mark.parametrize("options", [
    {"base_url": "http://api.openai.com/v1", "model": "gpt-5.6-luna"},
    {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna", "max_tokens": 0},
])
def test_invalid_hosted_configuration_fails_without_allocating(tmp_path, monkeypatch, options):
    monkeypatch.setenv("OPENAI_API_KEY", "private-value")
    with pytest.raises(ValueError):
        configure_policy({"name": "invalid", "policy": "student", "options": options}, tmp_path)


@pytest.mark.parametrize("options", [
    {"headers": {"X-API-Key": "private-value"}},
    {"authToken": "private-value"},
    {"nested": {1: "invalid JSON key"}},
    {"system_prompt": "Authenticate with Bearer private-value"},
    {"system_prompt": "Visit https://user:private-value@example.com"},
])
def test_public_configuration_rejects_nested_and_embedded_credentials(tmp_path, options):
    with pytest.raises(ValueError):
        configure_policy({"name": "unsafe", "policy": "student", "options": {
            "base_url": "http://localhost:8000/v1", "model": "local", **options,
        }}, tmp_path)


async def test_async_custom_factory_creates_independent_policies(tmp_path, monkeypatch):
    async def act(observation):
        return None, {}

    async def create(memory):
        memory.append("created")
        return SimpleNamespace(act=act, memory=memory)

    monkeypatch.setitem(sys.modules, "forkloop_test_external_policy", SimpleNamespace(create=create))
    configured = configure_policy({
        "name": "external", "factory": "forkloop_test_external_policy:create",
        "revision": "reviewed-source", "options": {"memory": []},
    }, tmp_path)
    first, second = await configured.factory(), await configured.factory()
    first.memory.append("first episode")
    assert second.memory == ["created"]
    assert configured.identity["options"]["memory"] == []


def test_openai_dns_absolute_name_cannot_bypass_hosted_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-value")
    with pytest.raises(ValueError):
        configure_policy({"name": "hosted", "policy": "student", "options": {
            "base_url": "https://api.openai.com./v1", "model": "unpriced-model",
        }}, tmp_path)
