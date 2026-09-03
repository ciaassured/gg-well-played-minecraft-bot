"""Parallel collection, learner barriers, promotion, and pipeline evaluation."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any

import gymnasium
import numpy
import stable_baselines3
import torch
from stable_baselines3 import DQN

from jump_trainer.config import (
    EVALUATION_SEED_START,
    SHOWCASE_SEED,
    TRAIN_SEED_MAX,
    TRAIN_SEED_MIN,
    VALIDATION_SEEDS,
    TrainConfig,
    evaluation_seeds,
    validation_seeds,
)
from jump_trainer.console import emit
from jump_trainer.endpoints import Endpoint
from jump_trainer.evaluation import EvaluationReport, promotion_key
from jump_trainer.learner import LearnerProcess
from jump_trainer.pool import ClientPool, ModelBatchPolicy, TrainingSeedStreams
from jump_trainer.run_directory import RunDirectory


def _versions() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gymnasium": gymnasium.__version__,
        "numpy": numpy.__version__,
        "stable_baselines3": stable_baselines3.__version__,
        "torch": torch.__version__,
        "deployment": {
            "cluster_revision": os.environ.get("JUMP_CLUSTER_REVISION", "local"),
            "git_revision": os.environ.get("JUMP_GIT_REVISION", "unknown"),
            "images": {
                "server": os.environ.get("JUMP_SERVER_IMAGE_REVISION", "local"),
                "client": os.environ.get("JUMP_CLIENT_IMAGE_REVISION", "local"),
                "trainer": os.environ.get("JUMP_TRAINER_IMAGE_REVISION", "local"),
            },
        },
    }


def _promotion_metrics(report: EvaluationReport) -> dict[str, int | float | None]:
    return {
        "success_count": report.success_count,
        "mean_completion_ticks": report.mean_completion_ticks,
        "mean_jump_requests_successful": report.mean_jump_requests_successful,
    }


def _promote(run: RunDirectory, learner: LearnerProcess, step: int) -> Path:
    """Ask the owning learner to atomically publish the selected model."""

    retained = run.promoted_checkpoint(step)
    learner.barrier(retained, run.best_checkpoint)
    return retained


def _configuration(config: TrainConfig, endpoints: tuple[Endpoint, ...]) -> dict[str, Any]:
    return {
        "trainer": config.as_dict(),
        "pool": {
            "client_count": len(endpoints),
            "clients": [endpoint.as_dict() for endpoint in endpoints],
            "startup_timeout_seconds": config.pool_startup_timeout_seconds,
        },
        "seed_partitions": {
            "training": [TRAIN_SEED_MIN, TRAIN_SEED_MAX],
            "validation": [VALIDATION_SEEDS[0], VALIDATION_SEEDS[-1]],
            "evaluation": {"start": EVALUATION_SEED_START},
            "showcase": SHOWCASE_SEED,
        },
    }


def run_parallel(
    config: TrainConfig,
    run_root: Path,
    endpoints: tuple[Endpoint, ...],
    *,
    run_id: str | None = None,
    final_evaluation_episodes: int | None = None,
) -> RunDirectory:
    """Train with a fixed pool; optionally evaluate the promoted best checkpoint in-process."""

    config.validate()
    if not endpoints:
        raise ValueError("parallel training requires at least one endpoint")
    if final_evaluation_episodes is not None and final_evaluation_episodes <= 0:
        raise ValueError("evaluation episodes must be positive")
    run = RunDirectory.create(run_root, _configuration(config, endpoints), run_id=run_id)
    versions = _versions()
    run.write_json("versions.json", versions)
    emit("train", "run", f"created; directory={run.root}")

    pool = ClientPool(
        endpoints,
        startup_timeout=config.pool_startup_timeout_seconds,
        message_timeout=config.message_timeout_seconds,
        reset_retries=config.reset_retries,
    )
    learner = LearnerProcess(
        config,
        run.untrained_checkpoint,
        run.latest_checkpoint,
        len(endpoints),
    )
    requested = config.total_timesteps
    actual = 0
    cycle = 0
    boundary_records: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    best_key: tuple[int, float, float] | None = None
    interrupted = False

    try:
        pool.start()
        learner.start()
        seed_streams = TrainingSeedStreams(config.random_seed, endpoints)
        selected_validation_seeds = validation_seeds(config.validation_episodes)
        requested_boundary = 0

        while True:
            candidate = run.candidate_checkpoint(actual)
            barrier = learner.barrier(candidate, run.latest_checkpoint)
            validation = pool.evaluate(
                learner.policy,
                selected_validation_seeds,
                policy_id=f"dqn-step-{actual:08d}",
                suite="validation",
            )
            validation_path = f"metrics/validation-step-{actual:08d}.json"
            run.write_json(validation_path, validation.as_dict())
            key = promotion_key(validation)
            promoted: str | None = None
            if best_key is None or key > best_key:
                best_key = key
                retained = _promote(run, learner, actual)
                promoted = str(retained.relative_to(run.root))
                history.append(
                    {
                        "step": actual,
                        "requested_step": requested_boundary,
                        "checkpoint": promoted,
                        "promotion_metrics": _promotion_metrics(validation),
                        "validation": validation.as_dict(),
                    }
                )
                run.write_json("promotion-history.json", history)
            emit(
                "train",
                f"validation/dqn-step-{actual:08d}",
                f"successes={validation.success_count}/{len(validation.episodes)}; "
                f"requested={requested_boundary}, actual={actual}, "
                f"promoted={promoted is not None}",
            )
            boundary_records.append(
                {
                    "requested_transitions": requested_boundary,
                    "actual_transitions": actual,
                    "overshoot": actual - requested_boundary,
                    "checkpoint": str(candidate.relative_to(run.root)),
                    "validation_report": validation_path,
                    "gradient_updates": barrier.gradient_updates,
                    "target_updates": barrier.target_updates,
                    "promoted_checkpoint": promoted,
                }
            )
            if actual >= requested:
                break

            requested_boundary = min(
                requested,
                requested_boundary + config.validation_interval,
            )
            if actual < requested_boundary:
                collection = pool.collect(
                    requested_total=requested_boundary,
                    actual_total=actual,
                    first_cycle=cycle,
                    seeds=seed_streams,
                    policy=learner.policy,
                    transition_sink=learner.submit,
                    after_cycle=learner.after_cycle,
                )
                actual = collection.actual_transitions
                cycle = collection.last_cycle
                emit(
                    "train",
                    "collect",
                    f"requested={requested_boundary}, actual={actual}, "
                    f"throughput={collection.throughput:.2f} transitions/s",
                )

        final_barrier = learner.barrier(run.latest_checkpoint)
        training_summary = {
            "status": "complete",
            "requested_transitions": requested,
            "actual_transitions": actual,
            "overshoot": actual - requested,
            "client_count": len(endpoints),
            "client_ordinals": [endpoint.ordinal for endpoint in endpoints],
            "deployment": versions["deployment"],
            "gradient_updates": final_barrier.gradient_updates,
            "target_updates": final_barrier.target_updates,
            "learner": learner.metrics(),
            "pool": pool.stats(),
            "validation_boundaries": boundary_records,
            "promotions": len(history),
            "best_checkpoint": str(run.best_checkpoint.relative_to(run.root)),
        }
        run.write_json("metrics/training-summary.json", training_summary)

        if final_evaluation_episodes is not None:
            best = DQN.load(run.best_checkpoint, device="cpu")
            evaluation = pool.evaluate(
                ModelBatchPolicy(best),
                evaluation_seeds(final_evaluation_episodes),
                policy_id="best",
                suite="performance",
            )
            run.write_json(
                f"metrics/performance-best-{final_evaluation_episodes}-episodes.json",
                {
                    "checkpoint": str(run.best_checkpoint),
                    "requested_transitions": requested,
                    "actual_transitions": actual,
                    "deployment": versions["deployment"],
                    "evaluation": evaluation.as_dict(),
                    "pool": pool.stats(),
                },
            )
        return run
    except KeyboardInterrupt:
        interrupted = True
        pool.close()
        actual = _checkpoint_failure(
            run,
            learner,
            pool,
            requested,
            actual,
            "interrupted",
            "SIGINT/SIGTERM",
            versions["deployment"],
        )
        raise
    except BaseException as exception:
        pool.close()
        _checkpoint_failure(
            run,
            learner,
            pool,
            requested,
            actual,
            "failed",
            str(exception),
            versions["deployment"],
        )
        raise
    finally:
        pool.close()
        learner.close()
        if interrupted:
            emit("train", "run", f"interrupted; requested={requested}, actual={actual}")


def _checkpoint_failure(
    run: RunDirectory,
    learner: LearnerProcess,
    pool: ClientPool,
    requested: int,
    actual: int,
    status: str,
    detail: str,
    deployment: dict[str, Any],
) -> int:
    checkpoint_error: str | None = None
    try:
        learner.barrier(run.latest_checkpoint, timeout=20.0)
    except BaseException as exception:
        checkpoint_error = str(exception)
    learner_metrics = learner.metrics()
    reported_actual = max(actual, int(learner_metrics["transitions"]))
    run.write_json(
        f"metrics/training-{status}.json",
        {
            "status": status,
            "requested_transitions": requested,
            "actual_transitions": reported_actual,
            "failure": detail,
            "deployment": deployment,
            "latest_checkpoint": str(run.latest_checkpoint.relative_to(run.root)),
            "checkpoint_error": checkpoint_error,
            "learner": learner_metrics,
            "pool": pool.stats(),
        },
    )
    return reported_actual
