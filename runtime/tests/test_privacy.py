import json

import pytest

from actor_runtime.privacy import SessionPrivacy, public_projection
from actor_runtime.skills import SkillResult
from actor_runtime.memory import MemoryStore


def test_explicit_private_result_hides_nested_output_details_and_objective_model_message():
    secret = "bd4316f01ee9453be13caa422ae576dd"
    result = SkillResult(skill_id="test.private", status="failed", data_policy="sensitive",
        output={"wrapper": [secret]}, details={"unusual": {"server_prose": f"code:{secret}"}})
    payload = {"objective_id": "objective-1", "message": f"The answer was {secret}",
               "skill_results": [result.model_dump(mode="json")]}
    safe = public_projection(payload)
    assert safe["message"] is None
    assert safe["skill_results"][0]["output"] == safe["skill_results"][0]["details"] == {"redacted": True}
    assert secret not in json.dumps(safe)


def test_registered_secret_redacts_nested_keys_values_and_public_model_prose_after_expiry():
    secret = "4a9c27ab99ed24014f2c994fd89d2504"
    privacy = SessionPrivacy()
    privacy.register_known_secret(secret, expires_at_unix_ms=1)
    safe = privacy.public({f"key-{secret}": [{"details": {"note": f"Value={secret}."}}],
        "outcome": {"objective_id": "objective-1", "message": "A partial fragment of private information"}})
    assert secret not in json.dumps(safe) and safe["outcome"]["message"] is None
    assert privacy.report()["registered_secrets"] == 1


def test_secret_registry_is_bounded_and_overflow_fails_closed_for_unknown_strings():
    privacy = SessionPrivacy(max_secrets=1)
    privacy.register_known_secret("first-sensitive-value")
    privacy.register_known_secret("second-sensitive-value")
    safe = privacy.public({"unexpected": "second-sensitive-value"})
    assert privacy.degraded and privacy.report()["registered_secrets"] == 1
    assert "second-sensitive-value" not in json.dumps(safe)
    shaped = privacy.public({"status": "succeeded", "message": "unknown secret", "details": {"unknown-secret-key": "secret"}})
    assert set(shaped) == {"status", "message", "details"}
    assert shaped["status"] == "succeeded" and shaped["details"] == {"redacted": True}


async def test_direct_memory_result_apis_defensively_strip_sensitive_data_without_runner(tmp_path):
    code = "a827f609cfb41ae16c705ab3d7fe50ef"
    store = MemoryStore(game_id="privacy-fixture", session_id="session-1", path=tmp_path / "memory.sqlite", profile="agent-1")
    result = SkillResult(skill_id="test.private", status="succeeded", data_policy="session_only",
        output={"text": code}, details={"nested": {"echo": code}})
    try:
        await store.remember_skill_result(result)
        await store.remember_objective_result({"objective_id": "objective-1", "status": "succeeded",
            "message": f"Finished with {code}", "skill_results": [result.model_dump(mode="json")]})
        entries = await store.retrieve()
        assert code not in json.dumps([entry.model_dump(mode="json") for entry in entries])
    finally:
        await store.close()
    assert code.encode() not in (tmp_path / "memory.sqlite").read_bytes()
