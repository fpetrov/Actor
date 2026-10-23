"""Versioned self-identity features from the same permitted event, without chat history."""
from ._legacy_projection import project as project_v4


def project(row):
    value = project_v4(row)
    event = row["input"]["event"]
    raw = event["entry"]["payload"]
    owner = event["entry"].get("entity_id")
    payload = dict(value.event.payload)
    if value.event.kind == "message_received":
        sender, recipient = raw.get("sender_actor_id"), raw.get("recipient_actor_id")
        if owner and recipient and owner != recipient:
            raise ValueError("Message recipient does not match the receiving actor")
        payload["from_self"] = sender == recipient if sender and recipient else None
    elif value.event.kind == "kill_presentation":
        victim = raw.get("victim_actor_id")
        payload["victim_is_self"] = victim == owner if victim and owner else None
    return value.model_copy(update={"event": value.event.model_copy(update={"payload": payload})})
