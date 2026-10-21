"""Explicit session-only data and safe public/persistent projections.

This is a local retention boundary, not a sandbox for installed Python plugins.
"""
from collections import OrderedDict
from copy import deepcopy
import re
from typing import Literal

DataPolicy = Literal["public", "session_only", "sensitive"]
PRIVATE_POLICIES = {"session_only", "sensitive"}
_SECRET_KEYS = {"authorizationcode", "secret", "secrets", "password", "accesstoken", "refreshtoken", "apikey"}


def has_private_data(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return value.get("data_policy") in PRIVATE_POLICIES or any(has_private_data(item) for item in value.values())
    return isinstance(value, (list, tuple)) and any(has_private_data(item) for item in value)


def public_projection(value):
    """Never persist a private result's output/details, even through nesting."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        private = value.get("data_policy") in PRIVATE_POLICIES
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _SECRET_KEYS:
                result[key] = "[redacted]"
            elif private and key in ("output", "details", "value", "content"):
                result[key] = {"redacted": True}
            elif key == "message" and (private or has_private_data(value.get("skill_results", ()))):
                result[key] = None
            else:
                result[key] = public_projection(item)
        return result
    if isinstance(value, (list, tuple)):
        return [public_projection(item) for item in value]
    return deepcopy(value)


class SessionPrivacy:
    def __init__(self, *, max_secrets=128, max_secret_bytes=4096):
        self.max_secrets, self.max_secret_bytes = max_secrets, max_secret_bytes
        self._secrets = OrderedDict()
        self.has_private_data = False
        self.degraded = False
        self.private_activity = False
        self.activity_revealed = False

    def activity_id(self, value):
        return "[private_activity]" if self.private_activity and not self.activity_revealed and value else value

    def mark_private(self):
        self.has_private_data = True

    def register_known_secret(self, value, *, expires_at_unix_ms=None):
        if not isinstance(value, str) or not value:
            raise ValueError("A nonempty sensitive string is required")
        self.mark_private()
        # Expiry limits use of working data, not redaction: old diagnostics must
        # never reveal a code merely because its gameplay authorization expired.
        if len(value.encode("utf-8")) > self.max_secret_bytes or len(self._secrets) >= self.max_secrets and value not in self._secrets:
            self.degraded = True
            return
        self._secrets[value] = expires_at_unix_ms

    def public(self, value):
        projected = public_projection(value)
        def redact(item):
            if isinstance(item, str):
                if self.degraded:
                    return "[redacted]" if item else ""
                for secret in sorted(self._secrets, key=len, reverse=True):
                    item = item.replace(secret, "[redacted]")
                return item
            if isinstance(item, dict):
                result = {}
                for key, value in item.items():
                    # Schema keys are not free-form message text. Overflow must
                    # not collapse every public field into one redacted key.
                    public_key = str(key)
                    for secret in self._secrets:
                        if len(secret) >= 16:
                            public_key = public_key.replace(secret, "[redacted]")
                    if self.private_activity and not self.activity_revealed and key in ("skill_id", "active_skill_id"):
                        result[public_key] = self.activity_id(value)
                    elif self.degraded and key in ("output", "details", "value", "content", "arguments"):
                        result[public_key] = {"redacted": True}
                    elif key == "status" and value in ("pending", "running", "succeeded", "failed", "cancelled", "timed_out"):
                        result[public_key] = value
                    else:
                        result[public_key] = redact(value)
                # A model may paraphrase or quote a fragment of private input.
                # Free-form Objective prose is withheld after private knowledge.
                if self.has_private_data and "objective_id" in item and "message" in item:
                    result["message"] = None
                return result
            if isinstance(item, list):
                return [redact(value) for value in item]
            return item
        return redact(projected)

    def report(self):
        return {"private_data_observed": self.has_private_data, "registered_secrets": len(self._secrets),
                "redaction_degraded": self.degraded}
