from __future__ import annotations

import sys

import pytest

import jump_trainer.cli as cli


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
