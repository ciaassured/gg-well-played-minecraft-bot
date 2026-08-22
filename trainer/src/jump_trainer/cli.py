"""Command-line entry points for training, evaluation, smoke tests, and inference."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from stable_baselines3 import DQN

from jump_trainer.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SHOWCASE_SEED,
    TrainConfig,
    seeds_for_suite,
)
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.evaluation import (
    always_jump_policy,
    evaluate_policy,
    final_passing_result,
    model_policy,
    noop_policy,
)
from jump_trainer.run_directory import (
    RunDirectory,
    atomic_write_json,
    find_run_for_checkpoint,
)
from jump_trainer.training import train


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--reset-retries", type=int, default=3)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jump-trainer")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="prove reset and one scripted remote episode")
    smoke.add_argument("--seed", type=int, default=SHOWCASE_SEED)
    _add_connection_arguments(smoke)

    evaluate = commands.add_parser("evaluate", help="deterministically evaluate without learning")
    subject = evaluate.add_mutually_exclusive_group(required=True)
    subject.add_argument("--policy", choices=("noop", "always-jump"))
    subject.add_argument("--checkpoint", type=Path)
    evaluate.add_argument("--suite", choices=("validation", "test"), required=True)
    evaluate.add_argument("--output", type=Path)
    _add_connection_arguments(evaluate)

    training = commands.add_parser("train", help="train DQN from scratch")
    training.add_argument(
        "--run-root",
        type=Path,
        default=Path(os.environ.get("JUMP_TRAINER_RUN_ROOT", "trainer/runs")),
    )
    training.add_argument("--timesteps", type=int, default=30_000)
    training.add_argument("--validation-interval", type=int, default=5_000)
    training.add_argument("--seed", type=int, default=20_260_823)
    _add_connection_arguments(training)

    run = commands.add_parser("run", help="run a saved checkpoint without learning")
    checkpoint = run.add_mutually_exclusive_group(required=True)
    checkpoint.add_argument("--run", type=Path)
    checkpoint.add_argument("--checkpoint", type=Path)
    run.add_argument("--episodes", type=int, default=1)
    run.add_argument("--seed", type=int, default=SHOWCASE_SEED)
    run.add_argument("--output", type=Path)
    _add_connection_arguments(run)
    return parser


def _environment(arguments: argparse.Namespace) -> MinecraftJumpEnv:
    return MinecraftJumpEnv(
        host=str(arguments.host),
        port=int(arguments.port),
        timeout=float(arguments.timeout),
        reset_retries=int(arguments.reset_retries),
    )


def _default_evaluation_output(policy_id: str, suite: str, checkpoint: Path | None) -> Path:
    if checkpoint is not None:
        run = find_run_for_checkpoint(checkpoint)
        if run is not None:
            return run.metrics / f"evaluation-{policy_id}-{suite}.json"
    root = Path(os.environ.get("JUMP_TRAINER_OUTPUT_ROOT", "trainer/evaluations"))
    return root / f"evaluation-{policy_id}-{suite}.json"


def _evaluate(arguments: argparse.Namespace) -> dict[str, Any]:
    seeds = seeds_for_suite(str(arguments.suite))
    env = _environment(arguments)
    checkpoint = Path(arguments.checkpoint).resolve() if arguments.checkpoint else None
    try:
        if arguments.policy:
            policy_id = str(arguments.policy)
            policy = noop_policy if policy_id == "noop" else always_jump_policy
            report = evaluate_policy(env, policy, seeds, policy_id, str(arguments.suite))
            result: dict[str, Any] = {"evaluation": report.as_dict()}
        else:
            if checkpoint is None or not checkpoint.is_file():
                raise FileNotFoundError(f"checkpoint does not exist: {checkpoint}")
            model = DQN.load(checkpoint, device="cpu")
            policy_id = checkpoint.stem
            report = evaluate_policy(
                env, model_policy(model), seeds, policy_id, str(arguments.suite)
            )
            result = {"evaluation": report.as_dict()}
            if arguments.suite == "test":
                noop = evaluate_policy(env, noop_policy, seeds, "noop", "test")
                always = evaluate_policy(env, always_jump_policy, seeds, "always-jump", "test")
                result["baselines"] = {
                    "noop": noop.as_dict(),
                    "always_jump": always.as_dict(),
                }
                result["acceptance"] = final_passing_result(report, noop, always)
        output = arguments.output or _default_evaluation_output(
            policy_id, str(arguments.suite), checkpoint
        )
        atomic_write_json(Path(output), result)
        result["report_file"] = str(Path(output).resolve())
        return result
    finally:
        env.close()


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    run_directory: RunDirectory | None = None
    if arguments.run:
        run_directory = RunDirectory.open(Path(arguments.run))
        checkpoint = run_directory.best_checkpoint
    else:
        checkpoint = Path(arguments.checkpoint).resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint does not exist: {checkpoint}")
    if arguments.episodes <= 0:
        raise ValueError("episodes must be positive")
    model = DQN.load(checkpoint, device="cpu")
    seeds = tuple(int(arguments.seed) + offset for offset in range(int(arguments.episodes)))
    env = _environment(arguments)
    try:
        report = evaluate_policy(env, model_policy(model), seeds, checkpoint.stem, "run")
    finally:
        env.close()
    result = {"checkpoint": str(checkpoint), "evaluation": report.as_dict()}
    output = arguments.output
    if output is None and run_directory is not None:
        output = run_directory.metrics / "latest-run.json"
    if output is not None:
        atomic_write_json(Path(output), result)
        result["report_file"] = str(Path(output).resolve())
    return result


def _smoke(arguments: argparse.Namespace) -> dict[str, Any]:
    env = _environment(arguments)
    try:
        report = evaluate_policy(
            env,
            always_jump_policy,
            (int(arguments.seed),),
            "always-jump-smoke",
            "smoke",
        )
        return {"smoke": "passed", "evaluation": report.as_dict()}
    finally:
        env.close()


def _train(arguments: argparse.Namespace) -> dict[str, Any]:
    config = TrainConfig(
        total_timesteps=int(arguments.timesteps),
        validation_interval=int(arguments.validation_interval),
        random_seed=int(arguments.seed),
        host=str(arguments.host),
        port=int(arguments.port),
        message_timeout_seconds=float(arguments.timeout),
        reset_retries=int(arguments.reset_retries),
    )
    run = train(config, Path(arguments.run_root))
    return {"status": "complete", "run_directory": str(run.root)}


def main() -> None:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "smoke":
            result = _smoke(arguments)
        elif arguments.command == "evaluate":
            result = _evaluate(arguments)
        elif arguments.command == "train":
            result = _train(arguments)
        elif arguments.command == "run":
            result = _run(arguments)
        else:
            raise AssertionError(f"unhandled command: {arguments.command}")
    except (InfrastructureError, FileNotFoundError, ValueError, RuntimeError) as exception:
        print(f"jump-trainer: {exception}", file=sys.stderr)
        raise SystemExit(2) from exception
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
