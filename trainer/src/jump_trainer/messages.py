"""Typed values extracted from generated Protobuf messages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

from jump_trainer.errors import ProtocolStateError


@dataclass(frozen=True)
class RawObservation:
    session_id: str
    episode_id: int
    client_tick: int
    server_tick: int
    observation_sequence: int
    action_sequence: int
    phase: int
    terminal_reason: int
    signed_wall_distance: float
    relative_feet_height: float
    vertical_velocity: float
    lane_velocity: float
    on_ground: bool
    elapsed_ticks: int

    @classmethod
    def from_proto(cls, observation: Any) -> RawObservation:
        value = cls(
            session_id=str(observation.session_id),
            episode_id=int(observation.episode_id),
            client_tick=int(observation.client_tick),
            server_tick=int(observation.server_tick),
            observation_sequence=int(observation.observation_sequence),
            action_sequence=int(observation.action_sequence),
            phase=int(observation.phase),
            terminal_reason=int(observation.terminal_reason),
            signed_wall_distance=float(observation.signed_wall_distance),
            relative_feet_height=float(observation.relative_feet_height),
            vertical_velocity=float(observation.vertical_velocity),
            lane_velocity=float(observation.lane_velocity),
            on_ground=bool(observation.on_ground),
            elapsed_ticks=int(observation.elapsed_ticks),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if not self.session_id:
            raise ProtocolStateError("observation has a blank session id")
        if self.episode_id <= 0:
            raise ProtocolStateError("observation has an invalid episode id")
        numeric = (
            self.signed_wall_distance,
            self.relative_feet_height,
            self.vertical_velocity,
            self.lane_velocity,
        )
        if not all(isfinite(value) for value in numeric):
            raise ProtocolStateError("observation contains a non-finite value")
        if self.elapsed_ticks < 0 or self.elapsed_ticks > 200:
            raise ProtocolStateError("observation elapsed ticks are outside 0..200")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
