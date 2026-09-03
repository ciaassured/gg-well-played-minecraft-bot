from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from stable_baselines3 import DQN

import jump_trainer.training as training
from jump_trainer.config import TrainConfig
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.evaluation import EpisodeMetrics, EvaluationReport
from jump_trainer.training import FullTrainingBudgetCallback, TrainingProgressCallback
from tests.fakes import SimulatedConnection


def test_deterministic_mock_training_saves_and_loads(tmp_path: Path, capsys) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=50_000)
    model = DQN(
        "MlpPolicy",
        env,
        learning_rate=0.001,
        buffer_size=256,
        learning_starts=0,
        batch_size=16,
        train_freq=1,
        gradient_steps=1,
        target_update_interval=32,
        exploration_fraction=0.2,
        exploration_final_eps=0.0,
        policy_kwargs={"net_arch": [16]},
        seed=1234,
        device="cpu",
        verbose=0,
    )
    model.learn(
        total_timesteps=64,
        progress_bar=False,
        callback=TrainingProgressCallback(total_timesteps=64, report_interval=16),
    )
    checkpoint = tmp_path / "mock-dqn.zip"
    model.save(checkpoint)
    loaded = DQN.load(checkpoint, device="cpu")
    observation, _info = env.reset(seed=100_000)
    original_action, _ = model.predict(observation, deterministic=True)
    loaded_action, _ = loaded.predict(observation, deterministic=True)
    assert int(np.asarray(original_action).item()) == int(np.asarray(loaded_action).item())
    assert checkpoint.is_file()
    output = capsys.readouterr().err
    assert "[train] learn: 16/64 timesteps;" in output
    assert "[train] learn: 32/64 timesteps;" in output
    assert "[train] learn: 48/64 timesteps;" in output
    assert "[train] learn: 64/64 timesteps;" in output
    assert "mean_return=" in output
    assert "client_ticks/action=1.00" in output
    assert "server_ticks/action=1.00" in output
    assert "exploration=" in output
    assert "eta=00:00" in output
    env.close()


def test_chunked_learning_uses_full_budget_for_exploration() -> None:
    env = MinecraftJumpEnv(connection_factory=SimulatedConnection, identifier_base=60_000)
    model = DQN(
        "MlpPolicy",
        env,
        buffer_size=64,
        learning_starts=100,
        batch_size=16,
        train_freq=1,
        gradient_steps=1,
        exploration_fraction=0.5,
        exploration_initial_eps=1.0,
        exploration_final_eps=0.1,
        policy_kwargs={"net_arch": [16]},
        seed=1234,
        device="cpu",
        verbose=0,
    )
    full_budget = 20
    callback = FullTrainingBudgetCallback(full_budget)

    for chunk, expected_step in ((5, 5), (7, 12), (8, 20)):
        model.learn(
            total_timesteps=chunk,
            reset_num_timesteps=False,
            progress_bar=False,
            callback=callback,
        )
        expected_epsilon = model.exploration_schedule(1.0 - expected_step / full_budget)
        assert model.num_timesteps == expected_step
        assert model.exploration_rate == pytest.approx(expected_epsilon)
        assert model._total_timesteps == full_budget

    env.close()


class _ChunkedModel:
    def __init__(self) -> None:
        self.num_timesteps = 0
        self.learn_chunks: list[int] = []
        self.vector_env = object()

    def save(self, path: Path) -> None:
        Path(path).write_bytes(f"checkpoint at {self.num_timesteps}".encode())

    def get_env(self) -> object:
        return self.vector_env

    def set_env(self, env: object, *, force_reset: bool) -> None:
        assert env is self.vector_env
        assert force_reset is True

    def learn(self, *, total_timesteps: int, **kwargs: Any) -> _ChunkedModel:
        assert kwargs["reset_num_timesteps"] is False
        assert kwargs["progress_bar"] is False
        self.learn_chunks.append(total_timesteps)
        self.num_timesteps += total_timesteps
        return self

    def predict(self, _observation: np.ndarray, *, deterministic: bool) -> tuple[np.ndarray, None]:
        assert deterministic is True
        return np.asarray(0), None


class _TrainingEnvironment:
    def __init__(self) -> None:
        self.closed = False

    def _preserve_seed_stream(self):
        return nullcontext()

    def close(self) -> None:
        self.closed = True


