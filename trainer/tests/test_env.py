from __future__ import annotations

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env
from stable_baselines3.common.vec_env import DummyVecEnv

from jump_trainer.config import TRAIN_SEED_MAX, TRAIN_SEED_MIN
from jump_trainer.env import JUMP, NOOP, MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from tests.fakes import SimulatedConnection


def make_env(connection: SimulatedConnection, identifier_base: int = 1_000) -> MinecraftJumpEnv:
    return MinecraftJumpEnv(
        connection_factory=lambda: connection,
        identifier_base=identifier_base,
    )


def test_reset_step_termination_and_requested_jump_accounting() -> None:
    connection = SimulatedConnection()
    env = make_env(connection)
    initial, reset_info = env.reset(seed=42)
    assert initial.shape == (6,)
    assert reset_info["elapsed_ticks"] == 0
    assert connection.reset_seeds == [42]

    _next, reward, terminated, truncated, info = env.step(JUMP)
    assert reward > 10.0
    assert terminated is True
    assert truncated is False
    assert info["success"] is True
    assert info["jump_requests"] == 1
    assert connection.actions[-1] != 0
    env.close()
    assert connection.closed


def test_noop_missed_jump_is_a_termination() -> None:
    connection = SimulatedConnection()
    env = make_env(connection)
    env.reset(seed=7)
    final = None
    for _ in range(3):
        final = env.step(NOOP)
    assert final is not None
    _observation, reward, terminated, truncated, info = final
    assert reward < -9.0
    assert terminated is True
    assert truncated is False
    assert info["success"] is False
    assert info["jump_requests"] == 0
    env.close()


def test_infrastructure_failure_raises_without_a_transition() -> None:
    connection = SimulatedConnection()
    env = make_env(connection)
    env.reset(seed=9)
    connection.fail_next_step = True
    with pytest.raises(InfrastructureError, match="scripted"):
        env.step(NOOP)
    assert connection.closed
    with pytest.raises(RuntimeError, match=r"reset\(\)"):
        env.step(NOOP)


def test_implicit_training_seeds_stay_in_partition() -> None:
    connection = SimulatedConnection()
    env = make_env(connection)
    for _ in range(20):
        env.reset()
    assert all(TRAIN_SEED_MIN <= seed <= TRAIN_SEED_MAX for seed in connection.reset_seeds)
    env.close()


def test_gymnasium_checker_and_terminal_observation() -> None:
    checked_connection = SimulatedConnection()
    checked = make_env(checked_connection, identifier_base=10_000)
    check_env(checked, skip_render_check=True)
    checked.close()

    vector_connection = SimulatedConnection()
    vector = DummyVecEnv([lambda: make_env(vector_connection, identifier_base=20_000)])
    vector.reset()
    observations, rewards, dones, infos = vector.step(np.asarray([JUMP]))
    assert observations.shape == (1, 6)
    assert rewards.shape == (1,)
    assert bool(dones[0]) is True
    assert "terminal_observation" in infos[0]
    assert infos[0]["terminal_observation"].shape == (6,)
    vector.close()
