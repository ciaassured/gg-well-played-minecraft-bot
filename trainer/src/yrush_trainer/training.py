"""Frozen-rollout PPO updates, global switching, evaluation, and artifacts."""

from __future__ import annotations

import os
import queue
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from yrush_trainer.checkpoint import load_checkpoint, save_checkpoint
from yrush_trainer.config import TrainConfig
from yrush_trainer.console import emit, format_duration
from yrush_trainer.endpoints import Endpoint
from yrush_trainer.evaluation import EvaluationReport
from yrush_trainer.policy import OptimizationResult, PPOPolicy, create_model, optimize_copy
from yrush_trainer.pool import ClientPool
from yrush_trainer.rollout import RolloutCollector, Transition
from yrush_trainer.run_directory import RunDirectory


@dataclass(frozen=True)
class TrainResult:
    run: RunDirectory
    updates: int
    latest_policy_version: int
    best_policy_version: int
    discarded_while_optimizing: int
    reports: tuple[EvaluationReport, ...]


def deployment_metadata() -> dict[str, Any]:
    return {
        "cluster_revision": os.environ.get("YRUSH_CLUSTER_REVISION", "local"),
        "git_revision": os.environ.get("YRUSH_GIT_REVISION", "unknown"),
        "images": {
            "server": os.environ.get("YRUSH_SERVER_IMAGE_REVISION", "local"),
            "client": os.environ.get("YRUSH_CLIENT_IMAGE_REVISION", "local"),
            "trainer": os.environ.get("YRUSH_TRAINER_IMAGE_REVISION", "local"),
        },
        "server_pod_uid": os.environ.get("YRUSH_SERVER_POD_UID", "local"),
        "server_restart_count": int(os.environ.get("YRUSH_SERVER_RESTART_COUNT", "0")),
    }


def evaluate_policy(
    pool: ClientPool,
    policy: PPOPolicy,
    *,
    rounds: int,
    policy_id: str,
) -> EvaluationReport:
    driven = pool.drive(policy, deterministic=True, rounds=rounds)
    return EvaluationReport(
        policy_id=policy_id,
        policy_version=policy.version,
        rounds=driven.rounds,
    )


