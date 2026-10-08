"""Reusable Standard capability bindings registered as a normal ACTOR plugin."""

from importlib import resources

from actor.standard.v1.transform2d_pb2 import Transform2DObservation
from actor.standard.v1.motion2d_pb2 import Motion2DObservation
from actor.standard.v1.locomotion2d_pb2 import Locomotion2DCommand

from actor_runtime.extensions import ExtensionPackage

TRANSFORM = "actor.standard.transform2d"
MOTION = "actor.standard.motion2d"
LOCOMOTION = "actor.standard.locomotion2d"


def extension_package() -> ExtensionPackage:
    data = resources.files("actor.metadata")
    return ExtensionPackage.from_resources(
        data.joinpath("standard.v1.json").read_bytes(),
        data.joinpath("standard.v1.pb").read_bytes(),
        (Transform2DObservation, Motion2DObservation, Locomotion2DCommand),
    )
