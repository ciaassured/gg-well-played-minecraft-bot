from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from jump_trainer.capture import (
    retain_artifact,
    retained_checkpoints,
    sha256_file,
    validate_mcpr,
)
from jump_trainer.errors import InfrastructureError
from jump_trainer.run_directory import RunDirectory
from jump_trainer.wire import CaptureArtifact


def write_replay(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as replay:
        replay.writestr("metaData.json", json.dumps({"fileFormat": "MCPR"}))
        replay.writestr("recording.tmcpr", b"packets")


def test_retained_checkpoints_follow_promotion_history(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, {"trainer": {}})
    run.untrained_checkpoint.write_bytes(b"untrained")
    promoted = run.promoted_checkpoint(5000)
    promoted.write_bytes(b"promoted")
    run.write_json(
        "promotion-history.json",
        [{"step": 5000, "checkpoint": str(promoted.relative_to(run.root))}],
    )

    checkpoints = retained_checkpoints(run)
    assert [checkpoint.checkpoint_id for checkpoint in checkpoints] == [
        "untrained",
        "promotion-step-00005000",
    ]
    assert checkpoints[1].path == promoted


def test_capture_artifact_is_validated_and_retained(tmp_path: Path) -> None:
    source = tmp_path / "source.mcpr"
    write_replay(source)
    digest = sha256_file(source)
    artifact = CaptureArtifact(
        request_id=1,
        session_id="session",
        checkpoint_id="untrained",
        episode_id=2,
        replay_file=source,
        sha256=digest,
        size_bytes=source.stat().st_size,
    )

    destination = retain_artifact(artifact, tmp_path / "retained", 0)
    assert destination.name == "000-untrained.mcpr"
    assert sha256_file(destination) == digest
    validate_mcpr(destination)

    invalid = replace(artifact, sha256=b"x" * 32)
    with pytest.raises(InfrastructureError, match="digest"):
        retain_artifact(invalid, tmp_path / "other", 0)
