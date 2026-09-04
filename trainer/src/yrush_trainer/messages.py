"""Validated values extracted from generated Protobuf messages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

from yrush.v1 import yrush_pb2 as pb

from yrush_trainer.config import PROTOCOL_VERSION, VOXEL_FEATURES
from yrush_trainer.errors import ProtocolStateError


@dataclass(frozen=True)
class RawObservation:
    session_id: str
    round_sequence: int
    policy_version: int
    client_tick: int
    observation_sequence: int
    action_sequence: int
    phase: int
    block_properties: bytes
    signed_target_height_difference: float
    forward_velocity: float
    strafe_velocity: float
    vertical_velocity: float
    fractional_x: float
    fractional_y: float
    fractional_z: float
    grounded: bool
    remaining_time_fraction: float
    yaw_residual_degrees: float
    pitch_degrees: float
    health_fraction: float
    air_fraction: float
    active_players: int
    total_players: int

    @classmethod
    def from_proto(cls, observation: Any) -> RawObservation:
        if int(observation.protocol_version) != PROTOCOL_VERSION:
            raise ProtocolStateError("observation uses the wrong protocol version")
        value = cls(
            session_id=str(observation.session_id),
            round_sequence=int(observation.round_sequence),
            policy_version=int(observation.policy_version),
            client_tick=int(observation.client_tick),
            observation_sequence=int(observation.observation_sequence),
            action_sequence=int(observation.action_sequence),
            phase=int(observation.phase),
            block_properties=bytes(observation.block_properties),
            signed_target_height_difference=float(observation.signed_target_height_difference),
            forward_velocity=float(observation.forward_velocity),
            strafe_velocity=float(observation.strafe_velocity),
            vertical_velocity=float(observation.vertical_velocity),
            fractional_x=float(observation.fractional_x),
            fractional_y=float(observation.fractional_y),
            fractional_z=float(observation.fractional_z),
            grounded=bool(observation.grounded),
            remaining_time_fraction=float(observation.remaining_time_fraction),
            yaw_residual_degrees=float(observation.yaw_residual_degrees),
            pitch_degrees=float(observation.pitch_degrees),
            health_fraction=float(observation.health_fraction),
            air_fraction=float(observation.air_fraction),
            active_players=int(observation.active_players),
            total_players=int(observation.total_players),
        )
        value.validate()
        return value

    @property
    def target_distance(self) -> float:
        return abs(self.signed_target_height_difference)

    def validate(self) -> None:
        if not self.session_id:
            raise ProtocolStateError("observation has a blank session ID")
        if self.round_sequence <= 0:
            raise ProtocolStateError("observation has an invalid round sequence")
        if self.phase not in {pb.ROUND_PHASE_ACTIVE, pb.ROUND_PHASE_COMPLETE}:
            raise ProtocolStateError("observation has an invalid phase")
        if len(self.block_properties) != VOXEL_FEATURES:
            raise ProtocolStateError(
                f"voxel payload has {len(self.block_properties)} bytes, expected {VOXEL_FEATURES}"
            )
        if any(value not in {0, 1} for value in self.block_properties):
            raise ProtocolStateError("voxel properties must be binary")
        numeric = (
            self.signed_target_height_difference,
            self.forward_velocity,
            self.strafe_velocity,
            self.vertical_velocity,
            self.fractional_x,
            self.fractional_y,
            self.fractional_z,
            self.remaining_time_fraction,
            self.yaw_residual_degrees,
            self.pitch_degrees,
            self.health_fraction,
            self.air_fraction,
        )
        if not all(isfinite(value) for value in numeric):
            raise ProtocolStateError("observation contains a non-finite value")
        fractions = (
            self.fractional_x,
            self.fractional_y,
            self.fractional_z,
            self.remaining_time_fraction,
            self.health_fraction,
            self.air_fraction,
        )
        if any(value < 0.0 or value > 1.0 for value in fractions):
            raise ProtocolStateError("observation fraction is outside [0, 1]")
        if self.yaw_residual_degrees < -45.0 or self.yaw_residual_degrees > 45.0:
            raise ProtocolStateError("yaw residual is outside [-45, 45]")
        if self.pitch_degrees < -90.0 or self.pitch_degrees > 90.0:
            raise ProtocolStateError("pitch is outside [-90, 90]")
        if self.total_players <= 0 or not 0 <= self.active_players <= self.total_players:
            raise ProtocolStateError("participant counts are invalid")

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["block_properties"] = list(self.block_properties)
        return values


@dataclass(frozen=True)
class RoundResult:
    session_id: str
    round_sequence: int
    policy_version: int
    client_tick: int
    observation_sequence: int
    outcome: int
    winner_uuid: str
    participant_count: int
    completion_time_seconds: float
    best_remaining_target_distance: float

    @classmethod
    def from_proto(cls, result: Any) -> RoundResult:
        if int(result.protocol_version) != PROTOCOL_VERSION:
            raise ProtocolStateError("result uses the wrong protocol version")
        value = cls(
            session_id=str(result.session_id),
            round_sequence=int(result.round_sequence),
            policy_version=int(result.policy_version),
            client_tick=int(result.client_tick),
            observation_sequence=int(result.observation_sequence),
            outcome=int(result.outcome),
            winner_uuid=str(result.winner_uuid),
            participant_count=int(result.participant_count),
            completion_time_seconds=float(result.completion_time_seconds),
            best_remaining_target_distance=float(result.best_remaining_target_distance),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if not self.session_id or self.round_sequence <= 0:
            raise ProtocolStateError("result identity is invalid")
        if self.outcome not in {
            pb.PLAYER_OUTCOME_WON,
            pb.PLAYER_OUTCOME_LOST,
            pb.PLAYER_OUTCOME_ELIMINATED,
            pb.PLAYER_OUTCOME_DRAW,
            pb.PLAYER_OUTCOME_STOPPED,
        }:
            raise ProtocolStateError("result outcome is invalid")
        if self.participant_count <= 0:
            raise ProtocolStateError("result participant count is invalid")
        if self.outcome in {pb.PLAYER_OUTCOME_WON, pb.PLAYER_OUTCOME_LOST}:
            if not self.winner_uuid:
                raise ProtocolStateError("winning result has no winner UUID")
        elif self.winner_uuid:
            raise ProtocolStateError("non-winning result contains a winner UUID")
        if not isfinite(self.completion_time_seconds) or self.completion_time_seconds < 0.0:
            raise ProtocolStateError("result completion time is invalid")
        if (
            not isfinite(self.best_remaining_target_distance)
            or self.best_remaining_target_distance < 0.0
        ):
            raise ProtocolStateError("result remaining distance is invalid")

    @property
    def outcome_name(self) -> str:
        return str(pb.PlayerOutcome.Name(self.outcome)).removeprefix("PLAYER_OUTCOME_")

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["outcome"] = self.outcome_name
        return values
