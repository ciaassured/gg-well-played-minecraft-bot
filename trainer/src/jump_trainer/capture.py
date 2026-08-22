"""Deterministic checkpoint demonstrations coordinated with Replay Mod."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jump.v1 import jump_pb2 as pb
from stable_baselines3 import DQN

from jump_trainer.config import SHOWCASE_SEED
from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.evaluation import evaluate_policy, model_policy
from jump_trainer.run_directory import RunDirectory, atomic_write_json
from jump_trainer.wire import BenchmarkConnection, CaptureArtifact


@dataclass(frozen=True)
class RetainedCheckpoint:
    checkpoint_id: str
    path: Path
    promotion_step: int | None


def retained_checkpoints(run: RunDirectory) -> tuple[RetainedCheckpoint, ...]:
    """Return the untrained checkpoint followed by promotions in history order."""

    if not run.untrained_checkpoint.is_file():
        raise FileNotFoundError(f"untrained checkpoint does not exist: {run.untrained_checkpoint}")
    history_path = run.root / "promotion-history.json"
    if not history_path.is_file():
        raise FileNotFoundError(f"promotion history does not exist: {history_path}")
    try:
        raw_history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exception:
        raise ValueError(f"cannot read promotion history: {history_path}") from exception
    if not isinstance(raw_history, list) or not raw_history:
        raise ValueError("promotion history must contain at least one checkpoint")

    retained = [RetainedCheckpoint("untrained", run.untrained_checkpoint, None)]
    seen_steps: set[int] = set()
    for raw_entry in raw_history:
        if not isinstance(raw_entry, dict):
            raise ValueError("promotion history entries must be objects")
        step = raw_entry.get("step")
        relative = raw_entry.get("checkpoint")
        if not isinstance(step, int) or step < 0 or step in seen_steps:
            raise ValueError("promotion history contains an invalid or duplicate step")
        if not isinstance(relative, str) or not relative:
            raise ValueError("promotion history contains an invalid checkpoint path")
        checkpoint = (run.root / relative).resolve()
        if not checkpoint.is_relative_to(run.root) or not checkpoint.is_file():
            raise FileNotFoundError(f"retained checkpoint does not exist: {checkpoint}")
        seen_steps.add(step)
        retained.append(
            RetainedCheckpoint(f"promotion-step-{step:08d}", checkpoint, promotion_step=step)
        )
    return tuple(retained)


def validate_mcpr(path: Path) -> None:
    """Require the core Replay Mod ZIP members and readable JSON metadata."""

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


def retain_artifact(
    artifact: CaptureArtifact,
    capture_directory: Path,
    ordinal: int,
) -> Path:
    source = artifact.replay_file.resolve()
    if source.suffix.lower() != ".mcpr" or not source.is_file():
        raise InfrastructureError(f"recording client reported a missing .mcpr file: {source}")
    if source.stat().st_size != artifact.size_bytes:
        raise InfrastructureError(f"recording client reported the wrong replay size: {source}")
    if sha256_file(source) != artifact.sha256:
        raise InfrastructureError(f"recording client reported the wrong replay digest: {source}")
    validate_mcpr(source)

    capture_directory.mkdir(parents=True, exist_ok=True)
    destination = capture_directory / f"{ordinal:03d}-{artifact.checkpoint_id}.mcpr"
    temporary = destination.with_suffix(".mcpr.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)
    if sha256_file(destination) != artifact.sha256:
        raise InfrastructureError(f"retained replay digest changed while copying: {destination}")
    validate_mcpr(destination)
    return destination


def _new_capture_directory(run: RunDirectory) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = run.replays / f"capture-{timestamp}"
    suffix = 1
    while directory.exists():
        directory = run.replays / f"capture-{timestamp}-{suffix}"
        suffix += 1
    directory.mkdir(parents=True)
    return directory


def capture_retained_checkpoints(
    run: RunDirectory,
    host: str,
    port: int,
    timeout: float,
    reset_retries: int,
) -> dict[str, Any]:
    subjects = retained_checkpoints(run)
    capture_directory = _new_capture_directory(run)
    manifest_path = capture_directory / "manifest.json"
    manifest: dict[str, Any] = {
        "status": "running",
        "showcase_seed": SHOWCASE_SEED,
        "capture_directory": str(capture_directory.relative_to(run.root)),
        "captures": [],
    }
    atomic_write_json(manifest_path, manifest)
    connection: BenchmarkConnection | None = None
    identifier = time.time_ns()
    if identifier <= 0 or identifier >= 2**64 - len(subjects) * 4 - 1:
        raise RuntimeError("cannot allocate capture identifiers")

    try:
        active_connection = BenchmarkConnection.connect(
            host,
            port,
            timeout,
            expected_mode=pb.CLIENT_MODE_RECORDING,
        )
        connection = active_connection

        def reuse_connection() -> BenchmarkConnection:
            return active_connection

        captures = manifest["captures"]
        if not isinstance(captures, list):
            raise AssertionError("capture manifest list was replaced")
        for ordinal, subject in enumerate(subjects):
            capture_request_id = identifier
            reset_identifier_base = identifier + 1
            episode_id = identifier + 2
            shutdown_request_id = identifier + 3
            identifier += 4
            reconnect = ordinal + 1 < len(subjects)

            model = DQN.load(subject.path, device="cpu")
            connection.begin_capture(
                capture_request_id,
                subject.checkpoint_id,
                episode_id,
                SHOWCASE_SEED,
            )
            env = MinecraftJumpEnv(
                reset_retries=reset_retries,
                connection_factory=reuse_connection,
                identifier_base=reset_identifier_base,
            )
            report = evaluate_policy(
                env,
                model_policy(model),
                (SHOWCASE_SEED,),
                subject.checkpoint_id,
                "capture",
            )
            artifact = connection.finish_capture(
                shutdown_request_id,
                subject.checkpoint_id,
                episode_id,
                reconnect,
            )
            retained = retain_artifact(artifact, capture_directory, ordinal)
            metadata = {
                "checkpoint_id": subject.checkpoint_id,
                "checkpoint": str(subject.path.relative_to(run.root)),
                "promotion_step": subject.promotion_step,
                "showcase_seed": SHOWCASE_SEED,
                "evaluation": report.as_dict(),
                "source_replay": str(artifact.replay_file),
                "replay": str(retained.relative_to(run.root)),
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256.hex(),
            }
            captures.append(metadata)
            atomic_write_json(retained.with_suffix(".json"), metadata)
            atomic_write_json(manifest_path, manifest)

        manifest["status"] = "complete"
        atomic_write_json(manifest_path, manifest)
        run.write_json(
            "replays/latest-capture.json",
            {
                "status": "complete",
                "manifest": str(manifest_path.relative_to(run.root)),
                "capture_count": len(subjects),
                "showcase_seed": SHOWCASE_SEED,
            },
        )
        return manifest
    except Exception as exception:
        manifest["status"] = "failed"
        manifest["error"] = str(exception)
        atomic_write_json(manifest_path, manifest)
        raise
    finally:
        if connection is not None:
            connection.close()
