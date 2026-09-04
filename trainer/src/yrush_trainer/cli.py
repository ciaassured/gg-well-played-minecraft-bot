"""Command-line entry points for shared-round PPO workflows."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from yrush_trainer.checkpoint import load_checkpoint
from yrush_trainer.config import ACTION_HOLD_TICKS, TrainConfig
from yrush_trainer.endpoints import Endpoint, resolve_endpoints
from yrush_trainer.errors import CheckpointCompatibilityError, InfrastructureError
from yrush_trainer.policy import PPOPolicy, create_model
from yrush_trainer.pool import ClientPool
from yrush_trainer.run_directory import RunDirectory, atomic_write_json
from yrush_trainer.training import deployment_metadata, evaluate_policy, train

_termination_signal: int | None = None


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _add_connections(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", action="append", default=[])
    parser.add_argument("--endpoint-template")
    parser.add_argument("--clients", type=_positive_int)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--message-timeout", type=_positive_float, default=10.0)
    parser.add_argument("--round-timeout", type=_positive_float, default=600.0)
    parser.add_argument(
        "--pool-startup-timeout",
        type=_positive_float,
        default=float(os.environ.get("YRUSH_POOL_STARTUP_TIMEOUT", "900")),
    )


def _add_training(parser: argparse.ArgumentParser, *, default_updates: int | None = None) -> None:
    parser.add_argument("--config", type=Path)
    parser.add_argument("--updates", type=_positive_int, default=default_updates)
    parser.add_argument("--evaluation-rounds", type=int, default=None)
    parser.add_argument("--learning-rate", type=_positive_float)
    parser.add_argument("--entropy-coefficient", type=float)
    parser.add_argument("--target-kl", type=_positive_float)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path(os.environ.get("YRUSH_TRAINER_RUN_ROOT", "trainer/runs")),
    )
    parser.add_argument("--run-id")
    _add_connections(parser)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yrush-trainer")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="observe and act through complete shared rounds")
    smoke.add_argument("--rounds", type=_positive_int, default=2)
    smoke.add_argument("--seed", type=int, default=20_260_904)
    _add_connections(smoke)

    training = commands.add_parser("train", help="train one feed-forward PPO policy")
    _add_training(training)

    canary = commands.add_parser("canary", help="run the bounded one-update PPO canary")
    _add_training(canary, default_updates=1)
    canary.set_defaults(evaluation_rounds=2)

    tuning = commands.add_parser("tuning-canary", help="run the bounded four-update pool canary")
    _add_training(tuning, default_updates=4)
    tuning.set_defaults(evaluation_rounds=4)

    proof = commands.add_parser("proof", help="run the bounded twelve-update proof")
    _add_training(proof, default_updates=12)
    proof.set_defaults(evaluation_rounds=8)

    evaluate = commands.add_parser("evaluate", help="evaluate a PPO archive deterministically")
    evaluate.add_argument("--checkpoint", type=Path, required=True)
    evaluate.add_argument("--rounds", type=_positive_int, default=8)
    evaluate.add_argument("--output", type=Path)
    _add_connections(evaluate)

    run = commands.add_parser("run", help="run saved-model deterministic inference")
    checkpoint = run.add_mutually_exclusive_group(required=True)
    checkpoint.add_argument("--run", type=Path)
    checkpoint.add_argument("--checkpoint", type=Path)
    run.add_argument("--rounds", type=_positive_int, default=1)
    run.add_argument("--output", type=Path)
    _add_connections(run)
    return parser


def _endpoints(arguments: argparse.Namespace) -> tuple[Endpoint, ...]:
    endpoint_values = list(arguments.endpoint)
    endpoint_template = arguments.endpoint_template
    clients = arguments.clients
    if not endpoint_values and endpoint_template is None and arguments.host is None:
        endpoint_template = os.environ.get("YRUSH_ENDPOINT_TEMPLATE")
        configured_clients = os.environ.get("YRUSH_EXPECTED_CLIENT_COUNT")
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
        message_timeout=float(arguments.message_timeout),
        round_timeout=float(arguments.round_timeout),
    )


def _config(arguments: argparse.Namespace, endpoints: tuple[Endpoint, ...]) -> TrainConfig:
    overrides: dict[str, Any] = {
        "updates": arguments.updates,
        "evaluation_rounds": arguments.evaluation_rounds,
        "learning_rate": arguments.learning_rate,
        "entropy_coefficient": arguments.entropy_coefficient,
        "target_kl": arguments.target_kl,
        "random_seed": arguments.seed,
        "message_timeout_seconds": float(arguments.message_timeout),
        "round_timeout_seconds": float(arguments.round_timeout),
        "pool_startup_timeout_seconds": float(arguments.pool_startup_timeout),
        "endpoints": tuple(endpoint.address for endpoint in endpoints),
        "expected_client_count": len(endpoints),
        "server_identity": os.environ.get("YRUSH_SERVER_POD_UID", "local"),
        "world_seed": os.environ.get("YRUSH_WORLD_SEED", "unknown"),
    }
    if arguments.config is not None:
        return TrainConfig.from_toml(arguments.config, **overrides)
    values = {key: value for key, value in overrides.items() if value is not None}
    if "updates" not in values:
        raise ValueError("--updates is required when no trainer TOML supplies it")
    values.setdefault("evaluation_rounds", 0)
    config = TrainConfig(**values)
    config.validate()
    return config


def _train(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    config = _config(arguments, endpoints)
    result = train(
        config,
        arguments.run_root,
        endpoints,
        run_id=arguments.run_id,
    )
    output = {
        "status": "complete",
        "run_directory": str(result.run.root),
        "updates": result.updates,
        "latest_policy_version": result.latest_policy_version,
        "best_policy_version": result.best_policy_version,
        "discarded_while_optimizing": result.discarded_while_optimizing,
    }
    if arguments.command in {"canary", "tuning-canary", "proof"}:
        _validate_stage(arguments.command, result.run, config)
        output["stage"] = "passed"
    return output


def _validate_stage(command: str, run: RunDirectory, config: TrainConfig) -> None:
    summary = json.loads((run.metrics / "summary.json").read_text(encoding="utf-8"))
    updates = list(summary["ppo_updates"])
    if len(updates) != config.updates:
        raise RuntimeError("bounded stage did not complete every requested PPO update")
    if any(float(update["kl"]) > config.target_kl * 2.0 for update in updates):
        raise RuntimeError("bounded stage exceeded its KL guardrail")
    if any(float(update["entropy"]) <= 0.05 for update in updates):
        raise RuntimeError("bounded stage policy entropy collapsed")
    distributions = list(summary["pool"]["action_distributions"])
    if command == "canary" and any(
        len(head) < upper for head, upper in zip(distributions, (3, 3, 2, 2, 5, 5), strict=True)
    ):
        raise RuntimeError("one-update canary did not sample every action head choice")
    if int(summary["pool"]["min_client_ticks_per_action"]) < ACTION_HOLD_TICKS:
        raise RuntimeError("client action cadence was faster than the four-tick hold")


def _smoke(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    config = TrainConfig(
        updates=1,
        random_seed=int(arguments.seed),
        endpoints=tuple(endpoint.address for endpoint in endpoints),
        expected_client_count=len(endpoints),
        message_timeout_seconds=float(arguments.message_timeout),
        round_timeout_seconds=float(arguments.round_timeout),
        pool_startup_timeout_seconds=float(arguments.pool_startup_timeout),
    )
    policy = PPOPolicy(create_model(config, len(endpoints)), 0)
    with _pool(arguments, endpoints) as pool:
        driven = pool.drive(policy, deterministic=False, rounds=int(arguments.rounds))
        return {
            "status": "passed",
            "global_rounds": [round_.as_dict() for round_ in driven.rounds],
            "pool": pool.stats(),
            "deployment": deployment_metadata(),
        }


def _evaluate(arguments: argparse.Namespace) -> dict[str, Any]:
    endpoints = _endpoints(arguments)
    model, metadata = load_checkpoint(arguments.checkpoint, expected_client_count=len(endpoints))
    policy = PPOPolicy(model, int(metadata["policy_version"]))
    with _pool(arguments, endpoints) as pool:
        report = evaluate_policy(
            pool,
            policy,
            rounds=int(arguments.rounds),
            policy_id=arguments.checkpoint.stem,
        )
        result = {
            "checkpoint": str(arguments.checkpoint.resolve()),
            "evaluation": report.as_dict(),
            "pool": pool.stats(),
            "deployment": deployment_metadata(),
        }
    if arguments.output is not None:
        atomic_write_json(arguments.output, result)
        result["report_file"] = str(arguments.output.resolve())
    return result


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.run is not None:
        checkpoint = RunDirectory.open(arguments.run).best_checkpoint
    else:
        checkpoint = arguments.checkpoint
    arguments.checkpoint = checkpoint
    return _evaluate(arguments)


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
            "train": _train,
            "canary": _train,
            "tuning-canary": _train,
            "proof": _train,
            "evaluate": _evaluate,
            "run": _run,
        }
        result = handlers[arguments.command](arguments)
    except (
        CheckpointCompatibilityError,
        FileNotFoundError,
        InfrastructureError,
        OSError,
        ValueError,
        RuntimeError,
    ) as exception:
        print(f"yrush-trainer: {exception}", file=sys.stderr)
        raise SystemExit(2) from exception
    except KeyboardInterrupt:
        if _termination_signal == signal.SIGTERM:
            print("yrush-trainer: interrupted by SIGTERM", file=sys.stderr)
            raise SystemExit(143) from None
        print("yrush-trainer: interrupted by user", file=sys.stderr)
        raise SystemExit(130) from None
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
