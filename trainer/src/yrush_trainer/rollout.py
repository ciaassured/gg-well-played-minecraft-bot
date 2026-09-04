"""Version-homogeneous per-client transition storage and GAE."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class Transition:
    actor_index: int
    round_sequence: int
    policy_version: int
    observation: NDArray[np.float32]
    action: NDArray[np.int64]
    reward: float
    next_observation: NDArray[np.float32]
    terminated: bool
    truncated: bool
    episode_start: bool
    log_probability: float
    value_estimate: float
    next_value_estimate: float

    @property
    def done(self) -> bool:
        return self.terminated or self.truncated


@dataclass(frozen=True)
class PreparedRollout:
    policy_version: int
    actor_indices: tuple[int, ...]
    observations: NDArray[np.float32]
    actions: NDArray[np.int64]
    rewards: NDArray[np.float32]
    episode_starts: NDArray[np.float32]
    values: NDArray[np.float32]
    log_probabilities: NDArray[np.float32]
    advantages: NDArray[np.float32]
    returns: NDArray[np.float32]

    @property
    def length_per_client(self) -> int:
        return int(self.observations.shape[0])

    @property
    def client_count(self) -> int:
        return int(self.observations.shape[1])


class RolloutCollector:
    """Accept exactly one configured transition count from every client."""

    def __init__(
        self,
        actor_indices: tuple[int, ...],
        *,
        policy_version: int,
        length_per_client: int = 256,
    ) -> None:
        if not actor_indices or len(set(actor_indices)) != len(actor_indices):
            raise ValueError("rollout actors must be nonempty and unique")
        if policy_version < 0 or length_per_client <= 0:
            raise ValueError("invalid rollout policy version or length")
        self.actor_indices = tuple(sorted(actor_indices))
        self.policy_version = policy_version
        self.length_per_client = length_per_client
        self._transitions: dict[int, list[Transition]] = {actor: [] for actor in self.actor_indices}
        self.discarded_after_close = 0

    def add(self, transition: Transition) -> bool:
        if transition.actor_index not in self._transitions:
            raise ValueError("transition came from an unknown actor")
        if transition.policy_version != self.policy_version:
            raise ValueError("rollout cannot mix policy versions")
        values = (
            transition.reward,
            transition.log_probability,
            transition.value_estimate,
            transition.next_value_estimate,
        )
        if not all(np.isfinite(value) for value in values):
            raise ValueError("transition contains non-finite learning data")
        target = self._transitions[transition.actor_index]
        if len(target) >= self.length_per_client:
            self.discarded_after_close += 1
            return False
        target.append(transition)
        return True

    @property
    def complete(self) -> bool:
        return all(
            len(transitions) == self.length_per_client for transitions in self._transitions.values()
        )

    @property
    def counts(self) -> dict[int, int]:
        return {actor: len(values) for actor, values in self._transitions.items()}

    def prepare(self, *, gamma: float, gae_lambda: float) -> PreparedRollout:
        if not self.complete:
            raise ValueError("cannot prepare an incomplete rollout")
        if not 0.0 < gamma <= 1.0 or not 0.0 <= gae_lambda <= 1.0:
            raise ValueError("invalid GAE settings")

        columns = [self._transitions[actor] for actor in self.actor_indices]
        observations = np.stack(
            [
                [columns[actor][step].observation for actor in range(len(columns))]
                for step in range(self.length_per_client)
            ]
        ).astype(np.float32)
        actions = np.stack(
            [
                [columns[actor][step].action for actor in range(len(columns))]
                for step in range(self.length_per_client)
            ]
        ).astype(np.int64)
        rewards = np.asarray(
            [
                [columns[actor][step].reward for actor in range(len(columns))]
                for step in range(self.length_per_client)
            ],
            dtype=np.float32,
        )
        values = np.asarray(
            [
                [columns[actor][step].value_estimate for actor in range(len(columns))]
                for step in range(self.length_per_client)
            ],
            dtype=np.float32,
        )
        log_probabilities = np.asarray(
            [
                [columns[actor][step].log_probability for actor in range(len(columns))]
                for step in range(self.length_per_client)
            ],
            dtype=np.float32,
        )
        episode_starts = np.asarray(
            [
                [float(columns[actor][step].episode_start) for actor in range(len(columns))]
                for step in range(self.length_per_client)
            ],
            dtype=np.float32,
        )
        advantages = np.zeros_like(rewards)
        for actor, transitions in enumerate(columns):
            next_advantage = 0.0
            for step in reversed(range(self.length_per_client)):
                transition = transitions[step]
                bootstrap = 0.0 if transition.terminated else transition.next_value_estimate
                delta = transition.reward + gamma * bootstrap - transition.value_estimate
                continuation = 0.0 if transition.done else 1.0
                next_advantage = delta + gamma * gae_lambda * continuation * next_advantage
                advantages[step, actor] = next_advantage
        returns = advantages + values
        if not all(
            np.all(np.isfinite(array))
            for array in (observations, rewards, values, log_probabilities, advantages, returns)
        ):
            raise ValueError("prepared rollout contains non-finite data")
        return PreparedRollout(
            policy_version=self.policy_version,
            actor_indices=self.actor_indices,
            observations=observations,
            actions=actions,
            rewards=rewards,
            episode_starts=episode_starts,
            values=values,
            log_probabilities=log_probabilities,
            advantages=advantages,
            returns=returns,
        )
