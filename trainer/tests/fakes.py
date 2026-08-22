"""Deterministic in-memory benchmark connection for Gymnasium tests."""

from __future__ import annotations

from jump.v1 import jump_pb2 as pb

from jump_trainer.errors import InfrastructureError
from jump_trainer.messages import RawObservation


def observation(
    *,
    episode_id: int = 10,
    sequence: int = 0,
    elapsed_ticks: int = 0,
    distance: float = 6.0,
    phase: int = pb.EPISODE_PHASE_READY,
    terminal_reason: int = pb.TERMINAL_REASON_UNSPECIFIED,
    on_ground: bool = True,
) -> RawObservation:
    return RawObservation(
        session_id="test-session",
        episode_id=episode_id,
        client_tick=100 + elapsed_ticks,
        server_tick=200 + elapsed_ticks,
        observation_sequence=sequence,
        action_sequence=sequence,
        phase=phase,
        terminal_reason=terminal_reason,
        signed_wall_distance=distance,
        relative_feet_height=0.0,
        vertical_velocity=0.0,
        lane_velocity=0.2,
        on_ground=on_ground,
        elapsed_ticks=elapsed_ticks,
    )


class SimulatedConnection:
    """Small deterministic episode: a jump succeeds; three no-ops miss."""

    def __init__(self) -> None:
        self.session_id = "test-session"
        self.current_episode_id = 0
        self.elapsed_ticks = 0
        self.distance = 6.0
        self.jumped = False
        self.closed = False
        self.fail_next_step = False
        self.reset_seeds: list[int] = []
        self.actions: list[int] = []

    def reset(self, request_id: int, episode_id: int, seed: int, retries: int) -> RawObservation:
        assert request_id > 0
        assert retries > 0
        self.current_episode_id = episode_id
        self.elapsed_ticks = 0
        self.distance = 6.0
        self.jumped = False
        self.reset_seeds.append(seed)
        return observation(episode_id=episode_id)

    def step(self, previous: RawObservation, action_sequence: int, action: int) -> RawObservation:
        if self.fail_next_step:
            self.fail_next_step = False
            raise InfrastructureError("scripted transport failure")
        assert previous.episode_id == self.current_episode_id
        assert action_sequence == previous.action_sequence + 1
        self.actions.append(action)
        self.elapsed_ticks += 1
        self.distance -= 0.2
        self.jumped = self.jumped or action == pb.ACTION_JUMP
        terminal = self.jumped or self.elapsed_ticks >= 3
        reason = (
            pb.TERMINAL_REASON_SUCCESS
            if terminal and self.jumped
            else pb.TERMINAL_REASON_MISSED_JUMP
            if terminal
            else pb.TERMINAL_REASON_UNSPECIFIED
        )
        return observation(
            episode_id=self.current_episode_id,
            sequence=action_sequence,
            elapsed_ticks=self.elapsed_ticks,
            distance=self.distance,
            phase=pb.EPISODE_PHASE_TERMINAL if terminal else pb.EPISODE_PHASE_ACTIVE,
            terminal_reason=reason,
            on_ground=terminal,
        )

    def shutdown(self, request_id: int, reason: str) -> None:
        assert request_id > 0
        assert reason
        self.closed = True

    def close(self) -> None:
        self.closed = True
