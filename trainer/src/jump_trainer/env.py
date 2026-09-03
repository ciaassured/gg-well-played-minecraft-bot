"""Gymnasium environment backed by the Fabric benchmark bridge."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium.envs.registration import EnvSpec
from jump.v1 import jump_pb2 as pb
from numpy.typing import NDArray

from jump_trainer.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    TRAIN_SEED_MAX,
    TRAIN_SEED_MIN,
)
from jump_trainer.errors import InfrastructureError
from jump_trainer.messages import RawObservation
from jump_trainer.normalization import OBSERVATION_SPACE, normalize_observation
from jump_trainer.wire import BenchmarkConnection, ConnectionFactory

NOOP = 0
JUMP = 1


def transition_reward(
    previous: RawObservation,
    current: RawObservation,
    requested_action: int,
) -> float:
    """Apply the benchmark's sole reward equation to one valid transition."""

    if requested_action not in {NOOP, JUMP}:
        raise ValueError("requested action must be NOOP or JUMP")
    forward_progress = previous.signed_wall_distance - current.signed_wall_distance
    reward = forward_progress - 0.01
    if requested_action == JUMP:
        reward -= 0.05
    if current.terminal_reason == pb.TERMINAL_REASON_SUCCESS:
        reward += 10.0
    elif current.terminal_reason in {
        pb.TERMINAL_REASON_MISSED_JUMP,
        pb.TERMINAL_REASON_TIME_LIMIT,
    }:
        reward -= 10.0
    return float(reward)


class MinecraftJumpEnv(gym.Env[NDArray[np.float32], int]):
    """One-action-per-tick remote one-block jump environment."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 5.0,
        reset_retries: int = 3,
        connection_factory: ConnectionFactory | None = None,
        identifier_base: int | None = None,
        owns_connection: bool = True,
    ) -> None:
        super().__init__()
        self.metadata = {"render_modes": []}
        self.spec = EnvSpec(
            id="MinecraftJumpRemote-v0",
            entry_point="jump_trainer.env:MinecraftJumpEnv",
            nondeterministic=True,
            max_episode_steps=200,
        )
        self.action_space = gym.spaces.Discrete(2)
        self.observation_space = OBSERVATION_SPACE
        self.host = host
        self.port = port
        self.timeout = timeout
        self.reset_retries = reset_retries
        self._connection_factory = connection_factory or (
            lambda: BenchmarkConnection.connect(host, port, timeout)
        )
        self._owns_connection = owns_connection
        self._connection: BenchmarkConnection | Any | None = None
        base = identifier_base if identifier_base is not None else time.time_ns()
        if base <= 0 or base >= 2**64 - 1_000_000:
            raise ValueError("identifier_base must leave room in uint64")
        self._next_identifier = base
        self._observation: RawObservation | None = None
        self._active = False
        self._jump_requests = 0
        self._episode_return = 0.0

    @contextmanager
    def _preserve_seed_stream(self) -> Iterator[None]:
        """Restore Gymnasium's current PRNG and seed after seeded resets."""

        np_random = self._np_random
        np_random_seed = self._np_random_seed
        try:
            yield
        finally:
            self._np_random = np_random
            self._np_random_seed = np_random_seed

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        del options
        super().reset(seed=seed)
        episode_seed = (
            int(seed)
            if seed is not None
            else int(self.np_random.integers(TRAIN_SEED_MIN, TRAIN_SEED_MAX + 1))
        )
        if episode_seed < 0 or episode_seed >= 2**64:
            raise ValueError("episode seed must fit uint64")
        connection = self._ensure_connection()
        request_id = self._allocate_identifier()
        episode_id = self._allocate_identifier()
        try:
            observation = connection.reset(
                request_id=request_id,
                episode_id=episode_id,
                seed=episode_seed,
                retries=self.reset_retries,
            )
        except InfrastructureError:
            self._fail_episode()
            raise
        self._observation = observation
        self._active = True
        self._jump_requests = 0
        self._episode_return = 0.0
        info = self._info(observation, episode_seed, previous=None)
        return normalize_observation(observation), info

    def step(self, action: int) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        if not self.action_space.contains(action):
            raise ValueError("action must be 0 (NOOP) or 1 (JUMP)")
        if not self._active or self._observation is None:
            raise RuntimeError("reset() must produce an active episode before step()")
        previous = self._observation
        wire_action = pb.ACTION_JUMP if int(action) == JUMP else pb.ACTION_NOOP
        try:
            current = self._ensure_connection().step(
                previous=previous,
                action_sequence=previous.action_sequence + 1,
                action=wire_action,
            )
        except InfrastructureError:
            self._fail_episode()
            raise
        if current.phase == pb.EPISODE_PHASE_ABORTED or current.terminal_reason == (
            pb.TERMINAL_REASON_INFRASTRUCTURE_ERROR
        ):
            self._fail_episode()
            raise InfrastructureError("benchmark aborted the episode as an infrastructure error")

        requested_action = int(action)
        reward = transition_reward(previous, current, requested_action)
        self._jump_requests += int(requested_action == JUMP)
        self._episode_return += reward
        self._observation = current

        terminated = current.terminal_reason in {
            pb.TERMINAL_REASON_SUCCESS,
            pb.TERMINAL_REASON_MISSED_JUMP,
        }
        truncated = current.terminal_reason == pb.TERMINAL_REASON_TIME_LIMIT
        if current.phase == pb.EPISODE_PHASE_TERMINAL and not (terminated or truncated):
            self._fail_episode()
            raise InfrastructureError("terminal observation has no valid benchmark reason")
        if terminated or truncated:
            self._active = False
        info = self._info(current, None, previous=previous)
        return normalize_observation(current), reward, terminated, truncated, info

    def connect(self) -> None:
        """Establish and validate the Fabric/Paper handshake without starting an episode."""

        self._ensure_connection()

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
        seed: int | None,
        previous: RawObservation | None,
    ) -> dict[str, Any]:
        reason_name = pb.TerminalReason.Name(observation.terminal_reason)
        return {
            "episode_id": observation.episode_id,
            "episode_seed": seed,
            "elapsed_ticks": observation.elapsed_ticks,
            "jump_requests": self._jump_requests,
            "episode_return": self._episode_return,
            "success": observation.terminal_reason == pb.TERMINAL_REASON_SUCCESS,
            "terminal_reason": reason_name,
            "client_tick_delta": (
                observation.client_tick - previous.client_tick if previous is not None else None
            ),
            "server_tick_delta": (
                observation.server_tick - previous.server_tick if previous is not None else None
            ),
            "raw_observation": observation.as_dict(),
        }
