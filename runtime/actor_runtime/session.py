"""One ordered, negotiated game stream with a generic typed developer API."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import logging
import time

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from google.protobuf.any_pb2 import Any
from google.protobuf.message import Message

from .errors import ActorError
from .extensions import ExtensionRegistry, type_url
from .mailbox import OutboundMailbox
from .world import CachedPayload, WorldState
from .privacy import SessionPrivacy

LOG = logging.getLogger("actor_runtime")


def wire_error(error: ActorError):
    return core.ProtocolError(code=error.code, message=error.message,
                              extension_id=error.details.get("extension_id", ""),
                              required_version=error.details.get("required_version", ""),
                              schema_hash=error.details.get("schema_hash", ""))


class RuntimeSession:
    def __init__(self, registry: ExtensionRegistry, *, command_timeout_ms=250):
        self.registry = registry
        self.command_timeout_ms = command_timeout_ms
        self.session_id = ""
        self.hello = None
        self.manifest = None
        self.negotiated = None
        self.capabilities = {}
        self.extensions = {}
        self.world: WorldState | None = None
        self.event_router = None
        self.executable_plan = None
        self.trace_sink = None
        self.trace_errors = 0
        self.mailbox = OutboundMailbox()
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.last_received_sequence = 0
        self.last_sent_sequence = 0
        self.next_command_id = 0
        self.pending_commands: OrderedDict[int, tuple[float, float]] = OrderedDict()
        self.command_results = deque(maxlen=64)
        self.command_result_rtt_ms = deque(maxlen=2048)
        self.commands_sent = 0
        self.observation_frames = 0
        self.events_received = 0
        self.errors = deque(maxlen=64)
        self.received_at = time.monotonic()
        self.privacy = SessionPrivacy()

    def register_known_secret(self, value, *, expires_at_unix_ms=None):
        self.privacy.register_known_secret(value, expires_at_unix_ms=expires_at_unix_ms)

    def emit_trace(self, trace_phase, **fields):
        """Optional local diagnostic sink; never an actuator or a public log."""
        if self.trace_sink is not None:
            try:
                self.trace_sink(trace_phase, fields)
            except Exception:
                self.trace_errors += 1

    async def accept(self, message: wire.GameToRuntime):
        if not message.session_id or len(message.session_id) > 128:
            raise ActorError("SessionInvalid", "Session identity is missing or too long")
        if not self.session_id:
            self.session_id = message.session_id
        elif self.session_id != message.session_id:
            raise ActorError("SessionMismatch", "Message belongs to a different session")
        if message.sequence == 0:
            raise ActorError("SequenceInvalid", "Stream sequence starts at one")
        if message.sequence <= self.last_received_sequence:
            return False
        self.last_received_sequence = message.sequence
        self.received_at = time.monotonic()
        body = message.WhichOneof("body")
        if self.hello is None:
            if body != "hello":
                raise ActorError("HandshakeRequired", "First game message must be Hello")
            hello = message.hello
            if hello.protocol_version.major != 1:
                raise ActorError("ProtocolVersionMismatch", "ACTOR Core major version must be one")
            if not hello.self_actor_id or not hello.game_id:
                raise ActorError("HelloInvalid", "Game and controlled actor identities are required")
            self.hello = core.GameHello()
            self.hello.CopyFrom(hello)
            return True
        if body == "hello":
            raise ActorError("HandshakeInvalid", "Hello can only be sent once")
        if self.manifest is None:
            if body != "manifest":
                raise ActorError("ManifestRequired", "Hello must be followed by CapabilityManifest")
            self._accept_manifest(message.manifest)
            await self.world.notify()
            return True
        if body == "manifest":
            raise ActorError("ManifestInvalid", "Changing capabilities requires a fresh session")
        if body == "observation":
            frame = message.observation
            if len(frame.observations) > 256:
                raise ActorError("FrameTooLarge", "Observation frame exceeds 256 envelopes")
            # Installed typed payload policies also label private observations.
            # Ordinary frames do not invoke classification. A running accepted
            # plan may nominate one fresh observation when its condition first holds.
            observations = [self.event_router.prepare_entry(self._decode(item,
                core.CAPABILITY_KIND_OBSERVATION, message.sequence, frame.revision))
                for item in frame.observations]
            observations = [item for item in observations if item is not None]
            if self.world.apply_observations(observations, message.sequence, frame.revision):
                self.observation_frames += 1
                if self.executable_plan is not None:
                    plan = self.executable_plan
                    for entry in plan.observation_candidates(observations, self.world.self_actor_id):
                        self.event_router.ingest(entry, plan=plan)
                await self.world.notify()
        elif body == "event":
            item = self._decode(message.event, core.CAPABILITY_KIND_EVENT,
                                message.sequence, message.event.revision)
            item = self.event_router.prepare_entry(item)
            if item is None:
                return True
            if not self.world.retain_event(item):
                return True
            # Live bounded ingress precedes the diagnostic historical ring.
            self.event_router.ingest(item)
            self.world.apply_event(item, retained=True)
            self.events_received += 1
            LOG.info("Event capability=%s entity=%s revision=%s", item.capability_id,
                     item.entity_id, item.revision)
            LOG.debug("Event payload content omitted capability=%s", item.capability_id)
            await self.world.notify()
        elif body == "command_result":
            pending = self.pending_commands.pop(message.command_result.command_id, None)
            if pending is not None:
                self.command_result_rtt_ms.append((time.monotonic() - pending[1]) * 1000)
            result = core.CommandResult()
            result.CopyFrom(message.command_result)
            self.command_results.append(result)
            self.emit_trace("command_result", command_id=result.command_id,
                            status=result.status, error_code=result.error.code)
            if result.status in (core.COMMAND_STATUS_REJECTED, core.COMMAND_STATUS_TIMED_OUT):
                LOG.warning("Command %s status=%s code=%s", result.command_id,
                            result.status, result.error.code)
        elif body == "heartbeat":
            pass
        elif body == "error":
            self.errors.append({"code": message.error.code, "message": message.error.message})
            LOG.warning("Game error code=%s", message.error.code)
        else:
            raise ActorError("MessageInvalid", "Stream message has no recognised body")
        return True

    def _accept_manifest(self, manifest: core.CapabilityManifest):
        if (manifest.game_id != self.hello.game_id or
                manifest.game_version != self.hello.game_version or
                manifest.self_actor_id != self.hello.self_actor_id or
                manifest.protocol_version != self.hello.protocol_version):
            raise ActorError("ManifestInvalid", "Manifest identity differs from Hello")
        if len(manifest.capabilities) > 128 or len(manifest.extensions) > 32:
            raise ActorError("ManifestInvalid", "Manifest exceeds capability/extension limits")
        negotiated = self.registry.negotiate(manifest.extensions)
        extensions = {item.extension_id: item for item in manifest.extensions}
        capabilities = {}
        for item in manifest.capabilities:
            if not item.capability_id or item.capability_id in capabilities:
                raise ActorError("ManifestInvalid", "Capability IDs must be unique and nonempty")
            if item.extension_id not in extensions:
                raise ActorError("ManifestInvalid", "Capability refers to an undeclared extension")
            if item.kind not in (core.CAPABILITY_KIND_OBSERVATION, core.CAPABILITY_KIND_COMMAND,
                                 core.CAPABILITY_KIND_EVENT, core.CAPABILITY_KIND_SKILL):
                raise ActorError("ManifestInvalid", "Capability has an invalid kind")
            payload_url = item.input_type_url if item.kind in (core.CAPABILITY_KIND_COMMAND,
                                                             core.CAPABILITY_KIND_SKILL) else item.output_type_url
            if not payload_url:
                raise ActorError("ManifestInvalid", "Capability payload type is required")
            if item.extension_id in negotiated.available:
                for declared_url in (item.input_type_url, item.output_type_url):
                    if not declared_url:
                        continue
                    if self.registry.resolve_type(declared_url) is None:
                        raise ActorError("CapabilityTypeMissing", "Installed extension cannot resolve declared type",
                                         {"type_url": declared_url})
                    if not self.registry.package_has_type(item.extension_id, declared_url):
                        raise ActorError("ManifestInvalid", "Capability type is absent from its extension descriptor",
                                         {"extension_id": item.extension_id, "type_url": declared_url})
            capabilities[item.capability_id] = item
        self.manifest = core.CapabilityManifest()
        self.manifest.CopyFrom(manifest)
        self.negotiated, self.extensions, self.capabilities = negotiated, extensions, capabilities
        self.world = WorldState(manifest.self_actor_id)
        from .events import EventRouter
        self.event_router = EventRouter(self)
        self.mailbox.put_control(wire.RuntimeToGame(hello=core.RuntimeHello(
            protocol_version=core.ProtocolVersion(major=1, minor=0), accepted=True)))
        self.ready.set()
        LOG.info("Unity connected game=%s version=%s protocol=%s.%s actor=%s",
                 manifest.game_id, manifest.game_version, manifest.protocol_version.major,
                 manifest.protocol_version.minor, manifest.self_actor_id)
        LOG.info("Loaded extensions=%s capabilities=%s", sorted(negotiated.available),
                 sorted(capabilities))
        for warning in negotiated.warnings:
            LOG.warning("Optional extension opaque: %s", warning)

    def _decode(self, item, kind, sequence, revision):
        capability = self.capabilities.get(item.capability_id)
        extension = self.extensions.get(item.extension_id)
        if capability is None:
            raise ActorError("UnknownCapability", "Payload refers to an undeclared capability")
        if (capability.kind != kind or capability.extension_id != item.extension_id or
                extension is None or extension.extension_version != item.extension_version):
            raise ActorError("EnvelopeInvalid", "Envelope metadata differs from negotiated capability")
        if capability.output_type_url != item.payload.type_url:
            raise ActorError("PayloadTypeMismatch", "Payload type differs from advertised output type")
        if not item.entity_id or len(item.entity_id) > 128:
            raise ActorError("EntityInvalid", "Payload entity identity is invalid")
        decoded = self.registry.decode(item.payload,
                                       opaque=item.extension_id in self.negotiated.opaque)
        return CachedPayload(item.entity_id, item.capability_id, sequence, revision, decoded)

    def command(self, capability_id: str, payload: Message, *, target_actor_id: str | None = None,
                ttl_ms: int | None = None) -> int:
        if not self.ready.is_set() or self.closed.is_set() or self.mailbox.closed:
            raise ActorError("SessionUnavailable", "Game session is not connected")
        capability = self.capabilities.get(capability_id)
        if capability is None or capability.kind != core.CAPABILITY_KIND_COMMAND:
            raise ActorError("UnknownCapability", "Requested command is unavailable")
        if capability.extension_id not in self.negotiated.available:
            raise ActorError("ExtensionUnavailable", "Cannot issue commands through an opaque extension")
        if type_url(type(payload)) != capability.input_type_url:
            raise ActorError("PayloadTypeMismatch", "Command type differs from advertised input type")
        lifetime = self.command_timeout_ms if ttl_ms is None else ttl_ms
        if not 1 <= lifetime <= 5000:
            raise ActorError("CommandTimeoutInvalid", "Command TTL must be between 1 and 5000 ms")
        target = target_actor_id or self.world.self_actor_id
        if target != self.world.self_actor_id:
            raise ActorError("CommandTargetInvalid", "This connection controls its own actor only")
        self.next_command_id += 1
        packed = Any()
        packed.Pack(payload)
        command = core.CommandEnvelope(command_id=self.next_command_id, capability_id=capability_id,
                    extension_id=capability.extension_id,
                    extension_version=self.extensions[capability.extension_id].extension_version,
                    target_actor_id=target, ttl_ms=lifetime, payload=packed,
                    issued_at_unix_ms=int(time.time() * 1000))
        outgoing = wire.RuntimeToGame(command=command)
        if capability.command_delivery == core.COMMAND_DELIVERY_LATEST:
            self.mailbox.put_latest((target, capability_id), outgoing)
        else:
            self.mailbox.put_control(outgoing)
        self.emit_trace("command_queued", command_id=command.command_id,
                        capability_id=capability_id, ttl_ms=lifetime)
        return command.command_id

    def require(self, capability_id: str, payload_type: type[Message]):
        capability = self.capabilities.get(capability_id)
        if capability is None:
            raise ActorError("CapabilityMissing", "Script requires an unavailable capability",
                             {"capability_id": capability_id})
        expected = (capability.input_type_url if capability.kind == core.CAPABILITY_KIND_COMMAND
                    else capability.output_type_url)
        if expected != type_url(payload_type):
            raise ActorError("PayloadTypeMismatch", "Script capability type differs")
        if capability.extension_id not in self.negotiated.available:
            raise ActorError("ExtensionUnavailable", "Script requires a decoded extension")

    def fail(self, error: ActorError):
        self.errors.append(error.as_dict())
        LOG.error("Session error %s", self.privacy.public(error.as_dict()))
        try:
            self.mailbox.terminate(wire.RuntimeToGame(error=wire_error(error)),
                                   wire.RuntimeToGame(disconnect=core.Disconnect(reason=error.code)))
        except BaseException:
            self.mailbox.close()
            raise

    def disconnect(self, reason: str):
        try:
            self.mailbox.terminate(wire.RuntimeToGame(disconnect=core.Disconnect(reason=reason)))
        except BaseException:
            self.mailbox.close()
            raise

    async def outgoing(self):
        while True:
            message = await self.mailbox.get()
            if message is None:
                return
            if message.WhichOneof("body") == "command":
                command = message.command
                if int(time.time() * 1000) - command.issued_at_unix_ms >= command.ttl_ms:
                    continue
                self.commands_sent += 1
                now = time.monotonic()
                self.pending_commands[command.command_id] = (now + command.ttl_ms / 1000, now)
                while len(self.pending_commands) > 64:
                    self.pending_commands.popitem(last=False)
            self.last_sent_sequence += 1
            message.session_id = self.session_id
            message.sequence = self.last_sent_sequence
            yield message

    def expire_results(self):
        now = time.monotonic()
        for command_id, (deadline, _) in tuple(self.pending_commands.items()):
            if now >= deadline:
                self.pending_commands.pop(command_id)
                self.command_results.append(core.CommandResult(
                    command_id=command_id, status=core.COMMAND_STATUS_TIMED_OUT,
                    error=core.ProtocolError(code="CommandResultTimeout", message="Game result deadline elapsed")))

    def summary(self):
        return self.privacy.public({"session_id": self.session_id,
                "game_id": self.manifest.game_id if self.manifest else "",
                "game_version": self.manifest.game_version if self.manifest else "",
                "protocol_version": ({"major": self.manifest.protocol_version.major,
                                      "minor": self.manifest.protocol_version.minor}
                                     if self.manifest else None),
                "actor_id": self.world.self_actor_id if self.world else "",
                "loaded_extensions": sorted(self.negotiated.available) if self.negotiated else [],
                "opaque_extensions": sorted(self.negotiated.opaque) if self.negotiated else [],
                "capabilities": sorted(self.capabilities),
                "commands_sent": self.commands_sent, "command_values_replaced": self.mailbox.replaced,
                "observation_frames": self.observation_frames, "events_received": self.events_received,
                "last_received_sequence": self.last_received_sequence,
                "last_sent_sequence": self.last_sent_sequence,
                "command_result_rtt_ms": list(self.command_result_rtt_ms), "errors": list(self.errors),
                "privacy": self.privacy.report(),
                "inboxes": {cap: box.report() for cap, box in self.world.inboxes.items()} if self.world else {}})

    def close(self):
        self.mailbox.close()
        self.pending_commands.clear()
        self.closed.set()
