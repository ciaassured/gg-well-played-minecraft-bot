from __future__ import annotations

import json
from pathlib import Path

from jump_trainer.run_directory import RunDirectory, find_run_for_checkpoint


def test_run_layout_json_and_checkpoint_promotion(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, {"trainer": {"timesteps": 10}})
    assert run.root.is_dir()
    assert run.replays.is_dir()
    assert run.videos.is_dir()
    assert json.loads((run.root / "config.json").read_text())["trainer"]["timesteps"] == 10

    candidate = run.candidate_checkpoint(5)
    candidate.write_bytes(b"checkpoint")
    retained = run.promote(candidate, 5)
    assert retained.read_bytes() == b"checkpoint"
    assert run.best_checkpoint.read_bytes() == b"checkpoint"
    assert find_run_for_checkpoint(retained) == run