def train(
    config: TrainConfig,
    run_root: Path,
    endpoints: tuple[Endpoint, ...],
    *,
    run_id: str | None = None,
    pool: ClientPool | None = None,
) -> TrainResult:
    config.validate()
    if len(endpoints) != config.expected_client_count:
        raise ValueError("configured endpoint count does not match the fixed pool")
    deployment = deployment_metadata()
    run = RunDirectory.create(
        run_root,
        config.as_dict()
        | {
            "clients": [endpoint.as_dict() for endpoint in endpoints],
            "deployment": deployment,
        },
        run_id,
    )
    run.write_json(
        "versions.json",
        {
            "algorithm": "PPO",
            "protocol": "yrush.v1",
            "yrush_packet_schema": 1,
            "deployment": deployment,
            "server_identity": config.server_identity,
            "world_seed": config.world_seed,
            "expected_client_count": config.expected_client_count,
        },
    )

    selected_pool = pool or ClientPool(
        endpoints,
        startup_timeout=config.pool_startup_timeout_seconds,
        message_timeout=config.message_timeout_seconds,
        round_timeout=config.round_timeout_seconds,
    )
    owns_pool = pool is None
    started = monotonic()
    model = create_model(config, len(endpoints))
    active = PPOPolicy(model, 0)
    save_checkpoint(
        model,
        run.untrained_checkpoint,
        config,
        policy_version=0,
        deployment=deployment,
    )
    shutil.copy2(run.untrained_checkpoint, run.best_checkpoint)
    save_checkpoint(
        model,
        run.latest_checkpoint,
        config,
        policy_version=0,
        deployment=deployment,
    )

    collector: RolloutCollector | None = RolloutCollector(
        tuple(endpoint.index for endpoint in endpoints),
        policy_version=0,
        length_per_client=config.rollout_length,
    )
    optimizer_thread: threading.Thread | None = None
    optimizer_output: queue.Queue[OptimizationResult | BaseException] = queue.Queue(maxsize=1)
    completed_updates = 0
    discarded_while_optimizing = 0
    update_metrics: list[dict[str, Any]] = []

    def start_optimizer(prepared: Any, source: PPOPolicy) -> None:
        nonlocal optimizer_thread

        def work() -> None:
            try:
                optimizer_output.put(
                    optimize_copy(
                        source.model,
                        prepared,
                        config,
                        source_policy_version=source.version,
                    )
                )
            except BaseException as exception:
                optimizer_output.put(exception)

        optimizer_thread = threading.Thread(
            target=work,
            name=f"yrush-ppo-optimizer-v{source.version}",
            daemon=True,
        )
        optimizer_thread.start()
        emit(
            "train",
            "rollout",
            f"closed policy={source.version}; optimizing "
            f"{config.rollout_length} transitions/client",
        )

    def receive_transition(transition: Transition) -> None:
        nonlocal collector, discarded_while_optimizing
        if collector is None:
            discarded_while_optimizing += 1
            return
        collector.add(transition)
        if collector.complete:
            prepared = collector.prepare(gamma=config.gamma, gae_lambda=config.gae_lambda)
            source = active
            collector = None
            start_optimizer(prepared, source)

    def boundary(summary: Any, current: Any) -> tuple[Any, bool]:
        nonlocal active, collector, completed_updates, optimizer_thread
        run.append_jsonl("metrics/rounds.jsonl", summary.as_dict())
        emit(
            "train",
            "round",
            f"sequence={summary.round_sequence} policy={current.version} "
            f"outcome={'win' if summary.completed else 'stopped' if summary.stopped else 'draw'}",
        )
        try:
            optimized = optimizer_output.get_nowait()
        except queue.Empty:
            optimized = None
        if isinstance(optimized, BaseException):
            raise RuntimeError(f"PPO optimizer failed: {optimized}") from optimized
        if optimized is not None:
            if optimized.source_policy_version != current.version:
                raise RuntimeError("optimizer returned a stale policy version")
            active = PPOPolicy(optimized.model, optimized.policy_version)
            completed_updates += 1
            candidate = run.candidate_checkpoint(active.version)
            save_checkpoint(
                active.model,
                candidate,
                config,
                policy_version=active.version,
                deployment=deployment,
            )
            save_checkpoint(
                active.model,
                run.latest_checkpoint,
                config,
                policy_version=active.version,
                deployment=deployment,
            )
            metrics = {
                "update": completed_updates,
                "policy_version": active.version,
                "source_policy_version": optimized.source_policy_version,
                "round_sequence_switched": summary.round_sequence,
                **optimized.metrics,
            }
            update_metrics.append(metrics)
            run.append_jsonl("metrics/ppo-updates.jsonl", metrics)
            emit(
                "train",
                "update",
                f"{completed_updates}/{config.updates}; policy={active.version}; "
                f"kl={optimized.metrics['kl']:.5f}; entropy={optimized.metrics['entropy']:.5f}",
            )
            optimizer_thread = None
            if completed_updates < config.updates:
                collector = RolloutCollector(
                    tuple(endpoint.index for endpoint in endpoints),
                    policy_version=active.version,
                    length_per_client=config.rollout_length,
                )
        return active, completed_updates >= config.updates

    try:
        selected_pool.start()
        driven = selected_pool.drive(
            active,
            deterministic=False,
            transition_sink=receive_transition,
            boundary_callback=boundary,
        )
        if optimizer_thread is not None:
            optimizer_thread.join()
    finally:
        if owns_pool:
            selected_pool.close()

    reports: list[EvaluationReport] = []
    best_policy_version = active.version
    if config.evaluation_rounds > 0:
        evaluation_pool = pool or ClientPool(
            endpoints,
            startup_timeout=config.pool_startup_timeout_seconds,
            message_timeout=config.message_timeout_seconds,
            round_timeout=config.round_timeout_seconds,
        )
        evaluation_owns_pool = pool is None
        try:
            evaluation_pool.start()
            checkpoints = [run.untrained_checkpoint, *sorted(run.candidates.glob("policy-*.zip"))]
            best_report: EvaluationReport | None = None
            best_checkpoint = run.untrained_checkpoint
            for checkpoint in checkpoints:
                candidate_model, metadata = load_checkpoint(
                    checkpoint, expected_client_count=config.expected_client_count
                )
                version = int(metadata["policy_version"])
                report = evaluate_policy(
                    evaluation_pool,
                    PPOPolicy(candidate_model, version),
                    rounds=config.evaluation_rounds,
                    policy_id=checkpoint.stem,
                )
                reports.append(report)
                run.write_json(f"metrics/evaluation-policy-{version:06d}.json", report.as_dict())
                if best_report is None or report.promotion_key > best_report.promotion_key:
                    best_report = report
                    best_checkpoint = checkpoint
                    best_policy_version = version
            run.promote(best_checkpoint, best_policy_version)
        finally:
            if evaluation_owns_pool:
                evaluation_pool.close()
    else:
        run.promote(run.latest_checkpoint, active.version)

    elapsed = monotonic() - started
    summary = {
        "status": "complete",
        "updates": completed_updates,
        "latest_policy_version": active.version,
        "best_policy_version": best_policy_version,
        "valid_transitions": driven.valid_transitions,
        "discarded_transitions": driven.discarded_transitions,
        "discarded_while_optimizing": discarded_while_optimizing,
        "elapsed_seconds": elapsed,
        "pool": selected_pool.stats(),
        "ppo_updates": update_metrics,
        "evaluations": [report.as_dict() for report in reports],
        "server_identity": config.server_identity,
        "server_pod_uid": deployment["server_pod_uid"],
        "server_restart_count": deployment["server_restart_count"],
    }
    run.write_json("metrics/summary.json", summary)
    emit(
        "train",
        "complete",
        f"updates={completed_updates}; elapsed={format_duration(elapsed)}; "
        f"latest={run.latest_checkpoint}",
    )
    return TrainResult(
        run=run,
        updates=completed_updates,
        latest_policy_version=active.version,
        best_policy_version=best_policy_version,
        discarded_while_optimizing=discarded_while_optimizing,
        reports=tuple(reports),
    )
