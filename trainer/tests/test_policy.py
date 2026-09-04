from pathlib import Path

import numpy as np
import pytest

from yrush_trainer.checkpoint import (
    checkpoint_metadata,
    load_checkpoint,
    read_checkpoint_metadata,
    save_checkpoint,
)
from yrush_trainer.config import TrainConfig
from yrush_trainer.errors import CheckpointCompatibilityError
from yrush_trainer.policy import PPOPolicy, create_model, optimize_copy
from yrush_trainer.rollout import RolloutCollector, Transition

from .fakes import normalized_observation


def prepared_rollout(config: TrainConfig, policy: PPOPolicy):
    collector = RolloutCollector((0, 1), policy_version=0, length_per_client=256)
    observations = np.stack([normalized_observation(), normalized_observation(0.1)])
    for step in range(256):
        sampled = policy.sample(observations, deterministic=False)
        next_observations = observations + np.float32(0.0001)
        next_values = policy.values(next_observations)
        for actor in (0, 1):
            collector.add(
                Transition(
                    actor_index=actor,
                    round_sequence=(step // 16) + 1,
                    policy_version=0,
                    observation=observations[actor].copy(),
                    action=sampled.actions[actor].copy(),
                    reward=float(actor) * 0.01 - 0.001,
                    next_observation=next_observations[actor].copy(),
                    terminated=step % 16 == 15,
                    truncated=False,
                    episode_start=step % 16 == 0,
                    log_probability=float(sampled.log_probabilities[actor]),
                    value_estimate=float(sampled.values[actor]),
                    next_value_estimate=float(next_values[actor]),
                )
            )
        observations = next_observations
    return collector.prepare(gamma=config.gamma, gae_lambda=config.gae_lambda)


def test_sb3_ppo_update_and_checkpoint_inference(tmp_path: Path) -> None:
    config = TrainConfig(
        updates=1,
        optimization_epochs=1,
        endpoints=("a:1", "b:1"),
        expected_client_count=2,
        server_identity="pod-a",
        world_seed="42",
    )
    model = create_model(config, 2)
    policy = PPOPolicy(model, 0)
    rollout = prepared_rollout(config, policy)
    optimized = optimize_copy(model, rollout, config, source_policy_version=0)
    assert optimized.policy_version == 1
    assert set(optimized.metrics) == {
        "policy_loss",
        "entropy",
        "kl",
        "value_loss",
        "explained_variance",
        "clip_fraction",
        "total_loss",
    }
    assert np.isfinite(list(optimized.metrics.values())).all()

    checkpoint = tmp_path / "policy.zip"
    save_checkpoint(
        optimized.model,
        checkpoint,
        config,
        policy_version=1,
        deployment={"revision": "test"},
    )
    metadata = read_checkpoint_metadata(checkpoint)
    assert metadata["algorithm"] == "PPO"
    assert metadata["action_space"]["nvec"] == [3, 3, 2, 2, 5, 5]
    assert metadata["observation_space"]["shape"] == [513]
    assert metadata["observation_space"]["normalization"]["continuous_features"][0] == (
        "signed_target_height_difference"
    )
    reloaded, loaded_metadata = load_checkpoint(checkpoint, expected_client_count=2)
    actions = (
        PPOPolicy(reloaded, int(loaded_metadata["policy_version"]))
        .sample(np.stack([normalized_observation()]), deterministic=True)
        .actions
    )
    assert actions.shape == (1, 6)


def test_legacy_checkpoint_is_rejected_explicitly(tmp_path: Path) -> None:
    import zipfile

    legacy = tmp_path / "legacy.zip"
    with zipfile.ZipFile(legacy, "w") as archive:
        archive.writestr("data", '{"algorithm": "DQN", "replay_buffer": true}')
    with pytest.raises(CheckpointCompatibilityError, match="DQN"):
        read_checkpoint_metadata(legacy)


def test_checkpoint_with_changed_normalization_is_rejected(tmp_path: Path) -> None:
    import json
    import zipfile

    config = TrainConfig(updates=1)
    checkpoint = tmp_path / "changed-normalization.zip"
    metadata = checkpoint_metadata(config, policy_version=0, deployment={"revision": "test"})
    metadata["observation_space"]["normalization"]["target_height_scale"] = 32.0
    with zipfile.ZipFile(checkpoint, "w") as archive:
        archive.writestr("yrush-metadata.json", json.dumps(metadata))
    with pytest.raises(CheckpointCompatibilityError, match="metadata"):
        read_checkpoint_metadata(checkpoint)
