from __future__ import annotations

from collections import defaultdict
from typing import Any, ClassVar

import numpy as np

from yrush_trainer.endpoints import Endpoint
from yrush_trainer.pool import ClientPool

from .fakes import StaticPolicy, normalized_observation


class AsyncFakeEnv:
    calls: ClassVar[dict[int, int]] = defaultdict(int)

    def __init__(self, actor: int) -> None:
        self.actor = actor
        self.player_uuid = f"player-{actor}"
        self.player_name = f"player-{actor}"
        self.round_sequence = 0
        self.policy_version = 0
        self.steps = 0

    def connect(self) -> None:
        return None

    def reset(self, *, options: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
        self.round_sequence = int(options["round_sequence"])
        self.policy_version = int(options["policy_version"])
        self.steps = 0
        return normalized_observation(), self._info(None, False)

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        assert action.shape == (6,)
        self.steps += 1
        self.calls[self.actor] += 1
        terminal = self.steps >= (1 if self.actor == 0 else 3)
        outcome = "ELIMINATED" if self.actor == 0 else "WON"
        return (
            normalized_observation(self.steps / 10),
            -1.0 if self.actor == 0 else 1.0,
            terminal,
            False,
            self._info(outcome if terminal else None, True),
        )

    def _info(self, outcome: str | None, valid: bool) -> dict[str, Any]:
        return {
            "round_sequence": self.round_sequence,
            "policy_version": self.policy_version,
            "player_uuid": self.player_uuid,
            "player_name": self.player_name,
            "outcome": outcome,
            "winner_uuid": "player-1" if outcome == "WON" else None,
            "participant_count": 2,
            "completion_time_seconds": float(self.steps),
            "best_remaining_target_distance": float(3 - self.steps),
            "episode_return": float(self.steps),
            "episode_actions": self.steps,
            "observation_clipped_features": 0,
            "target_direction": "UP",
            "target_progress": float(self.steps),
            "active_players": 1,
            # The first action is held across four callbacks even though the
            # initial and resulting observation timestamps differ by three.
            "client_tick_delta": (3 if self.steps == 1 else 4) if valid else None,
            "valid_transition": valid,
        }

    def close(self) -> None:
        return None


def test_eliminated_actor_does_not_block_survivor_actions() -> None:
    AsyncFakeEnv.calls.clear()
    endpoints = (Endpoint(0, "client-0", 1, 0), Endpoint(1, "client-1", 1, 1))
    transitions = []
    with ClientPool(
        endpoints,
        startup_timeout=2.0,
        message_timeout=1.0,
        round_timeout=2.0,
        environment_factory=lambda endpoint: AsyncFakeEnv(endpoint.index),
    ) as pool:
        result = pool.drive(
            StaticPolicy(), deterministic=False, rounds=1, transition_sink=transitions.append
        )
    assert AsyncFakeEnv.calls == {0: 1, 1: 3}
    assert result.valid_transitions == 4
    assert result.rounds[0].winner_uuid == "player-1"
    assert [transition.actor_index for transition in transitions].count(0) == 1
    assert [transition.actor_index for transition in transitions].count(1) == 3
    assert pool.stats()["min_client_ticks_per_action"] == 4
    assert pool.stats()["mean_client_ticks_per_action"] == 4.0
