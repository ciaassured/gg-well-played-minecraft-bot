from __future__ import annotations

import numpy as np
import pytest

from jump_trainer.endpoints import Endpoint
from jump_trainer.env import JUMP, NOOP, MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.pool import ClientPool, TrainingSeedStreams
from tests.fakes import SimulatedConnection


class AlwaysJump:
    def actions(
        self,
        actor_indices: tuple[int, ...],
        observations: np.ndarray,
        *,
        deterministic: bool,
    ) -> np.ndarray:
        del observations, deterministic
        return np.full(len(actor_indices), JUMP, dtype=np.int64)

    def reset(self, actor_index: int) -> None:
        del actor_index


class AlwaysNoop(AlwaysJump):
    def actions(
        self,
        actor_indices: tuple[int, ...],
        observations: np.ndarray,
        *,
        deterministic: bool,
    ) -> np.ndarray:
        del observations, deterministic
        return np.full(len(actor_indices), NOOP, dtype=np.int64)


def _pool(width: int) -> tuple[ClientPool, list[SimulatedConnection]]:
    endpoints = tuple(Endpoint(index, f"client-{index}", 64123, index) for index in range(width))
    connections = [SimulatedConnection() for _ in endpoints]

    def factory(endpoint: Endpoint) -> MinecraftJumpEnv:
        return MinecraftJumpEnv(
            connection_factory=lambda: connections[endpoint.index],
            identifier_base=10_000 + endpoint.index * 1_000,
        )

    return (
        ClientPool(
            endpoints,
            startup_timeout=1,
            message_timeout=1,
            reset_retries=1,
            environment_factory=factory,
        ),
        connections,
    )


def test_parallel_evaluation_assigns_each_seed_once_and_sorts_report() -> None:
    pool, connections = _pool(3)
    with pool:
        report = pool.evaluate(AlwaysJump(), (9, 3, 7, 1, 5), policy_id="fixed", suite="test")
    assert [episode.seed for episode in report.episodes] == [1, 3, 5, 7, 9]
    assert sum(len(connection.reset_seeds) for connection in connections) == 5
    assigned = sorted(seed for connection in connections for seed in connection.reset_seeds)
    assert assigned == [1, 3, 5, 7, 9]
    assert report.success_count == 5


def test_collection_overshoot_is_bounded_by_pool_width() -> None:
    pool, _connections = _pool(3)
    batches: list[tuple[int, int]] = []
    with pool:
        result = pool.collect(
            requested_total=5,
            actual_total=0,
            first_cycle=0,
            seeds=TrainingSeedStreams(42, pool.endpoints),
            policy=AlwaysJump(),
            transition_sink=lambda transitions, cycle: batches.append((cycle, len(transitions))),
        )
    assert result.actual_transitions == 6
    assert result.actual_transitions - result.requested_transitions < pool.width
    assert batches == [(1, 3), (2, 3)]


def test_abort_barrier_disconnects_all_actors_before_subset_evaluation() -> None:
    pool, connections = _pool(3)
    with pool:
        pool.collect(
            requested_total=3,
            actual_total=0,
            first_cycle=0,
            seeds=TrainingSeedStreams(42, pool.endpoints),
            policy=AlwaysNoop(),
            transition_sink=lambda _transitions, _cycle: None,
        )
        pool.abort_active_episodes()
        assert all(connection.closed for connection in connections)
        report = pool.evaluate(
            AlwaysJump(),
            (100_000,),
            policy_id="after-barrier",
            suite="test",
        )

    assert report.success_count == 1
    assert sum(len(connection.reset_seeds) for connection in connections) == 4
    assert pool.stats()["episode_abort_barriers"] == 1


def test_training_seed_streams_are_per_client_and_repeatable() -> None:
    endpoints = tuple(Endpoint(index, f"client-{index}", 64123, index) for index in range(3))
    first = TrainingSeedStreams(123, endpoints)
    second = TrainingSeedStreams(123, endpoints)
    first_values = [[first.next(index) for _ in range(5)] for index in range(3)]
    second_values = [[second.next(index) for _ in range(5)] for index in range(3)]
    assert first_values == second_values
    assert len({tuple(values) for values in first_values}) == 3


def test_any_client_loss_fails_the_fixed_pool() -> None:
    pool, connections = _pool(2)
    connections[1].fail_next_step = True
    with pool, pytest.raises(InfrastructureError, match="configured client client-1:64123"):
        pool.collect(
            requested_total=2,
            actual_total=0,
            first_cycle=0,
            seeds=TrainingSeedStreams(42, pool.endpoints),
            policy=AlwaysJump(),
            transition_sink=lambda _transitions, _cycle: None,
        )
