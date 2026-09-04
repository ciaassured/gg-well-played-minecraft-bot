#!/usr/bin/env python3
"""Semantic checks for the intentionally incompatible YRush v1 contract."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "proto" / "yrush" / "v1" / "yrush.proto"


def message_body(source: str, name: str) -> str:
    match = re.search(rf"message\s+{re.escape(name)}\s*\{{(.*?)\n\}}", source, re.S)
    if match is None:
        raise AssertionError(f"missing message {name}")
    return match.group(1)


def fields(body: str) -> set[str]:
    return set(
        re.findall(
            r"^\s*(?:(?:repeated\s+)?[.\w]+)\s+([a-z][a-z0-9_]*)\s*=\s*\d+",
            body,
            re.M,
        )
    )


def require_fields(source: str, name: str, required: set[str]) -> None:
    missing = required - fields(message_body(source, name))
    assert not missing, f"{name} is missing: {sorted(missing)}"


def main() -> None:
    source = SCHEMA.read_text(encoding="utf-8")
    assert "package yrush.v1;" in source
    assert 'java_package = "gg.wellplayed.yrush.protocol.v1";' in source
    assert "intentionally incompatible" in source
    assert "payloads larger than 1 MiB" in source
    assert "yrush:bot_state" in source

    wire = message_body(source, "WireMessage")
    expected_payloads = {
        "connection_hello",
        "connection_ready",
        "arm_episode",
        "episode_ready",
        "action_request",
        "action_applied",
        "observation",
        "episode_result",
        "error",
        "shutdown",
    }
    assert expected_payloads <= fields(wire)

    require_fields(
        source,
        "ActionRequest",
        {
            "protocol_version",
            "session_id",
            "round_sequence",
            "policy_version",
            "observation_sequence",
            "action_sequence",
            "action",
        },
    )
    require_fields(
        source,
        "Observation",
        {
            "protocol_version",
            "session_id",
            "round_sequence",
            "policy_version",
            "client_tick",
            "observation_sequence",
            "action_sequence",
            "phase",
            "block_properties",
            "signed_target_height_difference",
            "forward_velocity",
            "strafe_velocity",
            "vertical_velocity",
            "fractional_x",
            "fractional_y",
            "fractional_z",
            "grounded",
            "remaining_time_fraction",
            "yaw_residual_degrees",
            "pitch_degrees",
            "health_fraction",
            "air_fraction",
        },
    )
    require_fields(
        source,
        "EpisodeResult",
        {
            "round_sequence",
            "policy_version",
            "outcome",
            "winner_uuid",
            "participant_count",
            "completion_time_seconds",
            "best_remaining_target_distance",
        },
    )

    forbidden = {
        "reset_seed",
        "seed",
        "server_tick",
        "arena",
        "lane_direction_x",
        "wall_near_coordinate",
        "absolute_x",
        "inventory",
        "pixels",
        "server_group",
    }
    all_fields: set[str] = set()
    for body in re.findall(r"message\s+\w+\s*\{(.*?)\n\}", source, re.S):
        all_fields.update(fields(body))
    assert all_fields.isdisjoint(forbidden), sorted(all_fields & forbidden)
    assert "ResetRequest" not in source


if __name__ == "__main__":
    main()
