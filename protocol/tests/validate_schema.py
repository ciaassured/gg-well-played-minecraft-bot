#!/usr/bin/env python3
"""Small semantic guardrails beyond protoc and Buf's structural checks."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "proto" / "jump" / "v1" / "jump.proto"


def message_body(source: str, name: str) -> str:
    match = re.search(rf"message\s+{re.escape(name)}\s*\{{(.*?)\n\}}", source, re.S)
    if match is None:
        raise AssertionError(f"missing message {name}")
    return match.group(1)


def fields(body: str) -> set[str]:
    return set(
        re.findall(
            r"^\s*(?:[.\w]+)\s+([a-z][a-z0-9_]*)\s*=\s*\d+\s*;",
            body,
            re.M,
        )
    )


def require_fields(source: str, name: str, required: set[str]) -> None:
    missing = required - fields(message_body(source, name))
    assert not missing, f"{name} is missing: {sorted(missing)}"


def main() -> None:
    source = SCHEMA.read_text(encoding="utf-8")
    assert 'package jump.v1;' in source
    assert "payloads larger than 1 MiB" in source
    assert "protocol v2" in source.lower()

    require_fields(
        source,
        "Observation",
        {
            "protocol_version",
            "session_id",
            "episode_id",
            "client_tick",
            "server_tick",
            "observation_sequence",
            "action_sequence",
            "signed_wall_distance",
            "relative_feet_height",
            "vertical_velocity",
            "lane_velocity",
            "on_ground",
            "elapsed_ticks",
            "phase",
            "terminal_reason",
        },
    )
    require_fields(
        source,
        "ActionRequest",
        {
            "protocol_version",
            "session_id",
            "episode_id",
            "client_tick",
            "server_tick",
            "observation_sequence",
            "action_sequence",
            "action",
        },
    )
    require_fields(
        source,
        "ResetRequest",
        {"protocol_version", "request_id", "session_id", "episode_id", "seed"},
    )
    require_fields(
        source,
        "Shutdown",
        {"protocol_version", "request_id", "session_id", "episode_id", "reason"},
    )
    require_fields(
        source,
        "CommandFinalize",
        {
            "protocol_version",
            "request_id",
            "session_id",
            "active_episode_id",
            "interrupted",
            "transfer_timeout_seconds",
        },
    )
    require_fields(
        source,
        "EpisodeArtifact",
        {
            "protocol_version",
            "request_id",
            "session_id",
            "ordinal",
            "episode_id",
            "seed",
            "recording_status",
            "terminal_reason",
            "staging_path",
            "size_bytes",
            "sha256",
        },
    )
    require_fields(
        source,
        "RetentionAcknowledgement",
        {
            "protocol_version",
            "request_id",
            "session_id",
            "ordinal",
            "episode_id",
            "sha256",
            "retained",
            "detail",
        },
    )

    observation = fields(message_body(source, "Observation"))
    forbidden = {"image", "yaw", "pitch", "inventory", "nearby_blocks", "yrush"}
    assert observation.isdisjoint(forbidden), "observation leaked a forbidden feature"

    wire = message_body(source, "WireMessage")
    for payload in {
        "connection_hello",
        "reset_request",
        "episode_ready",
        "episode_result",
        "action_request",
        "observation",
        "error",
        "shutdown",
        "command_finalize",
        "episode_artifact",
        "retention_acknowledgement",
        "batch_complete",
    }:
        assert re.search(rf"\b{payload}\s*=", wire), f"WireMessage lacks {payload}"

    assert "ClientMode" not in source
    assert "CaptureRequest" not in source
    assert "CaptureReady" not in source
    assert "CaptureComplete" not in source
    assert re.search(r'reserved\s+21\s+to\s+23\s*;', wire)
    assert 'reserved "capture_request", "capture_ready", "capture_complete";' in wire
    assert "mode" not in fields(message_body(source, "ConnectionHello"))
    assert "mode" not in fields(message_body(source, "ConnectionReady"))


if __name__ == "__main__":
    main()
