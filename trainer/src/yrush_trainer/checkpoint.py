"""Self-describing PPO archives with explicit legacy rejection."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from stable_baselines3 import PPO

from yrush_trainer.config import (
    ACTION_CARDINALITIES,
    OBSERVATION_FEATURES,
    PROTOCOL_VERSION,
    YRUSH_PACKET_SCHEMA_VERSION,
    TrainConfig,
)
from yrush_trainer.errors import CheckpointCompatibilityError
from yrush_trainer.normalization import normalization_metadata

METADATA_FILE = "yrush-metadata.json"
FORMAT_VERSION = 1


def checkpoint_metadata(
    config: TrainConfig,
    *,
    policy_version: int,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "algorithm": "PPO",
        "policy_version": policy_version,
        "protocol": {"namespace": "yrush.v1", "version": PROTOCOL_VERSION},
        "yrush_packet_schema": YRUSH_PACKET_SCHEMA_VERSION,
        "observation_space": {
            "type": "Box",
            "shape": [OBSERVATION_FEATURES],
            "dtype": "float32",
            "normalization": normalization_metadata(),
        },
        "action_space": {"type": "MultiDiscrete", "nvec": list(ACTION_CARDINALITIES)},
        "deployment": deployment,
        "expected_client_count": config.expected_client_count,
        "server_identity": config.server_identity,
        "world_seed": config.world_seed,
    }


def save_checkpoint(
    model: PPO,
    destination: Path,
    config: TrainConfig,
    *,
    policy_version: int,
    deployment: dict[str, Any],
) -> Path:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.zip")
    model.save(temporary)
    metadata = checkpoint_metadata(config, policy_version=policy_version, deployment=deployment)
    with zipfile.ZipFile(temporary, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(METADATA_FILE, json.dumps(metadata, sort_keys=True) + "\n")
    temporary.replace(destination)
    return destination


def read_checkpoint_metadata(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"checkpoint does not exist: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            if METADATA_FILE not in archive.namelist():
                data = archive.read("data") if "data" in archive.namelist() else b""
                if b"DQN" in data or b"replay_buffer" in data:
                    raise CheckpointCompatibilityError(
                        "legacy DQN checkpoints are intentionally incompatible with YRush PPO"
                    )
                raise CheckpointCompatibilityError(
                    "checkpoint has no YRush PPO compatibility metadata"
                )
            metadata = json.loads(archive.read(METADATA_FILE))
    except zipfile.BadZipFile as exception:
        raise CheckpointCompatibilityError("checkpoint is not an SB3 zip archive") from exception
    if not isinstance(metadata, dict):
        raise CheckpointCompatibilityError("checkpoint metadata is not an object")
    typed_metadata: dict[str, Any] = metadata
    action_space = typed_metadata.get("action_space")
    observation_space = typed_metadata.get("observation_space")
    deployment = typed_metadata.get("deployment")
    if (
        typed_metadata.get("format_version") != FORMAT_VERSION
        or typed_metadata.get("algorithm") != "PPO"
        or typed_metadata.get("protocol") != {"namespace": "yrush.v1", "version": PROTOCOL_VERSION}
        or typed_metadata.get("yrush_packet_schema") != YRUSH_PACKET_SCHEMA_VERSION
        or not isinstance(action_space, dict)
        or action_space != {"type": "MultiDiscrete", "nvec": list(ACTION_CARDINALITIES)}
        or not isinstance(observation_space, dict)
        or observation_space
        != {
            "type": "Box",
            "shape": [OBSERVATION_FEATURES],
            "dtype": "float32",
            "normalization": normalization_metadata(),
        }
        or not isinstance(deployment, dict)
        or not isinstance(typed_metadata.get("expected_client_count"), int)
        or int(typed_metadata["expected_client_count"]) <= 0
        or not isinstance(typed_metadata.get("server_identity"), str)
        or not typed_metadata["server_identity"]
        or not isinstance(typed_metadata.get("world_seed"), str)
        or not typed_metadata["world_seed"]
    ):
        raise CheckpointCompatibilityError("checkpoint metadata does not match YRush PPO v1")
    return typed_metadata


def load_checkpoint(
    path: Path, *, expected_client_count: int | None = None
) -> tuple[PPO, dict[str, Any]]:
    metadata = read_checkpoint_metadata(path)
    if (
        expected_client_count is not None
        and metadata.get("expected_client_count") != expected_client_count
    ):
        raise CheckpointCompatibilityError(
            "checkpoint expected-client count does not match this fixed pool"
        )
    model = PPO.load(path.resolve(), device="cpu")
    return model, metadata
