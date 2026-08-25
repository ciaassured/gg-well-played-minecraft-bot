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
    evaluation_seeds,
)
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.evaluation import (
    evaluate_policy,
    model_policy,
    scripted_one_jump_policy,
)
from jump_trainer.recording import RecordingSession, recording_directory
from jump_trainer.run_directory import (
    RunDirectory,
    atomic_write_json,
    find_run_for_checkpoint,
)
from jump_trainer.training import train


def _add_connection_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--reset-retries", type=int, default=3)
    parser.add_argument(
        "--recording-timeout",
        type=float,
        default=float(os.environ.get("JUMP_TRAINER_RECORDING_TIMEOUT", "300")),
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jump-trainer")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="prove reset and one scripted remote episode")
    smoke.add_argument("--seed", type=int, default=SHOWCASE_SEED)
    _add_connection_arguments(smoke)

    evaluate = commands.add_parser(
        "evaluate", help="report deterministic checkpoint performance without learning"
    )
    evaluate.add_argument("--checkpoint", type=Path, required=True)
    evaluate.add_argument("--episodes", type=_positive_int, default=100)
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


def _recording(arguments: argparse.Namespace, command: str) -> RecordingSession:
    directory = recording_directory(command)
    return RecordingSession(
        command,
        directory,
        host=str(arguments.host),
        port=int(arguments.port),
        message_timeout=float(arguments.timeout),
        recording_timeout=float(arguments.recording_timeout),
    )


def _environment(arguments: argparse.Namespace, recording: RecordingSession) -> MinecraftJumpEnv:
    return MinecraftJumpEnv(
        host=str(arguments.host),
        port=int(arguments.port),
        timeout=float(arguments.timeout),
        reset_retries=int(arguments.reset_retries),
        connection_factory=recording.connection,
        episode_recorder=recording,
        owns_connection=False,
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
    model = DQN.load(checkpoint, device="cpu")
    policy_id = checkpoint.stem
    with _recording(arguments, "evaluate") as recording:
        env = _environment(arguments, recording)
        env.set_recording_context(
            policy_id=policy_id,
            suite="performance",
            checkpoint=str(checkpoint),
        )
        try:
            report = evaluate_policy(
                env,
                model_policy(model),
                seeds,
                policy_id,
                "performance",
            )
            result: dict[str, Any] = {
                "checkpoint": str(checkpoint),
                "seed_range": {"start": seeds[0], "end": seeds[-1]},
                "evaluation": report.as_dict(),
            }
            output = arguments.output or _default_evaluation_output(checkpoint, len(seeds))
            atomic_write_json(Path(output), result)
            result["report_file"] = str(Path(output).resolve())
        finally:
            env.close()
    return result


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
    with _recording(arguments, "run") as recording:
        env = _environment(arguments, recording)
        env.set_recording_context(
            policy_id=checkpoint.stem,
            suite="run",
            checkpoint=str(checkpoint),
        )
        try:
            report = evaluate_policy(env, model_policy(model), seeds, checkpoint.stem, "run")
            result = {"checkpoint": str(checkpoint), "evaluation": report.as_dict()}
            output = arguments.output
            if output is None and run_directory is not None:
                output = run_directory.metrics / "latest-run.json"
            if output is not None:
                atomic_write_json(Path(output), result)
                result["report_file"] = str(Path(output).resolve())
        finally:
            env.close()
    return result


def _smoke(arguments: argparse.Namespace) -> dict[str, Any]:
    with _recording(arguments, "smoke") as recording:
        env = _environment(arguments, recording)
        env.set_recording_context(policy_id="one-jump-smoke", suite="smoke")
        try:
            report = evaluate_policy(
                env,
                scripted_one_jump_policy(),
                (int(arguments.seed),),
                "one-jump-smoke",
                "smoke",
            )
            if report.success_count != 1 or report.episodes[0].jump_requests != 1:
                raise RuntimeError("scripted one-jump smoke episode did not succeed exactly once")
            result = {"smoke": "passed", "evaluation": report.as_dict()}
        finally:
            env.close()
    return result


def _train(arguments: argparse.Namespace) -> dict[str, Any]:
    config = TrainConfig(
        total_timesteps=int(arguments.timesteps),
        validation_interval=int(arguments.validation_interval),
        random_seed=int(arguments.seed),
        host=str(arguments.host),
        port=int(arguments.port),
        message_timeout_seconds=float(arguments.timeout),
        recording_timeout_seconds=float(arguments.recording_timeout),
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
    except (InfrastructureError, OSError, ValueError, RuntimeError) as exception:
        print(f"jump-trainer: {exception}", file=sys.stderr)
        raise SystemExit(2) from exception
    except KeyboardInterrupt:
        print("jump-trainer: interrupted by user", file=sys.stderr)
        raise SystemExit(130) from None
    if arguments.command == "evaluate":
        print(_evaluation_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
