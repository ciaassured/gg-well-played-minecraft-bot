"""Gymnasium semantics backed by one persistent Fabric YRush bridge."""

from __future__ import annotations

import time
from typing import Any, cast

import gymnasium as gym
import numpy as np
from gymnasium.envs.registration import EnvSpec
from numpy.typing import NDArray
from yrush.v1 import yrush_pb2 as pb

from yrush_trainer.config import ACTION_CARDINALITIES, DEFAULT_HOST, DEFAULT_PORT
from yrush_trainer.errors import InfrastructureError
from yrush_trainer.messages import RawObservation, RoundResult
from yrush_trainer.normalization import (
    OBSERVATION_SPACE,
    normalize_observation_with_stats,
)
from yrush_trainer.wire import ConnectionFactory, StepExchange, YRushConnection


def transition_reward(
    previous: RawObservation,
    current: RawObservation,
    result: RoundResult | None,
) -> float:
    """Reward target progress and apply the configured per-decision/terminal values."""

    progress = float(np.clip(previous.target_distance - current.target_distance, -1.0, 1.0))
    reward = 0.1 * progress - 0.001
    if result is not None:
        terminal = {
            pb.PLAYER_OUTCOME_WON: 10.0,
            pb.PLAYER_OUTCOME_LOST: -1.0,
            pb.PLAYER_OUTCOME_ELIMINATED: -10.0,
            pb.PLAYER_OUTCOME_DRAW: -2.0,
            pb.PLAYER_OUTCOME_STOPPED: 0.0,
        }
        reward += terminal[result.outcome]
    return float(reward)


