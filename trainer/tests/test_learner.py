from __future__ import annotations

from pathlib import Path

import numpy as np
from stable_baselines3 import DQN

from jump_trainer.config import TrainConfig
from jump_trainer.learner import (
    AggregateSchedule,
    InferencePolicy,
    LearnerProcess,
    LearnerStatus,
    _build_model,
    _weights,
)
from jump_trainer.pool import Transition


def test_aggregate_update_schedule_is_independent_of_batch_width() -> None:
    one_at_a_time = AggregateSchedule(500, 4, 1_000)
    for _ in range(2_003):
        one_at_a_time.advance(1)

    batched = AggregateSchedule(500, 4, 1_000)
    for count in (37, 463, 117, 886, 500):
        batched.advance(count)

    assert one_at_a_time.transitions == batched.transitions == 2_003
    assert one_at_a_time.gradient_updates == batched.gradient_updates == 375
    assert one_at_a_time.target_updates == batched.target_updates == 2


def test_no_gradient_occurs_at_learning_start_boundary() -> None:
    schedule = AggregateSchedule(500, 4, 1_000)
    assert schedule.advance(500).gradient_steps == 0
    assert schedule.advance(3).gradient_steps == 0
    assert schedule.advance(1).gradient_steps == 1


def test_numpy_inference_copy_matches_the_sb3_q_network() -> None:
    config = TrainConfig(total_timesteps=10, validation_episodes=1, policy_width=8)
    model = _build_model(config)
    observations = np.asarray(
        [
            [-0.75, 0.0, 0.1, 0.5, 1.0, -1.0],
            [0.25, 0.2, -0.1, 0.25, 0.0, 0.0],
            [0.9, 0.8, 0.0, 0.0, 1.0, 1.0],
        ],
        dtype=np.float32,
    )
    policy = InferencePolicy(config.random_seed, (0, 1, 2))
    policy.load(LearnerStatus("policy", 1, 4, 0, 0, 0.0, _weights(model)))

    expected, _state = model.predict(observations, deterministic=True)
    actual = policy.actions((0, 1, 2), observations, deterministic=True)
    np.testing.assert_array_equal(actual, np.asarray(expected).reshape(-1))


def _transition(cycle: int, reward: float) -> Transition:
    observation = np.zeros(6, dtype=np.float32)
    next_observation = np.full(6, 0.1, dtype=np.float32)
    return Transition(
        actor_index=0,
        endpoint="client-0:64123",
        cycle=cycle,
        observation=observation,
        action=cycle % 2,
        reward=reward,
        next_observation=next_observation,
        done=False,
        info={"client_tick_delta": 1, "server_tick_delta": 1},
        action_latency_ms=1.0,
    )


def test_spawned_learner_owns_updates_and_writes_loadable_checkpoints(tmp_path: Path) -> None:
    config = TrainConfig(
        total_timesteps=2,
        validation_episodes=1,
        learning_starts=0,
        batch_size=2,
        buffer_size=8,
        train_frequency=1,
        target_update_interval=2,
        policy_width=8,
    )
    untrained = tmp_path / "untrained.zip"
    latest = tmp_path / "latest.zip"
    candidate = tmp_path / "candidate.zip"
    learner = LearnerProcess(config, untrained, latest, actor_count=1)
    try:
        learner.start()
        learner.submit((_transition(1, 0.0), _transition(1, 1.0)), cycle=1)
        status = learner.barrier(candidate)
    finally:
        learner.close()

    assert untrained.is_file()
    assert latest.is_file()
    assert candidate.is_file()
    assert status.transitions == 2
    assert status.gradient_updates == 2
    assert status.target_updates == 1
    assert DQN.load(candidate, device="cpu").num_timesteps == 2
