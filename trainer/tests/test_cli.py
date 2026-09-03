from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

import jump_trainer.cli as cli
from jump_trainer.endpoints import Endpoint
from jump_trainer.env import NOOP, MinecraftJumpEnv
from jump_trainer.pool import ClientPool
from jump_trainer.run_directory import RunDirectory
from tests.fakes import SimulatedConnection


def test_train_parser_requires_timesteps_and_resolves_defaults() -> None:
    parser = cli._parser()

    with pytest.raises(SystemExit) as missing:
        parser.parse_args(["train"])
    assert missing.value.code == 2

    arguments = parser.parse_args(["train", "--timesteps", "30000"])
    assert arguments.timesteps == 30_000
    assert arguments.seed == 20_260_823
    assert arguments.validation_interval == 5_000
    assert arguments.validation_episodes == 20


def test_train_parser_accepts_schedule_overrides() -> None:
    arguments = cli._parser().parse_args(
        [
            "train",
            "--timesteps",
            "45000",
            "--seed",
            "7",
            "--validation-interval",
            "7500",
            "--validation-episodes",
            "100",
        ]
    )

    assert arguments.timesteps == 45_000
    assert arguments.seed == 7
    assert arguments.validation_interval == 7_500
    assert arguments.validation_episodes == 100


@pytest.mark.parametrize(
    "arguments",
    (
        ["train", "--timesteps", "0"],
        ["train", "--timesteps", "-1"],
        ["train", "--timesteps", "10", "--validation-interval", "0"],
        ["train", "--timesteps", "10", "--validation-interval", "-1"],
        ["train", "--timesteps", "10", "--validation-episodes", "0"],
        ["train", "--timesteps", "10", "--validation-episodes", "-1"],
        ["train", "--timesteps", "10", "--validation-episodes", "101"],
    ),
)
def test_train_parser_rejects_invalid_schedule_values(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli._parser().parse_args(arguments)
    assert raised.value.code == 2


def test_evaluate_parser_requires_checkpoint_and_defaults_to_100_episodes() -> None:
    parser = cli._parser()
    arguments = parser.parse_args(["evaluate", "--checkpoint", "best.zip"])

    assert arguments.checkpoint == Path("best.zip")
    assert arguments.episodes == 100
    assert arguments.output is None

    with pytest.raises(SystemExit) as missing:
        parser.parse_args(["evaluate"])
    assert missing.value.code == 2


@pytest.mark.parametrize(
    "extra_arguments",
    (["--episodes", "0"], ["--episodes", "-1"], ["--policy", "noop"], ["--suite", "test"]),
)
def test_evaluate_parser_rejects_invalid_or_removed_arguments(
    extra_arguments: list[str],
) -> None:
    parser = cli._parser()
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["evaluate", "--checkpoint", "best.zip", *extra_arguments])
    assert raised.value.code == 2


def test_default_evaluation_output_routes_by_checkpoint_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = RunDirectory.create(tmp_path / "runs", {"trainer": {}})
    run.best_checkpoint.write_bytes(b"checkpoint")
    assert cli._default_evaluation_output(run.best_checkpoint, 7) == (
        run.metrics / "performance-best-7-episodes.json"
    )

    output_root = tmp_path / "external-reports"
    monkeypatch.setenv("JUMP_TRAINER_OUTPUT_ROOT", str(output_root))
    external = tmp_path / "frozen.zip"
    external.write_bytes(b"checkpoint")
    assert cli._default_evaluation_output(external, 12) == (
        output_root / "performance-frozen-12-episodes.json"
    )


def test_zero_success_evaluation_writes_report_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkpoint = tmp_path / "candidate.zip"
    checkpoint.write_bytes(b"checkpoint")
    output = tmp_path / "custom-report.json"
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=80_000)

    class FixedModel:
        def predict(
            self, _observation: np.ndarray, *, deterministic: bool
        ) -> tuple[np.ndarray, None]:
            assert deterministic
            return np.asarray(NOOP), None

    class FakeDQN:
        @staticmethod
        def load(path: Path, *, device: str) -> FixedModel:
            assert path == checkpoint.resolve()
            assert device == "cpu"
            return FixedModel()

    monkeypatch.setattr(cli, "DQN", FakeDQN)
    pool = ClientPool(
        (Endpoint(0, "127.0.0.1", 64_123),),
        startup_timeout=1,
        message_timeout=1,
        reset_retries=1,
        environment_factory=lambda _endpoint: env,
    )
    monkeypatch.setattr(cli, "_pool", lambda _arguments, _endpoints: pool)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "jump-trainer",
            "evaluate",
            "--checkpoint",
            str(checkpoint),
            "--episodes",
            "3",
            "--output",
            str(output),
        ],
    )

    cli.main()

    assert connection.reset_seeds == [200_000, 200_001, 200_002]
    persisted = json.loads(output.read_text())
    assert persisted["checkpoint"] == str(checkpoint.resolve())
    assert persisted["seed_range"] == {"start": 200_000, "end": 200_002}
    evaluation = persisted["evaluation"]
    assert evaluation["episode_count"] == 3
    assert evaluation["success_count"] == 0
    assert evaluation["success_rate"] == 0.0
    assert evaluation["terminal_reason_counts"] == {"TERMINAL_REASON_MISSED_JUMP": 3}
    assert [episode["seed"] for episode in evaluation["episodes"]] == [
        200_000,
        200_001,
        200_002,
    ]
    summary = capsys.readouterr().out
    assert f"checkpoint: {checkpoint.resolve()}" in summary
    assert "episodes/seeds: 3; 200000..200002 inclusive" in summary
    assert "successes: 0/3 (0.00%)" in summary
    assert "TERMINAL_REASON_MISSED_JUMP=3" in summary
    assert "mean completion ticks=n/a, mean jump requests=n/a" in summary
    assert "client mean=1.00, max=1; server mean=1.00, max=1 ticks/action" in summary
    assert f"report: {output.resolve()}" in summary


def test_capture_command_is_removed() -> None:
    parser = cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["capture"])


def test_pipeline_requires_unique_run_id() -> None:
    parser = cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["pipeline", "--timesteps", "2000"])
    parsed = parser.parse_args(["pipeline", "--run-id", "smoke-001", "--timesteps", "2000"])
    assert parsed.run_id == "smoke-001"
    assert parsed.evaluation_episodes == 100


def test_keyboard_interrupt_exits_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt(_arguments: object) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["jump-trainer", "train", "--timesteps", "10"])
    monkeypatch.setattr(cli, "_train", interrupt)
    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 130
    output = capsys.readouterr().err
    assert output == "jump-trainer: interrupted by user\n"
    assert "Traceback" not in output
