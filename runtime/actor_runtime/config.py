"""One validated, local configuration for the cognitive layer (never wire data)."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RouterConfig(Settings):
    enabled: bool = True
    max_queue_size: int = Field(default=128, ge=4, le=4096)
    max_pending_events: int = Field(default=128, ge=4, le=4096)
    max_dedup_keys: int = Field(default=4096, ge=128, le=65536)
    max_event_bytes: int = Field(default=16384, ge=512, le=65536)
    aggregation_window: float = Field(default=2.0, ge=0, le=60)
    decision_cooldown: float = Field(default=2.0, ge=0, le=60)
    max_replans: int = Field(default=8, ge=1, le=64)
    stale_event_seconds: float = Field(default=60, gt=0, le=3600)


class ClassifierConfig(Settings):
    provider: str = "rules"
    timeout: float = Field(default=1.5, gt=0, le=30)
    confidence_threshold: float = Field(default=0.85, ge=0, le=1)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    requests_per_minute: int = Field(default=30, ge=1, le=600)
    retries: int = Field(default=1, ge=0, le=2)
    max_request_bytes: int = Field(default=8192, ge=1024, le=16384)
    model: str = "jev-1.13.0"


class ContextConfig(Settings):
    max_external_context_bytes: int = Field(default=32768, ge=4096, le=131072)
    max_observations: int = Field(default=24, ge=1, le=128)
    max_events: int = Field(default=12, ge=1, le=64)
    max_memory_items: int = Field(default=6, ge=0, le=32)
    max_event_payload_bytes: int = Field(default=4096, ge=256, le=8192)
    stale_observation_seconds: float = Field(default=3, gt=0, le=300)


class MemoryConfig(Settings):
    enabled: bool = True
    path: str | None = None
    profile: str | None = Field(default=None, min_length=1, max_length=128)
    max_entries: int = Field(default=1000, ge=8, le=100000)
    max_payload_bytes: int = Field(default=4096, ge=512, le=16384)
    ttl_seconds: float = Field(default=604800, gt=0, le=31536000)
    max_retrieved: int = Field(default=6, ge=1, le=32)


class BrainConfig(Settings):
    decision_timeout: float = Field(default=120, gt=0, le=600)
    objective_timeout: float = Field(default=600, gt=0, le=3600)
    max_decisions: int = Field(default=16, ge=1, le=64)
    usage_warning_tokens: int = Field(default=100000, ge=1000)
    usage_limit_tokens: int = Field(default=500000, ge=1000)


class CognitiveConfig(Settings):
    router: RouterConfig = Field(default_factory=RouterConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    brain: BrainConfig = Field(default_factory=BrainConfig)
    # Trusted, opt-in policy declarations; never inferred from game message text.
    policy_tags: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path | None):
        return cls.model_validate_json(path.read_text()) if path else cls()
