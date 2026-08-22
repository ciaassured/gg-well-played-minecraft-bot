"""SB3 DQN training with deterministic periodic validation."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

import gymnasium
import numpy
import stable_baselines3
import torch
from stable_baselines3 import DQN

from jump_trainer.config import VALIDATION_SEEDS, TrainConfig
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.evaluation import (
    EvaluationReport,
    evaluate_policy,
    model_policy,
    promotion_key,
)
from jump_trainer.run_directory import RunDirectory


def build_model(env: MinecraftJumpEnv, config: TrainConfig) -> DQN:
    return DQN(
        "MlpPolicy",
        env,
        learning_rate=config.learning_rate,
        buffer_size=config.buffer_size,
        learning_starts=config.learning_starts,
        batch_size=config.batch_size,
        gamma=config.gamma,
        train_freq=config.train_frequency,
        gradient_steps=config.gradient_steps,
        target_update_interval=config.target_update_interval,
        exploration_fraction=config.exploration_fraction,
        exploration_initial_eps=config.exploration_initial_epsilon,
        exploration_final_eps=config.exploration_final_epsilon,
        policy_kwargs={"net_arch": [config.policy_width, config.policy_width]},
        seed=config.random_seed,
        device="cpu",
        verbose=1,
    )


def _versions() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gymnasium": gymnasium.__version__,
        "numpy": numpy.__version__,
        "stable_baselines3": stable_baselines3.__version__,
        "torch": torch.__version__,
    }


def _validate_candidate(
    run: RunDirectory,
    env: MinecraftJumpEnv,
    model: DQN,
    step: int,
) -> EvaluationReport:
    report = evaluate_policy(
        env,
        model_policy(model),
        VALIDATION_SEEDS,
        policy_id=f"dqn-step-{step:08d}",
        suite="validation",
    )
    run.write_json(f"metrics/validation-step-{step:08d}.json", report.as_dict())
    return report


def _promotion_metrics(report: EvaluationReport) -> dict[str, int | float | None]:
    return {
        "success_count": report.success_count,
        "mean_completion_ticks": report.mean_completion_ticks,
        "mean_jump_requests_successful": report.mean_jump_requests_successful,
    }


def train(config: TrainConfig, run_root: Path) -> RunDirectory:
    """Train from scratch and retain every lexicographically promoted checkpoint."""

    config.validate()
    run = RunDirectory.create(
        run_root,
        {
            "trainer": config.as_dict(),
            "seed_partitions": {
                "training": [0, 99_999],
                "validation": [100_000, 100_099],
                "test": [200_000, 200_099],
                "showcase": 100_000,
            },
        },
    )
    run.write_json("versions.json", _versions())
    env = MinecraftJumpEnv(
        host=config.host,
        port=config.port,
        timeout=config.message_timeout_seconds,
        reset_retries=config.reset_retries,
    )
    model = build_model(env, config)
    history: list[dict[str, Any]] = []
    best_key: tuple[int, float, float] | None = None

    try:
        model.save(run.untrained_checkpoint)
        model.save(run.latest_checkpoint)
        initial_candidate = run.candidate_checkpoint(0)
        model.save(initial_candidate)
        initial_report = _validate_candidate(run, env, model, 0)
        best_key = promotion_key(initial_report)
        retained = run.promote(initial_candidate, 0)
        history.append(
            {
                "step": 0,
                "checkpoint": str(retained.relative_to(run.root)),
                "promotion_metrics": _promotion_metrics(initial_report),
                "validation": initial_report.as_dict(),
            }
        )
        run.write_json("promotion-history.json", history)

        while model.num_timesteps < config.total_timesteps:
            remaining = config.total_timesteps - model.num_timesteps
            chunk = min(config.validation_interval, remaining)
            vector_env = model.get_env()
            if vector_env is None:
                raise RuntimeError("DQN lost its training environment")
            model.set_env(vector_env, force_reset=True)
            model.learn(total_timesteps=chunk, reset_num_timesteps=False, progress_bar=False)
            step = model.num_timesteps
            candidate = run.candidate_checkpoint(step)
            model.save(candidate)
            model.save(run.latest_checkpoint)
            report = _validate_candidate(run, env, model, step)
            key = promotion_key(report)
            if best_key is None or key > best_key:
                best_key = key
                retained = run.promote(candidate, step)
                history.append(
                    {
                        "step": step,
                        "checkpoint": str(retained.relative_to(run.root)),
                        "promotion_metrics": _promotion_metrics(report),
                        "validation": report.as_dict(),
                    }
                )
                run.write_json("promotion-history.json", history)

        run.write_json(
            "metrics/training-summary.json",
            {
                "status": "complete",
                "timesteps": model.num_timesteps,
                "promotions": len(history),
                "best_checkpoint": str(run.best_checkpoint.relative_to(run.root)),
                "best_promotion_metrics": (history[-1]["promotion_metrics"] if history else None),
            },
        )
    except InfrastructureError as exception:
        model.save(run.latest_checkpoint)
        run.write_json(
            "metrics/training-failure.json",
            {
                "status": "infrastructure_error",
                "timesteps": model.num_timesteps,
                "message": str(exception),
                "latest_checkpoint": str(run.latest_checkpoint.relative_to(run.root)),
            },
        )
        raise
    finally:
        env.close()
    return run
