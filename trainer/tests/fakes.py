from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from yrush.v1 import yrush_pb2 as pb

from yrush_trainer.config import OBSERVATION_FEATURES, VOXEL_FEATURES
from yrush_trainer.messages import RawObservation, RoundResult
from yrush_trainer.wire import StepExchange


def raw_observation(
    *,
    round_sequence: int = 1,
    policy_version: int = 0,
    sequence: int = 0,
    target_difference: float = 12.0,
    phase: int = pb.ROUND_PHASE_ACTIVE,
    client_tick: int = 100,
    active_players: int = 2,
    total_players: int = 2,
) -> RawObservation:
    return RawObservation(
        session_id="session",
        round_sequence=round_sequence,
        policy_version=policy_version,
        client_tick=client_tick,
        observation_sequence=sequence,
        action_sequence=sequence,
        phase=phase,
        block_properties=bytes([0, 1, 0, 1] * (VOXEL_FEATURES // 4)),
        signed_target_height_difference=target_difference,
        forward_velocity=0.1,
        strafe_velocity=-0.1,
        vertical_velocity=0.0,
        fractional_x=0.25,
        fractional_y=0.5,
        fractional_z=0.75,
        grounded=True,
        remaining_time_fraction=0.8,
        yaw_residual_degrees=10.0,
        pitch_degrees=-20.0,
        health_fraction=1.0,
        air_fraction=1.0,
        active_players=active_players,
        total_players=total_players,
    )


def round_result(
    *,
    round_sequence: int = 1,
    policy_version: int = 0,
    outcome: int = pb.PLAYER_OUTCOME_WON,
    participant_count: int = 2,
    winner_uuid: str = "player-0",
    sequence: int = 1,
) -> RoundResult:
    return RoundResult(
        session_id="session",
        round_sequence=round_sequence,
        policy_version=policy_version,
        client_tick=104,
        observation_sequence=sequence,
        outcome=outcome,
        winner_uuid=winner_uuid,
        participant_count=participant_count,
        completion_time_seconds=12.5,
        best_remaining_target_distance=0.1,
    )


class FakeConnection:
    def __init__(self, outcomes: list[int] | None = None) -> None:
        self.player_uuid = "player-0"
        self.player_name = "player-zero"
        self.outcomes = list(outcomes or [pb.PLAYER_OUTCOME_WON])
        self.round_sequence = 0
        self.policy_version = 0
        self.sequence = 0
        self.closed = False

    def arm(self, *, request_id: int, round_sequence: int, policy_version: int) -> RawObservation:
        assert request_id > 0
        self.round_sequence = round_sequence
        self.policy_version = policy_version
        self.sequence = 0
        return raw_observation(
            round_sequence=round_sequence,
            policy_version=policy_version,
            sequence=0,
        )

    def step(
        self, previous: RawObservation, action_sequence: int, action: list[int]
    ) -> StepExchange:
        assert len(action) == 6
        self.sequence = action_sequence
        outcome = self.outcomes[0]
        terminal = action_sequence >= 2
        observation = raw_observation(
            round_sequence=self.round_sequence,
            policy_version=self.policy_version,
            sequence=action_sequence,
            target_difference=previous.signed_target_height_difference - 0.5,
            phase=pb.ROUND_PHASE_COMPLETE if terminal else pb.ROUND_PHASE_ACTIVE,
            client_tick=previous.client_tick + 4,
        )
        result = (
            round_result(
                round_sequence=self.round_sequence,
                policy_version=self.policy_version,
                outcome=outcome,
                sequence=action_sequence,
                winner_uuid="player-0" if outcome == pb.PLAYER_OUTCOME_WON else "",
            )
            if terminal
            else None
        )
        return StepExchange(observation, result, True)

    def shutdown(self, request_id: int, reason: str) -> None:
        assert request_id > 0 and reason
        self.closed = True

    def close(self) -> None:
        self.closed = True


@dataclass
class StaticPolicy:
    version: int = 0

    def sample(self, observations: np.ndarray, *, deterministic: bool) -> Any:
        del deterministic
        from yrush_trainer.policy import PolicyBatch

        count = observations.shape[0]
        return PolicyBatch(
            actions=np.tile(np.asarray([2, 1, 0, 0, 2, 2]), (count, 1)),
            log_probabilities=np.zeros(count, dtype=np.float32),
            values=np.zeros(count, dtype=np.float32),
        )

    def values(self, observations: np.ndarray) -> np.ndarray:
        return np.zeros(observations.shape[0], dtype=np.float32)


def normalized_observation(value: float = 0.0) -> np.ndarray:
    return np.full(OBSERVATION_FEATURES, value, dtype=np.float32)
