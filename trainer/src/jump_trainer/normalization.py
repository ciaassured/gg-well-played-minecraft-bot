"""Deterministic policy feature normalization."""

from __future__ import annotations

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray

from jump_trainer.messages import RawObservation

POLICY_FEATURES = (
    "signed_wall_distance",
    "relative_feet_height",
    "vertical_velocity",
    "lane_velocity",
    "on_ground",
    "elapsed_ticks",
)

OBSERVATION_SPACE = gym.spaces.Box(low=-1.0, high=1.0, shape=(6,), dtype=np.float32)


def _bounded(value: float, scale: float) -> float:
    return float(np.clip(value / scale, -1.0, 1.0))


def normalize_observation(observation: RawObservation) -> NDArray[np.float32]:
    """Map the six physical policy inputs to a stable [-1, 1] vector."""

    observation.validate()
    return np.asarray(
        [
            _bounded(observation.signed_wall_distance, 8.0),
            _bounded(observation.relative_feet_height, 1.25),
            _bounded(observation.vertical_velocity, 0.6),
            _bounded(observation.lane_velocity, 0.3),
            1.0 if observation.on_ground else -1.0,
            float(np.clip((observation.elapsed_ticks / 100.0) - 1.0, -1.0, 1.0)),
        ],
        dtype=np.float32,
    )
