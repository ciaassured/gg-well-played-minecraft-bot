"""Canonical retention for the episode recordings produced by one trainer command."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from jump.v1 import jump_pb2 as pb

from jump_trainer.console import emit
from jump_trainer.errors import InfrastructureError
from jump_trainer.run_directory import RunDirectory, atomic_write_json
from jump_trainer.wire import BenchmarkConnection, RecordingArtifact, RecordingBatch

DEFAULT_RECORDING_ROOT = Path("trainer/recordings")
DEFAULT_RECORDING_TIMEOUT_SECONDS = 300.0


def validate_mcpr(path: Path) -> None:
    """Require a readable Replay Mod archive with its core members."""

    try:
        with zipfile.ZipFile(path) as replay:
            names = set(replay.namelist())
            if not {"metaData.json", "recording.tmcpr"}.issubset(names):
                raise InfrastructureError(f"Replay Mod file lacks required members: {path}")
            metadata = json.loads(replay.read("metaData.json"))
            if not isinstance(metadata, dict):
                raise InfrastructureError(f"Replay Mod metadata is not an object: {path}")
            if replay.getinfo("recording.tmcpr").file_size <= 0:
                raise InfrastructureError(f"Replay Mod packet stream is empty: {path}")
            corrupt_member = replay.testzip()
            if corrupt_member is not None:
                raise InfrastructureError(
                    f"Replay Mod archive has a corrupt member {corrupt_member}: {path}"
                )
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exception:
        raise InfrastructureError(f"invalid Replay Mod file {path}: {exception}") from exception


def sha256_file(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _unique_directory(parent: Path, name: str) -> Path:
    candidate = parent / name
    suffix = 1
    while candidate.exists():
        candidate = parent / f"{name}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate.resolve()


def recording_directory(
    command: str,
    *,
    run: RunDirectory | None = None,
    root: Path | None = None,
) -> Path:
    timestamp = _timestamp()
    if command == "train":
        if run is None:
            raise ValueError("training recordings require their owning run")
        return _unique_directory(run.replays, f"train-{timestamp}")
    if command not in {"smoke", "evaluate", "run"}:
        raise ValueError(f"unsupported recording command: {command}")
    recording_root = root or Path(
        os.environ.get("JUMP_TRAINER_RECORDING_ROOT", str(DEFAULT_RECORDING_ROOT))
    )
    return _unique_directory(recording_root / command, timestamp)


class RecordingSession:
    """One trainer command's connection, episode context, manifest, and retention flow."""

    def __init__(
        self,
        command: str,
        directory: Path,
        *,
        host: str,
        port: int,
        message_timeout: float,
        recording_timeout: float = DEFAULT_RECORDING_TIMEOUT_SECONDS,
        context: dict[str, Any] | None = None,
    ) -> None:
        if recording_timeout <= 0:
            raise ValueError("recording timeout must be positive")
        self.command = command
        self.directory = directory.resolve()
        self.manifest_path = self.directory / "manifest.json"
        self.host = host
        self.port = port
        self.message_timeout = message_timeout
        self.recording_timeout = recording_timeout
        self._connection: BenchmarkConnection | None = None
        self._next_identifier = time.time_ns()
        if self._next_identifier <= 0 or self._next_identifier >= 2**64 - 1_000_000:
            raise RuntimeError("cannot allocate recording lifecycle identifiers")
        self._episode_context: dict[str, Any] = {}
        self._active_episode_id: int | None = None
        self._episodes_by_id: dict[int, dict[str, Any]] = {}
        self._manifest: dict[str, Any] = {
            "schema_version": 1,
            "status": "running",
            "command": command,
            "started_at": datetime.now(UTC).isoformat(),
            "recording_directory": str(self.directory),
            "context": dict(context or {}),
            "episodes": [],
            "warnings": [],
            "batch": None,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        self._persist()

    def __enter__(self) -> RecordingSession:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del traceback
        interrupted = exception_type is not None and issubclass(exception_type, KeyboardInterrupt)
        command_status = "interrupted" if interrupted else "failed" if exception else "complete"
        self.finish(interrupted=interrupted, command_status=command_status)
        return False

    def connection(self) -> BenchmarkConnection:
        if self._connection is None:
            self._connection = BenchmarkConnection.connect(
                self.host,
                self.port,
                self.message_timeout,
            )
        return self._connection

    def set_episode_context(self, **context: Any) -> None:
        self._episode_context = {key: value for key, value in context.items() if value is not None}

    def episode_started(self, episode_id: int, seed: int) -> None:
        if self._active_episode_id is not None:
            self._mark_partial(self._active_episode_id, "overlapped_by_reset")
        ordinal = len(self._episodes_by_id)
        episode: dict[str, Any] = {
            "ordinal": ordinal,
            "episode_id": episode_id,
            "seed": seed,
            "recording_status": "active",
            "terminal_reason": "TERMINAL_REASON_UNSPECIFIED",
            "outcome": None,
            "policy": dict(self._episode_context),
            "retained": False,
            "staging_path": None,
            "canonical_path": None,
            "size_bytes": None,
            "sha256": None,
            "retention_error": None,
        }
        self._episodes_by_id[episode_id] = episode
        episodes = self._manifest["episodes"]
        if not isinstance(episodes, list):
            raise AssertionError("recording manifest episode list was replaced")
        episodes.append(episode)
        self._active_episode_id = episode_id
        self._persist()

    def episode_finished(self, episode_id: int, info: dict[str, Any]) -> None:
        episode = self._episodes_by_id.get(episode_id)
        if episode is None:
            self._warn(f"trainer observed terminal metadata for unknown episode {episode_id}")
            return
        episode["recording_status"] = "complete"
        episode["terminal_reason"] = str(info["terminal_reason"])
        episode["outcome"] = {
            "success": bool(info["success"]),
            "elapsed_ticks": int(info["elapsed_ticks"]),
            "jump_requests": int(info["jump_requests"]),
            "return": float(info["episode_return"]),
        }
        if self._active_episode_id == episode_id:
            self._active_episode_id = None
        self._persist()

    def finish(self, *, interrupted: bool, command_status: str) -> None:
        if self._manifest["status"] != "running":
            return
        if self._active_episode_id is not None:
            reason = "command_interrupted" if interrupted else "command_ended"
            self._mark_partial(self._active_episode_id, reason)
        batch: RecordingBatch | None = None
        try:
            if self._connection is not None:
                batch = self._connection.finalize_recordings(
                    self._allocate_identifier(),
                    interrupted=interrupted,
                    timeout=self.recording_timeout,
                    artifact_handler=self._retain_artifact,
                )
        except Exception as exception:
            self._warn(f"recording finalization failed: {exception}")
        finally:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

        if batch is not None:
            self._manifest["batch"] = {
                "request_id": batch.request_id,
                "session_id": batch.session_id,
                "expected_artifacts": batch.expected_artifacts,
                "offered_artifacts": batch.offered_artifacts,
                "retained_artifacts": batch.retained_artifacts,
                "preserved_artifacts": batch.preserved_artifacts,
                "reconnecting_minecraft": batch.reconnecting_minecraft,
            }
            for warning in batch.warnings:
                self._warn(warning)
        self._manifest["command_status"] = command_status
        self._manifest["finished_at"] = datetime.now(UTC).isoformat()
        warnings = self._manifest["warnings"]
        self._manifest["status"] = (
            "complete_with_warnings" if isinstance(warnings, list) and warnings else "complete"
        )
        self._persist()

    def _retain_artifact(self, artifact: RecordingArtifact) -> tuple[bool, str]:
        temporary: Path | None = None
        destination: Path | None = None
        published = False
        episode = self._episodes_by_id.get(artifact.episode_id)
        try:
            self._validate_artifact_metadata(artifact, episode)
            source = artifact.staging_path.resolve()
            if source.suffix.lower() != ".mcpr" or not source.is_file():
                raise InfrastructureError(
                    f"recording client reported a missing .mcpr file: {source}"
                )
            if source.stat().st_size != artifact.size_bytes:
                raise InfrastructureError(
                    f"recording client reported the wrong replay size: {source}"
                )
            if sha256_file(source) != artifact.sha256:
                raise InfrastructureError(
                    f"recording client reported the wrong replay digest: {source}"
                )
            validate_mcpr(source)

            destination = self.directory / f"{artifact.ordinal:03d}-seed-{artifact.seed}.mcpr"
            temporary = destination.with_suffix(".mcpr.tmp")
            if destination.exists():
                raise InfrastructureError(f"canonical replay already exists: {destination}")
            temporary.unlink(missing_ok=True)
            shutil.copy2(source, temporary)
            if temporary.stat().st_size != artifact.size_bytes:
                raise InfrastructureError(f"temporary replay copy has the wrong size: {temporary}")
            if sha256_file(temporary) != artifact.sha256:
                raise InfrastructureError(
                    f"temporary replay copy has the wrong digest: {temporary}"
                )
            validate_mcpr(temporary)
            os.replace(temporary, destination)
            published = True
            if destination.stat().st_size != artifact.size_bytes:
                raise InfrastructureError(f"published replay has the wrong size: {destination}")
            if sha256_file(destination) != artifact.sha256:
                raise InfrastructureError(f"published replay has the wrong digest: {destination}")
            validate_mcpr(destination)

            if episode is None:
                raise AssertionError("validated artifact lost its episode metadata")
            episode.update(
                {
                    "recording_status": _recording_status_name(artifact.recording_status),
                    "terminal_reason": pb.TerminalReason.Name(artifact.terminal_reason),
                    "retained": True,
                    "staging_path": str(source),
                    "canonical_path": str(destination.resolve()),
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256.hex(),
                    "retention_error": None,
                }
            )
            self._persist()
            return True, "retained and validated canonical replay"
        except Exception as exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if published and destination is not None:
                destination.unlink(missing_ok=True)
            detail = str(exception) or exception.__class__.__name__
            if episode is not None:
                episode.update(
                    {
                        "retained": False,
                        "staging_path": str(artifact.staging_path),
                        "canonical_path": None,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256.hex(),
                        "retention_error": detail,
                    }
                )
            self._warn(f"preserved staging replay for episode {artifact.episode_id}: {detail}")
            self._persist()
            return False, detail

    def _validate_artifact_metadata(
        self, artifact: RecordingArtifact, episode: dict[str, Any] | None
    ) -> None:
        if episode is None:
            raise InfrastructureError(
                f"recording client offered unknown episode {artifact.episode_id}"
            )
        if artifact.ordinal != episode["ordinal"] or artifact.seed != episode["seed"]:
            raise InfrastructureError("recording artifact identifiers do not match the episode")
        expected_status = str(episode["recording_status"])
        offered_status = _recording_status_name(artifact.recording_status)
        if expected_status not in {offered_status, "active"}:
            raise InfrastructureError("recording artifact completeness does not match the episode")
        expected_reason = str(episode["terminal_reason"])
        offered_reason = pb.TerminalReason.Name(artifact.terminal_reason)
        if expected_reason != offered_reason:
            raise InfrastructureError(
                "recording artifact terminal reason does not match the episode"
            )

    def _mark_partial(self, episode_id: int, cause: str) -> None:
        episode = self._episodes_by_id.get(episode_id)
        if episode is not None:
            episode["recording_status"] = "partial"
            episode["terminal_reason"] = "TERMINAL_REASON_INFRASTRUCTURE_ERROR"
            episode["outcome"] = {"partial_cause": cause}
        if self._active_episode_id == episode_id:
            self._active_episode_id = None
        self._persist()

    def _warn(self, message: str) -> None:
        warnings = self._manifest["warnings"]
        if not isinstance(warnings, list):
            raise AssertionError("recording manifest warning list was replaced")
        if message not in warnings:
            warnings.append(message)
            emit("recording", self.command, f"warning; {message}")

    def _persist(self) -> None:
        atomic_write_json(self.manifest_path, self._manifest)

    def _allocate_identifier(self) -> int:
        value = self._next_identifier
        self._next_identifier += 1
        return value


def _recording_status_name(status: int) -> str:
    if status == pb.EPISODE_RECORDING_STATUS_COMPLETE:
        return "complete"
    if status == pb.EPISODE_RECORDING_STATUS_PARTIAL:
        return "partial"
    raise InfrastructureError(f"unknown episode recording status: {status}")
