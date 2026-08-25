from __future__ import annotations

import sys

import pytest

import jump_trainer.cli as cli


def test_only_failed_final_acceptance_requests_a_nonzero_exit() -> None:
    assert cli.FINAL_ACCEPTANCE_FAILURE_EXIT_CODE == 3
    assert not cli._final_acceptance_failed({})
    assert not cli._final_acceptance_failed({"acceptance": {"passed": True}})
    assert cli._final_acceptance_failed({"acceptance": {"passed": False}})


def test_capture_command_is_removed() -> None:
    parser = cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["capture"])


def test_keyboard_interrupt_exits_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt(_arguments: object) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["jump-trainer", "train"])
    monkeypatch.setattr(cli, "_train", interrupt)
    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 130
    output = capsys.readouterr().err
    assert output == "jump-trainer: interrupted by user\n"
    assert "Traceback" not in output
