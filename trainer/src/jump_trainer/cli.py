"""Pool-aware training, pipeline, capacity, evaluation, and inference commands."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from stable_baselines3 import DQN

from jump_trainer.config import (
    SHOWCASE_SEED,
    VALIDATION_SEEDS,
    TrainConfig,
    evaluation_seeds,
)
from jump_trainer.endpoints import Endpoint, resolve_endpoints
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.parallel_training import run_parallel
from jump_trainer.pool import (
    ClientPool,
    ModelBatchPolicy,
    ScriptedBatchPolicy,
    TrainingSeedStreams,
)
from jump_trainer.run_directory import RunDirectory, atomic_write_json, find_run_for_checkpoint

_termination_signal: int | None = None


def _deployment() -> dict[str, Any]:
    return {
        "cluster_revision": os.environ.get("JUMP_CLUSTER_REVISION", "local"),
        "git_revision": os.environ.get("JUMP_GIT_REVISION", "unknown"),
        "images": {
            "server": os.environ.get("JUMP_SERVER_IMAGE_REVISION", "local"),
            "client": os.environ.get("JUMP_CLIENT_IMAGE_REVISION", "local"),
            "trainer": os.environ.get("JUMP_TRAINER_IMAGE_REVISION", "local"),
        },
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _validation_episode_count(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > len(VALIDATION_SEEDS):
        raise argparse.ArgumentTypeError(f"must be at most {len(VALIDATION_SEEDS)}")
    return parsed


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", action="append", default=[], metavar="HOST:PORT")
    parser.add_argument("--endpoint-template")
    parser.add_argument("--clients", type=_positive_int)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=_positive_float, default=5.0)
    parser.add_argument("--reset-retries", type=_positive_int, default=3)
    parser.add_argument(
        "--pool-startup-timeout",
        type=_positive_float,
        default=float(os.environ.get("JUMP_POOL_STARTUP_TIMEOUT", "30")),
    )


def _add_training_arguments(parser: argparse.ArgumentParser, *, require_run_id: bool) -> None:
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path(os.environ.get("JUMP_TRAINER_RUN_ROOT", "trainer/runs")),
    )
    parser.add_argument("--run-id", required=require_run_id)
    parser.add_argument("--timesteps", type=_positive_int, required=True)
    parser.add_argument("--validation-interval", type=_positive_int, default=5_000)
    parser.add_argument(
        "--validation-episodes",
        type=_validation_episode_count,
        default=20,
    )
    parser.add_argument("--seed", type=int, default=20_260_823)
    _add_connection_arguments(parser)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jump-trainer")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="run the showcase episode on every client")
    smoke.add_argument("--seed", type=int, default=SHOWCASE_SEED)
    _add_connection_arguments(smoke)

    evaluate = commands.add_parser("evaluate", help="evaluate a frozen checkpoint in parallel")
    evaluate.add_argument("--checkpoint", type=Path, required=True)
    evaluate.add_argument("--episodes", type=_positive_int, default=100)
    evaluate.add_argument("--output", type=Path)
    _add_connection_arguments(evaluate)

    training = commands.add_parser("train", help="train one shared DQN with a client pool")
    _add_training_arguments(training, require_run_id=False)

    pipeline = commands.add_parser(
        "pipeline", help="train, promote, and finally evaluate one shared DQN"
    )
    _add_training_arguments(pipeline, require_run_id=True)
    pipeline.add_argument("--evaluation-episodes", type=_positive_int, default=100)

    run = commands.add_parser("run", help="run a saved checkpoint without learning")
    checkpoint = run.add_mutually_exclusive_group(required=True)
    checkpoint.add_argument("--run", type=Path)
    checkpoint.add_argument("--checkpoint", type=Path)
    run.add_argument("--episodes", type=_positive_int, default=1)
    run.add_argument("--seed", type=int, default=SHOWCASE_SEED)
    run.add_argument("--output", type=Path)
    _add_connection_arguments(run)

    capacity = commands.add_parser(
        "capacity", help="measure sustained scripted-policy pool throughput without learning"
    )
    capacity.add_argument(
        "--transitions",
        "--timesteps",
        dest="transitions",
        type=_positive_int,
        default=2_000,
    )
    capacity.add_argument("--seed", type=int, default=20_260_823)
    capacity.add_argument("--output", type=Path)
    _add_connection_arguments(capacity)
    return parser


def _endpoints(arguments: argparse.Namespace) -> tuple[Endpoint, ...]:
    endpoint_values = list(arguments.endpoint)
    endpoint_template = arguments.endpoint_template
    clients = arguments.clients
    if not endpoint_values and endpoint_template is None and arguments.host is None:
        endpoint_template = os.environ.get("JUMP_ENDPOINT_TEMPLATE")
        configured_clients = os.environ.get("JUMP_CLIENT_COUNT")
        if endpoint_template is not None and clients is None and configured_clients is not None:
            clients = int(configured_clients)
    return resolve_endpoints(
        endpoint_values=endpoint_values,
        endpoint_template=endpoint_template,
        clients=clients,
        host=arguments.host,
        port=arguments.port,
    )


def _pool(arguments: argparse.Namespace, endpoints: tuple[Endpoint, ...]) -> ClientPool:
    return ClientPool(
        endpoints,
        startup_timeout=float(arguments.pool_startup_timeout),
        message_timeout=float(arguments.timeout),
        reset_retries=int(arguments.reset_retries),
    )


def _environment(arguments: argparse.Namespace) -> MinecraftJumpEnv:
    """Single-endpoint compatibility helper for embedding the Gymnasium environment."""

    endpoint = _endpoints(arguments)
    if len(endpoint) != 1:
        raise ValueError("a single Gymnasium environment requires exactly one endpoint")
    return MinecraftJumpEnv(
        host=endpoint[0].host,
        port=endpoint[0].port,
        timeout=float(arguments.timeout),
        reset_retries=int(arguments.reset_retries),
    )


def _default_evaluation_output(checkpoint: Path, episode_count: int) -> Path:
    if episode_count <= 0:
        raise ValueError("episodes must be positive")
    checkpoint = checkpoint.resolve()
    filename = f"performance-{checkpoint.stem}-{episode_count}-episodes.json"
    run = find_run_for_checkpoint(checkpoint)
    if run is not None:
        return run.metrics / filename
    root = Path(os.environ.get("JUMP_TRAINER_OUTPUT_ROOT", "trainer/evaluations"))
    return root / filename


def _evaluate(arguments: argparse.Namespace) -> dict[str, Any]:
    checkpoint = Path(arguments.checkpoint).resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint does not exist: {checkpoint}")
    seeds = evaluation_seeds(int(arguments.episodes))
    endpoints = _endpoints(arguments)
    model = DQN.load(checkpoint, device="cpu")
    with _pool(arguments, endpoints) as pool:
        report = pool.evaluate(
            ModelBatchPolicy(model),
            seeds,
            policy_id=checkpoint.stem,
            suite="performance",
        )
        result: dict[str, Any] = {
            "checkpoint": str(checkpoint),
            "seed_range": {"start": seeds[0], "end": seeds[-1]},
            "client_count": len(endpoints),
            "clients": [endpoint.as_dict() for endpoint in endpoints],
            "deployment": _deployment(),
            "evaluation": report.as_dict(),
            "pool": pool.stats(),
        }
        output = arguments.output or _default_evaluation_output(checkpoint, len(seeds))
        atomic_write_json(Path(output), result)
        result["report_file"] = str(Path(output).resolve())
        return result


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    run_directory: RunDirectory | None = None
    if arguments.run:
        run_directory = RunDirectory.open(Path(arguments.run))
        checkpoint = run_directory.best_checkpoint
    else:
        checkpoint = Path(arguments.checkpoint).resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint does not exist: {checkpoint}")
    endpoints = _endpoints(arguments)
    seeds = tuple(int(arguments.seed) + offset for offset in range(int(arguments.episodes)))
    model = DQN.load(checkpoint, device="cpu")
    with _pool(arguments, endpoints) as pool:
        report = pool.evaluate(
            ModelBatchPolicy(model), seeds, policy_id=checkpoint.stem, suite="run"
        )
        result: dict[str, Any] = {
            "checkpoint": str(checkpoint),
            "client_count": len(endpoints),
            "clients": [endpoint.as_dict() for endpoint in endpoints],
            "deployment": _deployment(),
            "evaluation": report.as_dict(),
            "pool": pool.stats(),
        }
        output = arguments.output
        if output is None and run_directory is not None:
            output = run_directory.metrics / "latest-run.json"
        if output is not None:
            atomic_write_json(Path(output), result)
            result["report_file"] = str(Path(output).resolve())
        return result


def _smoke(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    with _pool(arguments, endpoints) as pool:
        report = pool.evaluate(
            ScriptedBatchPolicy(),
            (int(arguments.seed),) * len(endpoints),
            policy_id="one-jump-smoke",
            suite="smoke",
            require_unique_seeds=False,
        )
        if report.success_count != len(endpoints) or any(
            episode.jump_requests != 1 for episode in report.episodes
        ):
            raise RuntimeError("scripted smoke did not succeed exactly once on every endpoint")
        return {
            "smoke": "passed",
            "client_count": len(endpoints),
            "clients": [endpoint.as_dict() for endpoint in endpoints],
            "deployment": _deployment(),
            "evaluation": report.as_dict(),
            "pool": pool.stats(),
        }


def _capacity(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    requested = int(arguments.transitions)
    with _pool(arguments, endpoints) as pool:
        collection = pool.collect(
            requested_total=requested,
            actual_total=0,
            first_cycle=0,
            seeds=TrainingSeedStreams(int(arguments.seed), endpoints),
            policy=ScriptedBatchPolicy(),
            transition_sink=lambda _transitions, _cycle: None,
        )
        result = {
            "status": "complete",
            "learning": False,
            "requested_transitions": requested,
            "actual_transitions": collection.actual_transitions,
            "overshoot": collection.actual_transitions - requested,
            "client_count": len(endpoints),
            "clients": [endpoint.as_dict() for endpoint in endpoints],
            "deployment": _deployment(),
            "throughput_transitions_per_second": collection.throughput,
            "pool": pool.stats(),
        }
        if arguments.output is not None:
            atomic_write_json(Path(arguments.output), result)
            result["report_file"] = str(Path(arguments.output).resolve())
        return result


def _train_config(arguments: argparse.Namespace, endpoints: tuple[Endpoint, ...]) -> TrainConfig:
    return TrainConfig(
        total_timesteps=int(arguments.timesteps),
        validation_interval=int(arguments.validation_interval),
        validation_episodes=int(arguments.validation_episodes),
        random_seed=int(arguments.seed),
        host=endpoints[0].host,
        port=endpoints[0].port,
        message_timeout_seconds=float(arguments.timeout),
        reset_retries=int(arguments.reset_retries),
        endpoints=tuple(endpoint.address for endpoint in endpoints),
        pool_startup_timeout_seconds=float(arguments.pool_startup_timeout),
    )


def _train(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    run = run_parallel(
        _train_config(arguments, endpoints),
        Path(arguments.run_root),
        endpoints,
        run_id=arguments.run_id,
    )
    return {"status": "complete", "run_directory": str(run.root)}


def _pipeline(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    run = run_parallel(
        _train_config(arguments, endpoints),
        Path(arguments.run_root),
        endpoints,
        run_id=str(arguments.run_id),
        final_evaluation_episodes=int(arguments.evaluation_episodes),
    )
    return {
        "status": "complete",
        "run_id": str(arguments.run_id),
        "run_directory": str(run.root),
        "best_checkpoint": str(run.best_checkpoint),
    }


def _optional_metric(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def _evaluation_summary(result: dict[str, Any]) -> str:
    evaluation = dict(result["evaluation"])
    seed_range = dict(result["seed_range"])
    terminal_reason_counts = dict(evaluation["terminal_reason_counts"])
    terminal_reasons = ", ".join(
        f"{reason}={count}" for reason, count in sorted(terminal_reason_counts.items())
    )
    episode_count = int(evaluation["episode_count"])
    success_count = int(evaluation["success_count"])
    return "\n".join(
        (
            f"checkpoint: {result['checkpoint']}",
            f"clients: {result['client_count']}",
            f"episodes/seeds: {episode_count}; "
            f"{seed_range['start']}..{seed_range['end']} inclusive",
            f"successes: {success_count}/{episode_count} ({float(evaluation['success_rate']):.2%})",
            f"terminal reasons: {terminal_reasons or 'n/a'}",
            f"mean return: {float(evaluation['mean_return']):.3f}",
            "successful episodes: "
            f"mean completion ticks={_optional_metric(evaluation['mean_completion_ticks'])}, "
            "mean jump requests="
            f"{_optional_metric(evaluation['mean_jump_requests_successful'])}",
            "tick cadence: "
            f"client mean={float(evaluation['mean_client_ticks_per_action']):.2f}, "
            f"max={int(evaluation['max_client_ticks_per_action'])}; "
            f"server mean={float(evaluation['mean_server_ticks_per_action']):.2f}, "
            f"max={int(evaluation['max_server_ticks_per_action'])} ticks/action",
            f"report: {result['report_file']}",
        )
    )


def _handle_termination(signum: int, _frame: Any) -> None:
    global _termination_signal
    _termination_signal = signum
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    arguments = _parser().parse_args()
    try:
        handlers = {
            "smoke": _smoke,
            "evaluate": _evaluate,
            "train": _train,
            "pipeline": _pipeline,
            "run": _run,
            "capacity": _capacity,
        }
        result = handlers[arguments.command](arguments)
    except (InfrastructureError, OSError, ValueError, RuntimeError) as exception:
        print(f"jump-trainer: {exception}", file=sys.stderr)
        raise SystemExit(2) from exception
    except KeyboardInterrupt:
        if _termination_signal == signal.SIGTERM:
            print("jump-trainer: interrupted by SIGTERM", file=sys.stderr)
            raise SystemExit(143) from None
        print("jump-trainer: interrupted by user", file=sys.stderr)
        raise SystemExit(130) from None
    if arguments.command == "evaluate":
        print(_evaluation_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
