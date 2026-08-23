from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from stable_baselines3 import DQN

import jump_trainer.training as training
from jump_trainer.config import TrainConfig
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.training import TrainingProgressCallback
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


def test_interrupted_training_saves_latest_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=70_000)
    monkeypatch.setattr(training, "MinecraftJumpEnv", lambda **_kwargs: env)
    monkeypatch.setattr(training, "VALIDATION_SEEDS", (100_000,))

    def interrupt_learning(self: DQN, **_kwargs: object) -> DQN:
        self.num_timesteps = 7
        raise KeyboardInterrupt

    monkeypatch.setattr(DQN, "learn", interrupt_learning)
    with pytest.raises(KeyboardInterrupt):
        training.train(
            TrainConfig(total_timesteps=10, validation_interval=5),
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
