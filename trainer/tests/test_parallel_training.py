from __future__ import annotations

import json
from pathlib import Path

from stable_baselines3 import DQN

import jump_trainer.parallel_training as parallel_training
from jump_trainer.config import TrainConfig
from jump_trainer.endpoints import Endpoint
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.pool import ClientPool
from tests.fakes import SimulatedConnection


def test_parallel_pipeline_uses_spawned_learner_and_promotes_loadable_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    endpoints = (
        Endpoint(0, "client-0", 64123, 0),
        Endpoint(1, "client-1", 64123, 1),
    )
    connections = [SimulatedConnection(), SimulatedConnection()]

    def pool_factory(
        selected_endpoints: tuple[Endpoint, ...],
        *,
        startup_timeout: float,
        message_timeout: float,
        reset_retries: int,
    ) -> ClientPool:
        del message_timeout, reset_retries

        def environment_factory(endpoint: Endpoint) -> MinecraftJumpEnv:
            return MinecraftJumpEnv(
                connection_factory=lambda: connections[endpoint.index],
                identifier_base=50_000 + endpoint.index * 1_000,
            )

        return ClientPool(
            selected_endpoints,
            startup_timeout=startup_timeout,
            message_timeout=1,
            reset_retries=1,
            environment_factory=environment_factory,
        )

    monkeypatch.setattr(parallel_training, "ClientPool", pool_factory)
    config = TrainConfig(
        total_timesteps=2,
        validation_interval=2,
        validation_episodes=1,
        learning_starts=0,
        batch_size=2,
        buffer_size=8,
        train_frequency=1,
        target_update_interval=2,
        policy_width=8,
        endpoints=tuple(endpoint.address for endpoint in endpoints),
        pool_startup_timeout_seconds=1,
    )

    run = parallel_training.run_parallel(
        config,
        tmp_path / "runs",
        endpoints,
        run_id="integration",
        final_evaluation_episodes=2,
    )

    summary = json.loads((run.metrics / "training-summary.json").read_text())
    assert summary["status"] == "complete"
    assert summary["requested_transitions"] == 2
    assert summary["actual_transitions"] == 2
    assert summary["gradient_updates"] == 2
    assert summary["target_updates"] == 1
    assert summary["client_ordinals"] == [0, 1]
    assert len(summary["validation_boundaries"]) == 2
    assert summary["pool"]["episode_abort_barriers"] == 1
    assert run.latest_checkpoint.is_file()
    assert run.best_checkpoint.is_file()
    assert DQN.load(run.best_checkpoint, device="cpu").num_timesteps in {0, 2}
    performance = json.loads((run.metrics / "performance-best-2-episodes.json").read_text())
    assert [episode["seed"] for episode in performance["evaluation"]["episodes"]] == [
        200_000,
        200_001,
    ]
    assert not (run.root / "replays").exists()
    assert all(connection.closed for connection in connections)
