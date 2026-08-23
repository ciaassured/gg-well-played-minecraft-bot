"""SB3 DQN training with deterministic periodic validation."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from statistics import fmean
from time import monotonic
from typing import Any

import gymnasium
import numpy
import stable_baselines3
import torch
from stable_baselines3 import DQN
from stable_baselines3.common.callbacks import BaseCallback

from jump_trainer.config import VALIDATION_SEEDS, TrainConfig
from jump_trainer.console import emit, format_duration
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.evaluation import (
    EvaluationReport,
    evaluate_policy,
    model_policy,
    promotion_key,
)
from jump_trainer.run_directory import RunDirectory

TRAIN_PROGRESS_TIMESTEP_INTERVAL = 250


class TrainingProgressCallback(BaseCallback):
    """Replace SB3's box tables with stable one-line training records."""

    def __init__(
        self, total_timesteps: int, report_interval: int = TRAIN_PROGRESS_TIMESTEP_INTERVAL
    ):
        super().__init__(verbose=0)
        if total_timesteps <= 0:
            raise ValueError("total timesteps must be positive")
        if report_interval <= 0:
            raise ValueError("report interval must be positive")
        self.total_timesteps = total_timesteps
        self.report_interval = report_interval
        self.next_report = report_interval
        self.completed_episodes = 0
        self.active_elapsed_seconds = 0.0
        self.segment_started_at: float | None = None
        self.last_reported_step = -1

    def _on_training_start(self) -> None:
        self.segment_started_at = monotonic()

    def _on_step(self) -> bool:
        dones = self.locals.get("dones")
        if dones is not None:
            self.completed_episodes += sum(bool(done) for done in numpy.asarray(dones).reshape(-1))
        step = int(self.model.num_timesteps)
        if step >= self.next_report or step >= self.total_timesteps:
            self._report(step)
            while self.next_report <= step:
                self.next_report += self.report_interval
        return True

    def _on_training_end(self) -> None:
        if self.segment_started_at is not None:
            self.active_elapsed_seconds += monotonic() - self.segment_started_at
            self.segment_started_at = None
        step = int(self.model.num_timesteps)
        if step >= self.total_timesteps and self.last_reported_step != step:
            self._report(step)

    def _elapsed(self) -> float:
        elapsed = self.active_elapsed_seconds
        if self.segment_started_at is not None:
            elapsed += monotonic() - self.segment_started_at
        return elapsed

    def _report(self, step: int) -> None:
        elapsed = self._elapsed()
        fps = step / elapsed if elapsed > 0.0 else 0.0
        remaining = (self.total_timesteps - step) / fps if fps > 0.0 else 0.0
        episode_information = tuple(self.model.ep_info_buffer or ())
        mean_length = (
            f"{fmean(float(item['l']) for item in episode_information):.1f}"
            if episode_information
            else "n/a"
        )
        mean_return = (
            f"{fmean(float(item['r']) for item in episode_information):.3f}"
            if episode_information
            else "n/a"
        )
        exploration = float(getattr(self.model, "exploration_rate", 0.0))
        emit(
            "train",
            "learn",
            f"{step}/{self.total_timesteps} timesteps; "
            f"episodes={self.completed_episodes}, mean_length={mean_length}, "
            f"mean_return={mean_return}, exploration={exploration:.3f}, fps={fps:.1f}, "
            f"elapsed={format_duration(elapsed)}, eta={format_duration(remaining)}",
        )
        self.last_reported_step = step


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
        verbose=0,
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


