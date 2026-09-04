from pathlib import Path

import pytest

from yrush_trainer.run_directory import RunDirectory


def test_run_artifacts_are_unique_and_atomic(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, {"updates": 1}, "proof-1")
    assert (run.root / "config.json").is_file()
    assert run.checkpoints.is_dir()
    run.write_json("metrics/value.json", {"finite": 1.0})
    run.append_jsonl("metrics/events.jsonl", {"event": 1})
    assert not (run.metrics / "value.json.tmp").exists()
    with pytest.raises(FileExistsError):
        RunDirectory.create(tmp_path, {}, "proof-1")
    with pytest.raises(ValueError):
        RunDirectory.create(tmp_path, {}, "../unsafe")
