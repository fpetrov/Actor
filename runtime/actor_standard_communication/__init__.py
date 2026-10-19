"""Optional independently versioned Standard Communication capability plugin."""
from importlib import resources

from actor.standard.communication.v1.communication_pb2 import (
    SendMessageCommand, MessageReceivedEvent, MessageDeliveryResultEvent,
    CommunicationPeersObservation, CommunicationPeer, MessageDeliveryStage,
    MESSAGE_DELIVERY_STAGE_UNSPECIFIED, MESSAGE_DELIVERY_STAGE_REJECTED,
    MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED, MESSAGE_DELIVERY_STAGE_CLIENT_RECEIVED,
)
from actor_runtime.extensions import ExtensionPackage

SEND_MESSAGE = "actor.standard.communication.send-message"
MESSAGE_RECEIVED = "actor.standard.communication.message-received"
MESSAGE_DELIVERY_RESULT = "actor.standard.communication.delivery-result"
PEERS = "actor.standard.communication.peers"


def extension_package():
    data = resources.files("actor.communication.metadata")
    return ExtensionPackage.from_resources(data.joinpath("communication.v1.json").read_bytes(),
        data.joinpath("communication.v1.pb").read_bytes(),
        (SendMessageCommand, MessageReceivedEvent, MessageDeliveryResultEvent,
         CommunicationPeersObservation, CommunicationPeer))