def _report(seeds: tuple[int, ...], success_count: int, policy_id: str) -> EvaluationReport:
    episodes = tuple(
        EpisodeMetrics(
            seed=seed,
            success=index < success_count,
            terminal_reason=(
                "TERMINAL_REASON_SUCCESS"
                if index < success_count
                else "TERMINAL_REASON_MISSED_JUMP"
            ),
            return_value=1.0 if index < success_count else -1.0,
            completion_ticks=10,
            jump_requests=1,
            mean_client_ticks_per_action=1.0,
            max_client_ticks_per_action=1,
            mean_server_ticks_per_action=1.0,
            max_server_ticks_per_action=1,
        )
        for index, seed in enumerate(seeds)
    )
    return EvaluationReport(
        policy_id=policy_id,
        suite="validation",
        episodes=episodes,
        success_count=success_count,
        mean_return=sum(episode.return_value for episode in episodes) / len(episodes),
        mean_completion_ticks=10.0 if success_count else None,
        mean_jump_requests_successful=1.0 if success_count else None,
        mean_client_ticks_per_action=1.0,
        max_client_ticks_per_action=1,
        mean_server_ticks_per_action=1.0,
        max_server_ticks_per_action=1,
    )


def test_training_reuses_validation_subset_and_handles_final_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = _TrainingEnvironment()
    model = _ChunkedModel()
    validation_calls: list[tuple[int, ...]] = []
    success_counts = {0: 0, 5: 2, 10: 1, 12: 3}

    monkeypatch.setattr(training, "MinecraftJumpEnv", lambda **_kwargs: env)
    monkeypatch.setattr(training, "build_model", lambda _env, _config: model)

    def evaluate(
        _env: object,
        _policy: object,
        seeds: tuple[int, ...],
        policy_id: str,
        suite: str,
    ) -> EvaluationReport:
        assert suite == "validation"
        selected = tuple(seeds)
        validation_calls.append(selected)
        step = int(policy_id.removeprefix("dqn-step-"))
        return _report(selected, success_counts[step], policy_id)

    monkeypatch.setattr(training, "evaluate_policy", evaluate)

    run = training.train(
        TrainConfig(
            total_timesteps=12,
            validation_interval=5,
            validation_episodes=3,
        ),
        tmp_path / "runs",
    )

    expected_seeds = (100_000, 100_001, 100_002)
    assert validation_calls == [expected_seeds] * 4
    assert model.learn_chunks == [5, 5, 2]
    assert model.num_timesteps == 12
    assert json.loads((run.root / "config.json").read_text())["trainer"]["validation_episodes"] == 3
    for step in (0, 5, 10, 12):
        report = json.loads((run.metrics / f"validation-step-{step:08d}.json").read_text())
        assert [episode["seed"] for episode in report["episodes"]] == list(expected_seeds)
    history = json.loads((run.root / "promotion-history.json").read_text())
    assert [promotion["step"] for promotion in history] == [0, 5, 12]
    assert (run.latest_checkpoint).read_bytes() == b"checkpoint at 12"
    assert env.closed is True
    assert "validation_interval=5, validation_episodes=3" in capsys.readouterr().err


def test_interrupted_training_saves_latest_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=70_000)
    monkeypatch.setattr(training, "MinecraftJumpEnv", lambda **_kwargs: env)

    def interrupt_learning(self: DQN, **_kwargs: object) -> DQN:
        self.num_timesteps = 7
        raise KeyboardInterrupt

    monkeypatch.setattr(DQN, "learn", interrupt_learning)
    with pytest.raises(KeyboardInterrupt):
        training.train(
            TrainConfig(total_timesteps=10, validation_interval=5, validation_episodes=1),
            tmp_path / "runs",
        )

    run = next((tmp_path / "runs").iterdir())
    assert (run / "checkpoints/latest.zip").is_file()
    report = json.loads((run / "metrics/training-interrupted.json").read_text())
    assert report == {
        "latest_checkpoint": "checkpoints/latest.zip",
        "status": "interrupted",
        "timesteps": 7,
    }
    output = capsys.readouterr().err
    assert "[train] run: interrupted; timesteps=7, latest=checkpoints/latest.zip" in output
