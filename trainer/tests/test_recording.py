from __future__ import annotations

import json
import zipfile
from pathlib import Path

from jump.v1 import jump_pb2 as pb

from jump_trainer.errors import InfrastructureError
from jump_trainer.recording import (
    RecordingSession,
    recording_directory,
    sha256_file,
    validate_mcpr,
)
from jump_trainer.run_directory import RunDirectory
from jump_trainer.wire import RecordingArtifact


def write_replay(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as replay:
        replay.writestr("metaData.json", json.dumps({"fileFormat": "MCPR"}))
        replay.writestr("recording.tmcpr", b"packets")


def new_session(tmp_path: Path) -> RecordingSession:
    return RecordingSession(
        "smoke",
        tmp_path / "recording",
        host="127.0.0.1",
        port=64123,
        message_timeout=5,
        recording_timeout=300,
        context={"test": True},
    )


def complete_episode(session: RecordingSession, episode_id: int, seed: int) -> None:
    session.set_episode_context(policy_id="policy", checkpoint="checkpoint.zip")
    session.episode_started(episode_id, seed)
    session.episode_finished(
        episode_id,
        {
            "terminal_reason": "TERMINAL_REASON_SUCCESS",
            "success": True,
            "elapsed_ticks": 19,
            "jump_requests": 1,
            "episode_return": 10.5,
        },
    )


def artifact(
    source: Path, digest: bytes, *, episode_id: int = 2, seed: int = 42
) -> RecordingArtifact:
    return RecordingArtifact(
        request_id=1,
        session_id="session",
        ordinal=0,
        episode_id=episode_id,
        seed=seed,
        recording_status=pb.EPISODE_RECORDING_STATUS_COMPLETE,
        terminal_reason=pb.TERMINAL_REASON_SUCCESS,
        staging_path=source,
        sha256=digest,
        size_bytes=source.stat().st_size,
    )


def test_artifact_is_atomically_validated_and_manifested(tmp_path: Path) -> None:
    source = tmp_path / "staging.mcpr"
    write_replay(source)
    session = new_session(tmp_path)
    complete_episode(session, 2, 42)

    retained, detail = session._retain_artifact(artifact(source, sha256_file(source)))

    assert retained
    assert "retained" in detail
    destination = session.directory / "000-seed-42.mcpr"
    assert destination.is_file()
    assert source.is_file(), "only the client removes staging after acknowledgement"
    assert sha256_file(destination) == sha256_file(source)
    validate_mcpr(destination)
    manifest = json.loads(session.manifest_path.read_text())
    episode = manifest["episodes"][0]
    assert episode["retained"] is True
    assert episode["policy"] == {
        "checkpoint": "checkpoint.zip",
        "policy_id": "policy",
    }
    assert episode["canonical_path"] == str(destination.resolve())
    assert episode["sha256"] == sha256_file(source).hex()


def test_failed_transfer_removes_temporary_destination_and_preserves_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staging.mcpr"
    write_replay(source)
    session = new_session(tmp_path)
    complete_episode(session, 2, 42)

    retained, detail = session._retain_artifact(artifact(source, b"x" * 32))

    assert not retained
    assert "digest" in detail
    assert source.is_file()
    assert not (session.directory / "000-seed-42.mcpr").exists()
    assert not (session.directory / "000-seed-42.mcpr.tmp").exists()
    manifest = json.loads(session.manifest_path.read_text())
    assert manifest["episodes"][0]["retention_error"]
    assert manifest["warnings"]


def test_manifest_failure_removes_published_destination_and_preserves_source(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "staging.mcpr"
    write_replay(source)
    session = new_session(tmp_path)
    complete_episode(session, 2, 42)
    original_persist = session._persist
    attempts = 0

    def fail_first_persist() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("manifest disk failure")
        original_persist()

    monkeypatch.setattr(session, "_persist", fail_first_persist)

    retained, detail = session._retain_artifact(artifact(source, sha256_file(source)))

    assert not retained
    assert "manifest disk failure" in detail
    assert source.is_file()
    assert not (session.directory / "000-seed-42.mcpr").exists()
    manifest = json.loads(session.manifest_path.read_text())
    episode = manifest["episodes"][0]
    assert episode["retained"] is False
    assert episode["canonical_path"] is None
    assert episode["retention_error"] == "manifest disk failure"


def test_interrupted_command_marks_active_episode_partial_without_failing(
    tmp_path: Path,
) -> None:
    session = new_session(tmp_path)
    session.episode_started(10, 99)

    session.finish(interrupted=True, command_status="interrupted")

    manifest = json.loads(session.manifest_path.read_text())
    assert manifest["command_status"] == "interrupted"
    assert manifest["episodes"][0]["recording_status"] == "partial"
    assert manifest["episodes"][0]["terminal_reason"] == "TERMINAL_REASON_INFRASTRUCTURE_ERROR"


def test_recording_finalization_failure_warns_without_changing_ml_success(
    tmp_path: Path, monkeypatch
) -> None:
    class FailingConnection:
        def finalize_recordings(self, *_args, **_kwargs):
            raise InfrastructureError("Replay Mod unavailable")

        def close(self) -> None:
            pass

    session = new_session(tmp_path)
    monkeypatch.setattr(session, "_connection", FailingConnection())

    session.finish(interrupted=False, command_status="complete")

    manifest = json.loads(session.manifest_path.read_text())
    assert manifest["command_status"] == "complete"
    assert manifest["status"] == "complete_with_warnings"
    assert manifest["warnings"] == ["recording finalization failed: Replay Mod unavailable"]


def test_recording_directories_follow_command_ownership(tmp_path: Path, monkeypatch) -> None:
    run = RunDirectory.create(tmp_path / "runs", {"trainer": {}})
    training = recording_directory("train", run=run)
    assert training.parent == run.replays
    assert training.name.startswith("train-")

    root = tmp_path / "recordings"
    monkeypatch.setenv("JUMP_TRAINER_RECORDING_ROOT", str(root))
    smoke = recording_directory("smoke")
    evaluate = recording_directory("evaluate")
    inference = recording_directory("run")
    assert smoke.parent == root / "smoke"
    assert evaluate.parent == root / "evaluate"
    assert inference.parent == root / "run"
