"""Manual official optional SDK contract check with an offline HTTP transport."""
import asyncio
import json
from pathlib import Path

import httpx2
from google.protobuf.struct_pb2 import Struct
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from actor_jev.classifier import JevEventClassifier
from actor_runtime.classifiers import ClassificationContext, EventRuleRegistry
from actor_runtime.events import RoutedEvent
from actor_runtime.objectives import Objective
from actor_runtime.world import CachedPayload


async def main():
    captured = []
    def respond(request):
        body = json.loads(request.content)
        captured.append({"path": request.url.path, "model": body["model"],
                         "questions": {key: value["type"] for key, value in body["questions"].items()},
                         "state_bytes": len(json.dumps(body["state"]).encode())})
        return httpx2.Response(200, json={"model": "jev-1.13.0",
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "answers": {
                "relevance": {"type": "choice", "choice": "relevant", "confidence": .95,
                    "probabilities": {"relevant": .98, "unrelated": .01, "uncertain": .01}},
                "urgency": {"type": "score", "score": 1.0, "confidence": .95,
                    "legend": {"0": "routine", "1": "important", "2": "critical"},
                    "probabilities": {"0": .01, "1": .98, "2": .01}},
                "remember": {"type": "noul", "noul": .99},
                "interrupt": {"type": "noul", "noul": .01},
                "review": {"type": "noul", "noul": .99},
            }})
    def client():
        return AsyncTypeSafeClient(api_key="test-only-offline-sdk-contract", model="jev-1.13.0",
            base_url="https://api.typesafe.ai", transport=httpx2.MockTransport(respond),
            retry=RetryPolicy(max_retries=0))
    provider = JevEventClassifier(rules=EventRuleRegistry(), client_factory=client)
    payload = Struct()
    payload.update({"fact": "A visible resource became unavailable"})
    event = RoutedEvent("contract:1", "contract", CachedPayload("actor", "demo.fact", 1, 1, payload),
                        "demo", "1.0.0", "type.googleapis.com/google.protobuf.Struct")
    try:
        result = await provider.classify(ClassificationContext(Objective(text="Observe resource availability")), event)
        assert result.provider == "jev" and result.should_remember and result.should_request_brain_review
        assert not result.should_interrupt_skill and len(captured) == 1
    finally:
        await provider.close()
    report = {"passed": True, "scope": "Official typesafe-sdk0.7.5 with offline HTTP mock; no live inference",
              "requests": captured, "assessment": result.model_dump(mode="json"), "provider": provider.report()}
    output = Path(__file__).resolve().parents[2]/"build/event-validation/jev-sdk-contract.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({"passed": True, "report": str(output), "requests": len(captured)}))


if __name__ == "__main__":
    asyncio.run(main())
