"""Structured errors shared by plugin loading, negotiation and stream validation."""

from dataclasses import dataclass, field


@dataclass
class ActorError(Exception):
    code: str
    message: str
    details: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}
