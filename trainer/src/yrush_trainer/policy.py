"""Stable-Baselines3 PPO policy inference and isolated optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import gymnasium as gym
import numpy as np
import torch
from numpy.typing import NDArray
from stable_baselines3 import PPO
from stable_baselines3.common.buffers import RolloutBuffer
from stable_baselines3.common.logger import configure
from stable_baselines3.common.vec_env import DummyVecEnv

from yrush_trainer.config import ACTION_CARDINALITIES, OBSERVATION_FEATURES, TrainConfig
from yrush_trainer.normalization import OBSERVATION_SPACE
from yrush_trainer.rollout import PreparedRollout

ACTION_SPACE = cast(
    gym.Space[NDArray[np.int64]],
    gym.spaces.MultiDiscrete(np.asarray(ACTION_CARDINALITIES, dtype=np.int64)),
)


class _SpaceOnlyEnv(gym.Env[NDArray[np.float32], NDArray[np.int64]]):
    def __init__(self) -> None:
        self.metadata = {"render_modes": []}
        self.observation_space = OBSERVATION_SPACE
        self.action_space = ACTION_SPACE

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        del options
        super().reset(seed=seed)
        return np.zeros((OBSERVATION_FEATURES,), dtype=np.float32), {}

    def step(
        self, action: NDArray[np.int64]
    ) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        del action
        return np.zeros((OBSERVATION_FEATURES,), dtype=np.float32), 0.0, False, False, {}


@dataclass(frozen=True)
class PolicyBatch:
    actions: NDArray[np.int64]
    log_probabilities: NDArray[np.float32]
    values: NDArray[np.float32]


@dataclass(frozen=True)
class OptimizationResult:
    model: PPO
    source_policy_version: int
    policy_version: int
    metrics: dict[str, float]


def create_model(config: TrainConfig, client_count: int) -> PPO:
    if client_count <= 0:
        raise ValueError("client count must be positive")
    environment = DummyVecEnv([_SpaceOnlyEnv for _ in range(client_count)])
    model = PPO(
        "MlpPolicy",
        environment,
        learning_rate=config.learning_rate,
        n_steps=config.rollout_length,
        batch_size=config.batch_size,
        n_epochs=config.optimization_epochs,
        gamma=config.gamma,
        gae_lambda=config.gae_lambda,
        clip_range=config.clip_range,
        target_kl=config.target_kl,
        ent_coef=config.entropy_coefficient,
        vf_coef=config.value_coefficient,
        max_grad_norm=config.maximum_gradient_norm,
        policy_kwargs={
            "net_arch": {
                "pi": [config.policy_width, config.policy_width],
                "vf": [config.policy_width, config.policy_width],
            }
        },
        seed=config.random_seed,
        device="cpu",
        verbose=0,
    )
    model.set_logger(configure(folder=None, format_strings=[]))
    return model


class PPOPolicy:
    def __init__(self, model: PPO, version: int) -> None:
        if version < 0:
            raise ValueError("policy version must be nonnegative")
        self.model = model
        self.version = version

    def sample(self, observations: NDArray[np.float32], *, deterministic: bool) -> PolicyBatch:
        array = np.asarray(observations, dtype=np.float32)
        with torch.no_grad():
            tensor = torch.as_tensor(array, device=self.model.device)
            actions, values, log_probabilities = self.model.policy(
                tensor, deterministic=deterministic
            )
        return PolicyBatch(
            actions=actions.cpu().numpy().astype(np.int64, copy=False),
            log_probabilities=log_probabilities.cpu().numpy().astype(np.float32, copy=False),
            values=values.flatten().cpu().numpy().astype(np.float32, copy=False),
        )

    def values(self, observations: NDArray[np.float32]) -> NDArray[np.float32]:
        array = np.asarray(observations, dtype=np.float32)
        with torch.no_grad():
            tensor = torch.as_tensor(array, device=self.model.device)
            values = self.model.policy.predict_values(tensor)
        return values.flatten().cpu().numpy().astype(np.float32, copy=False)


def optimize_copy(
    active: PPO,
    rollout: PreparedRollout,
    config: TrainConfig,
    *,
    source_policy_version: int,
) -> OptimizationResult:
    if rollout.policy_version != source_policy_version:
        raise ValueError("optimizer rollout does not match the source policy")
    if rollout.client_count != config.expected_client_count:
        raise ValueError("optimizer rollout does not contain the complete client pool")
    candidate = create_model(config, rollout.client_count)
    candidate.set_parameters(active.get_parameters(), exact_match=True)  # type: ignore[arg-type]
    buffer = RolloutBuffer(
        config.rollout_length,
        OBSERVATION_SPACE,
        ACTION_SPACE,
        device=candidate.device,
        gamma=config.gamma,
        gae_lambda=config.gae_lambda,
        n_envs=rollout.client_count,
    )
    for step in range(config.rollout_length):
        buffer.add(
            rollout.observations[step],
            rollout.actions[step],
            rollout.rewards[step],
            rollout.episode_starts[step],
            torch.as_tensor(rollout.values[step], device=candidate.device),
            torch.as_tensor(rollout.log_probabilities[step], device=candidate.device),
        )
    buffer.advantages[:] = rollout.advantages
    buffer.returns[:] = rollout.returns
    candidate.rollout_buffer = buffer
    candidate.num_timesteps = active.num_timesteps + (config.rollout_length * rollout.client_count)
    candidate._current_progress_remaining = 1.0
    candidate.train()
    names = candidate.logger.name_to_value
    metric_names = {
        "policy_loss": "train/policy_gradient_loss",
        "entropy": "train/entropy_loss",
        "kl": "train/approx_kl",
        "value_loss": "train/value_loss",
        "explained_variance": "train/explained_variance",
        "clip_fraction": "train/clip_fraction",
        "total_loss": "train/loss",
    }
    metrics = {
        output: float(names[source]) for output, source in metric_names.items() if source in names
    }
    if "entropy" in metrics:
        metrics["entropy"] = -metrics["entropy"]
    if len(metrics) != len(metric_names) or not all(
        np.isfinite(value) for value in metrics.values()
    ):
        raise RuntimeError("PPO produced missing or non-finite learning metrics")
    return OptimizationResult(
        model=candidate,
        source_policy_version=source_policy_version,
        policy_version=source_policy_version + 1,
        metrics=metrics,
    )