class YRushEnv(gym.Env[NDArray[np.float32], NDArray[np.int64]]):
    """One client whose reset waits for its next eligible shared round."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        message_timeout: float = 10.0,
        round_timeout: float = 600.0,
        connection_factory: ConnectionFactory | None = None,
        identifier_base: int | None = None,
        owns_connection: bool = True,
    ) -> None:
        super().__init__()
        self.metadata = {"render_modes": []}
        self.spec = EnvSpec(
            id="MinecraftYRushRemote-v1",
            entry_point="yrush_trainer.env:YRushEnv",
            nondeterministic=True,
        )
        self.action_space = cast(
            gym.Space[NDArray[np.int64]],
            gym.spaces.MultiDiscrete(np.asarray(ACTION_CARDINALITIES, dtype=np.int64)),
        )
        self.observation_space = OBSERVATION_SPACE
        self.host = host
        self.port = port
        self.message_timeout = message_timeout
        self.round_timeout = round_timeout
        self._connection_factory = connection_factory or (
            lambda: YRushConnection.connect(
                host,
                port,
                message_timeout=message_timeout,
                round_timeout=round_timeout,
            )
        )
        self._owns_connection = owns_connection
        self._connection: YRushConnection | Any | None = None
        base = identifier_base if identifier_base is not None else time.time_ns()
        if base <= 0 or base >= 2**64 - 1_000_000:
            raise ValueError("identifier base must leave room in uint64")
        self._next_identifier = base
        self._next_round_sequence = 1
        self.policy_version = 0
        self._observation: RawObservation | None = None
        self._active = False
        self._episode_return = 0.0
        self._episode_actions = 0
        self._observation_clips = 0
        self._episode_target_progress = 0.0
        self._target_direction = "UP"

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        super().reset(seed=seed)
        selected = dict(options or {})
        round_sequence = int(selected.get("round_sequence", self._next_round_sequence))
        policy_version = int(selected.get("policy_version", self.policy_version))
        if round_sequence <= 0 or policy_version < 0:
            raise ValueError("round sequence must be positive and policy version nonnegative")
        self._next_round_sequence = max(self._next_round_sequence, round_sequence + 1)
        self.policy_version = policy_version
        try:
            observation = self._ensure_connection().arm(
                request_id=self._allocate_identifier(),
                round_sequence=round_sequence,
                policy_version=policy_version,
            )
        except InfrastructureError:
            self._fail_episode()
            raise
        self._observation = observation
        self._active = True
        self._episode_return = 0.0
        self._episode_actions = 0
        self._observation_clips = 0
        self._episode_target_progress = 0.0
        self._target_direction = (
            "UP" if observation.signed_target_height_difference >= 0.0 else "DOWN"
        )
        normalized = normalize_observation_with_stats(observation)
        self._observation_clips += normalized.clipped_features
        return normalized.values, self._info(observation, None, None, True)

    def step(
        self, action: NDArray[np.int64]
    ) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        choices = np.asarray(action, dtype=np.int64)
        if not self.action_space.contains(choices):
            raise ValueError(f"action must fit MultiDiscrete{ACTION_CARDINALITIES}")
        if not self._active or self._observation is None:
            raise RuntimeError("reset() must produce an active episode before step()")
        previous = self._observation
        try:
            exchange: StepExchange = self._ensure_connection().step(
                previous=previous,
                action_sequence=previous.action_sequence + 1,
                action=choices.tolist(),
            )
        except InfrastructureError:
            self._fail_episode()
            raise
        current = exchange.observation
        result = exchange.result
        if current.phase == pb.ROUND_PHASE_COMPLETE and result is None:
            self._fail_episode()
            raise InfrastructureError("terminal observation has no episode result")
        if current.phase == pb.ROUND_PHASE_ACTIVE and result is not None:
            self._fail_episode()
            raise InfrastructureError("active observation unexpectedly has an episode result")

        reward = transition_reward(previous, current, result)
        self._episode_target_progress += previous.target_distance - current.target_distance
        self._episode_actions += int(exchange.action_applied)
        self._episode_return += reward
        self._observation = current
        normalized = normalize_observation_with_stats(current)
        self._observation_clips += normalized.clipped_features

        stopped = result is not None and result.outcome == pb.PLAYER_OUTCOME_STOPPED
        terminated = result is not None and not stopped
        truncated = bool(stopped)
        if terminated or truncated:
            self._active = False
        info = self._info(current, previous, result, exchange.action_applied)
        return normalized.values, reward, terminated, truncated, info

    def connect(self) -> None:
        self._ensure_connection()

    @property
    def player_uuid(self) -> str:
        return "" if self._connection is None else str(self._connection.player_uuid)

    @property
    def player_name(self) -> str:
        return "" if self._connection is None else str(self._connection.player_name)

    def close(self) -> None:
        if self._connection is not None:
            if self._owns_connection:
                self._connection.shutdown(self._allocate_identifier(), "trainer environment closed")
            self._connection = None
        self._active = False

    def _ensure_connection(self) -> Any:
        if self._connection is None:
            self._connection = self._connection_factory()
        return self._connection

    def _allocate_identifier(self) -> int:
        value = self._next_identifier
        self._next_identifier += 1
        return value

    def _fail_episode(self) -> None:
        self._active = False
        self._observation = None
        if self._connection is not None and self._owns_connection:
            self._connection.close()
            self._connection = None

    def _info(
        self,
        observation: RawObservation,
        previous: RawObservation | None,
        result: RoundResult | None,
        action_applied: bool,
    ) -> dict[str, Any]:
        return {
            "round_sequence": observation.round_sequence,
            "policy_version": observation.policy_version,
            "player_uuid": self.player_uuid,
            "player_name": self.player_name,
            "episode_return": self._episode_return,
            "episode_actions": self._episode_actions,
            "outcome": None if result is None else result.outcome_name,
            "winner_uuid": None if result is None or not result.winner_uuid else result.winner_uuid,
            "participant_count": None if result is None else result.participant_count,
            "completion_time_seconds": (None if result is None else result.completion_time_seconds),
            "best_remaining_target_distance": (
                None if result is None else result.best_remaining_target_distance
            ),
            "target_distance": observation.target_distance,
            "target_progress": self._episode_target_progress,
            "decision_target_progress": (
                None if previous is None else previous.target_distance - observation.target_distance
            ),
            "target_direction": self._target_direction,
            "active_players": observation.active_players,
            "total_players": observation.total_players,
            "client_tick_delta": (
                None if previous is None else observation.client_tick - previous.client_tick
            ),
            "observation_clipped_features": self._observation_clips,
            "action_applied": action_applied,
            "valid_transition": action_applied,
            "raw_observation": observation.as_dict(),
            "round_result": None if result is None else result.as_dict(),
        }
