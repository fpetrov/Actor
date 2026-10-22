"""Local validated editorial policy. No game phase is built into Core."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)


class IdleConfig(Settings):
    enabled: bool = True
    after_seconds: float = Field(default=20, ge=5, le=120)
    jitter_fraction: float = Field(default=.25, ge=0, le=1)
    require_audible_silence: bool = True
    recent_window_seconds: float = Field(default=60, ge=5, le=600)
    allow_memory_callbacks: bool = True
    max_recent_facts: int = Field(default=4, ge=1, le=4)
    max_callback_facts: int = Field(default=2, ge=0, le=2)
    same_topic_repeat_seconds: float = Field(default=120, ge=20, le=3600)
    max_consecutive_without_new_material: int = Field(default=2, ge=1, le=4)
    empty_candidate_backoff_seconds: float = Field(default=20, ge=5, le=120)
    max_calls_per_rolling_minute: int = Field(default=2, ge=1, le=6)
    max_calls_per_hour: int = Field(default=40, ge=1, le=180)
    max_calls_per_match: int = Field(default=12, ge=1, le=60)
    context_target_tokens: int = Field(default=1200, ge=256, le=3500)
    # Conservative ledger reservation; the SDK turn API is not an output cap.
    max_output_tokens: int = Field(default=512, ge=64, le=1024)
    words_target: tuple[int, int] = (35, 55)
    max_preemption_wait_seconds: float = Field(default=1.5, ge=0, le=1.5)
    allowed_phases: tuple[str, ...] = ("Exploration",)


class CommentaryConfig(Settings):
    coalesce_ms: int = Field(default=500, ge=0, le=2000)
    normal_cooldown_seconds: float = Field(default=12, ge=0, le=120)
    normal_calls_per_minute: int = Field(default=3, ge=1, le=60)
    normal_burst_capacity: int = Field(default=2, ge=1, le=6)
    critical_extra_calls_per_minute: int = Field(default=2, ge=0, le=6)
    absolute_calls_per_rolling_minute: int = Field(default=6, ge=1, le=60)
    soft_calls_per_hour: int = Field(default=120, ge=1, le=1000)
    hard_calls_per_hour: int = Field(default=180, ge=1, le=2000)
    hard_calls_per_match: int = Field(default=60, ge=1, le=1000)
    max_parallel_llm_calls: int = Field(default=1, ge=1, le=1)
    max_pending_speech_segments: int = Field(default=2, ge=1, le=2)
    max_pending_speech_seconds: float = Field(default=12, ge=1, le=12)
    spoken_words_target: tuple[int, int] = (25, 45)
    context_target_tokens: int = Field(default=2000, ge=256, le=3500)
    client_context_hard_tokens: int = Field(default=3500, ge=256, le=3500)
    max_recent_chat_messages: int = Field(default=3, ge=0, le=3)
    max_recent_public_events: int = Field(default=12, ge=1, le=12)
    input_tokens_per_hour: int = Field(default=600000, ge=1000, le=20000000)
    output_tokens_per_hour: int = Field(default=60000, ge=1000, le=2000000)
    feed_stale_seconds: float = Field(default=3, gt=0, le=10)
    provider_timeout_seconds: float = Field(default=45, gt=0, le=120)
    interrupt_timeout_seconds: float = Field(default=7, gt=0, le=10)
    # A meeting comparison replaces one normal turn; it has no separate caller.
    analysis_enabled: bool = True
    analysis_max_calls_per_match: int = Field(default=3, ge=1, le=3)
    analysis_max_calls_per_meeting: int = Field(default=1, ge=1, le=1)
    idle: IdleConfig = Field(default_factory=IdleConfig)

    @model_validator(mode="after")
    def ordered(self):
        if self.soft_calls_per_hour > self.hard_calls_per_hour:
            raise ValueError("Soft call limit exceeds hard limit")
        for lower, upper in (self.spoken_words_target, self.idle.words_target):
            if not 1 <= lower <= upper <= 80: raise ValueError("Speech word targets must be bounded and ordered")
        return self