def _log(subject: str, detail: str) -> None:
    emit("train", subject, detail)


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
    _log(
        f"validation/dqn-step-{step:08d}",
        f"complete; successes={report.success_count}/{len(report.episodes)}, "
        f"mean_return={report.mean_return:.3f}, "
        f"mean_ticks={report.mean_completion_ticks}, "
        f"mean_jumps={report.mean_jump_requests_successful}",
    )
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
    _log("run", f"created; directory={run.root.resolve()}")
    env = MinecraftJumpEnv(
        host=config.host,
        port=config.port,
        timeout=config.message_timeout_seconds,
        reset_retries=config.reset_retries,
    )
    model = build_model(env, config)
    _log(
        "model",
        f"initialized; device=cpu, network={config.policy_width}x{config.policy_width}, "
        f"learning_rate={config.learning_rate:g}, "
        f"exploration={config.exploration_initial_epsilon:.3f}"
        f"->{config.exploration_final_epsilon:.3f}",
    )
    _log(
        "schedule",
        f"total_timesteps={config.total_timesteps}, "
        f"validation_interval={config.validation_interval}, "
        f"progress_interval={TRAIN_PROGRESS_TIMESTEP_INTERVAL}",
    )
    history: list[dict[str, Any]] = []
    best_key: tuple[int, float, float] | None = None
    progress = TrainingProgressCallback(config.total_timesteps)

    try:
        model.save(run.untrained_checkpoint)
        model.save(run.latest_checkpoint)
        _log(
            "checkpoint/untrained",
            f"saved; path={run.untrained_checkpoint.relative_to(run.root)}",
        )
        initial_candidate = run.candidate_checkpoint(0)
        model.save(initial_candidate)
        initial_report = _validate_candidate(run, env, model, 0)
        best_key = promotion_key(initial_report)
        retained = run.promote(initial_candidate, 0)
        _log(
            "promotion/dqn-step-00000000",
            f"promoted; path={retained.relative_to(run.root)}, "
            f"successes={initial_report.success_count}, "
            f"mean_ticks={initial_report.mean_completion_ticks}, "
            f"mean_jumps={initial_report.mean_jump_requests_successful}",
        )
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
            target_step = model.num_timesteps + chunk
            _log(
                "learn",
                f"{model.num_timesteps}/{config.total_timesteps} timesteps; "
                f"starting, next_validation={target_step}",
            )
            vector_env = model.get_env()
            if vector_env is None:
                raise RuntimeError("DQN lost its training environment")
            model.set_env(vector_env, force_reset=True)
            model.learn(
                total_timesteps=chunk,
                reset_num_timesteps=False,
                progress_bar=False,
                callback=progress,
            )
            step = model.num_timesteps
            candidate = run.candidate_checkpoint(step)
            model.save(candidate)
            model.save(run.latest_checkpoint)
            _log(
                f"checkpoint/dqn-step-{step:08d}",
                f"saved; candidate={candidate.relative_to(run.root)}, "
                f"latest={run.latest_checkpoint.relative_to(run.root)}",
            )
            report = _validate_candidate(run, env, model, step)
            key = promotion_key(report)
            if best_key is None or key > best_key:
                best_key = key
                retained = run.promote(candidate, step)
                _log(
                    f"promotion/dqn-step-{step:08d}",
                    f"promoted; path={retained.relative_to(run.root)}, "
                    f"successes={report.success_count}, "
                    f"mean_ticks={report.mean_completion_ticks}, "
                    f"mean_jumps={report.mean_jump_requests_successful}",
                )
                history.append(
                    {
                        "step": step,
                        "checkpoint": str(retained.relative_to(run.root)),
                        "promotion_metrics": _promotion_metrics(report),
                        "validation": report.as_dict(),
                    }
                )
                run.write_json("promotion-history.json", history)
            else:
                _log(
                    f"promotion/dqn-step-{step:08d}",
                    f"retained existing best; successes={report.success_count}, "
                    f"mean_ticks={report.mean_completion_ticks}, "
                    f"mean_jumps={report.mean_jump_requests_successful}",
                )

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
        _log(
            "run",
            f"complete; timesteps={model.num_timesteps}, promotions={len(history)}, "
            f"best={run.best_checkpoint.relative_to(run.root)}",
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
