"""Deterministic normalization for the flat 513-feature policy input."""

from __future__ import annotations

from dataclasses import dataclass

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray

from yrush_trainer.config import OBSERVATION_FEATURES, VOXEL_FEATURES
from yrush_trainer.messages import RawObservation

TARGET_HEIGHT_SCALE = 64.0
HORIZONTAL_VELOCITY_SCALE = 1.0
VERTICAL_VELOCITY_SCALE = 1.0
YAW_SCALE = 45.0
PITCH_SCALE = 90.0

CONTINUOUS_FEATURES = (
    "signed_target_height_difference",
    "forward_velocity",
    "strafe_velocity",
    "vertical_velocity",
    "fractional_x",
    "fractional_y",
    "fractional_z",
    "grounded",
    "remaining_time_fraction",
    "yaw_residual",
    "pitch",
    "health_fraction",
    "air_fraction",
)

_LOW = np.zeros(OBSERVATION_FEATURES, dtype=np.float32)
_HIGH = np.ones(OBSERVATION_FEATURES, dtype=np.float32)
for _index in (
    VOXEL_FEATURES,
    VOXEL_FEATURES + 1,
    VOXEL_FEATURES + 2,
    VOXEL_FEATURES + 3,
    VOXEL_FEATURES + 9,
    VOXEL_FEATURES + 10,
):
    _LOW[_index] = -1.0

OBSERVATION_SPACE = gym.spaces.Box(low=_LOW, high=_HIGH, dtype=np.float32)


@dataclass(frozen=True)
class NormalizedObservation:
    values: NDArray[np.float32]
    clipped_features: int


def normalize_observation_with_stats(observation: RawObservation) -> NormalizedObservation:
    observation.validate()
    raw_scaled = (
        observation.signed_target_height_difference / TARGET_HEIGHT_SCALE,
        observation.forward_velocity / HORIZONTAL_VELOCITY_SCALE,
        observation.strafe_velocity / HORIZONTAL_VELOCITY_SCALE,
        observation.vertical_velocity / VERTICAL_VELOCITY_SCALE,
        observation.fractional_x,
        observation.fractional_y,
        observation.fractional_z,
        1.0 if observation.grounded else 0.0,
        observation.remaining_time_fraction,
        observation.yaw_residual_degrees / YAW_SCALE,
        observation.pitch_degrees / PITCH_SCALE,
        observation.health_fraction,
        observation.air_fraction,
    )
    continuous = np.asarray(raw_scaled, dtype=np.float32)
    low = _LOW[VOXEL_FEATURES:]
    high = _HIGH[VOXEL_FEATURES:]
    clipped = np.clip(continuous, low, high)
    values = np.concatenate(
        (np.frombuffer(observation.block_properties, dtype=np.uint8).astype(np.float32), clipped)
    ).astype(np.float32, copy=False)
    return NormalizedObservation(
        values=values,
        clipped_features=int(np.count_nonzero(continuous != clipped)),
    )


def normalize_observation(observation: RawObservation) -> NDArray[np.float32]:
    return normalize_observation_with_stats(observation).values


def normalization_metadata() -> dict[str, object]:
    return {
        "observation_features": OBSERVATION_FEATURES,
        "voxel_features": VOXEL_FEATURES,
        "continuous_features": list(CONTINUOUS_FEATURES),
        "target_height_scale": TARGET_HEIGHT_SCALE,
        "horizontal_velocity_scale": HORIZONTAL_VELOCITY_SCALE,
        "vertical_velocity_scale": VERTICAL_VELOCITY_SCALE,
        "yaw_scale": YAW_SCALE,
        "pitch_scale": PITCH_SCALE,
    }
