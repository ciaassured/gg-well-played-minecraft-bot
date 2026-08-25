"""Pinned seed partitions and trainer configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TRAIN_SEED_MIN = 0
TRAIN_SEED_MAX = 99_999
VALIDATION_SEEDS = tuple(range(100_000, 100_100))
TEST_SEEDS = tuple(range(200_000, 200_100))
SHOWCASE_SEED = 100_000
PROTOCOL_VERSION = 2
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 64_123


@dataclass(frozen=True)
class TrainConfig:
    """Serializable DQN and validation settings for one training run."""

    total_timesteps: int = 30_000
    validation_interval: int = 5_000
    random_seed: int = 20_260_823
    learning_rate: float = 0.001
    buffer_size: int = 50_000
    learning_starts: int = 500
    batch_size: int = 128
    gamma: float = 0.99
    train_frequency: int = 4
    gradient_steps: int = 1
    target_update_interval: int = 1_000
    exploration_fraction: float = 0.35
    exploration_initial_epsilon: float = 1.0
    exploration_final_epsilon: float = 0.02
    policy_width: int = 64
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    message_timeout_seconds: float = 5.0
    recording_timeout_seconds: float = 300.0
    reset_retries: int = 3

    def validate(self) -> None:
        if self.total_timesteps <= 0:
            raise ValueError("total_timesteps must be positive")
        if self.validation_interval <= 0:
            raise ValueError("validation_interval must be positive")
        if self.batch_size <= 0 or self.buffer_size < self.batch_size:
            raise ValueError("buffer_size must be at least batch_size")
        if not 0.0 <= self.exploration_final_epsilon <= self.exploration_initial_epsilon <= 1.0:
            raise ValueError("invalid exploration epsilon range")
        if self.port <= 0 or self.port > 65_535:
            raise ValueError("port must be in 1..65535")
        if self.message_timeout_seconds <= 0:
            raise ValueError("message timeout must be positive")
        if self.recording_timeout_seconds <= 0:
            raise ValueError("recording timeout must be positive")
        if self.reset_retries < 1:
            raise ValueError("reset_retries must be positive")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def seeds_for_suite(name: str) -> tuple[int, ...]:
    if name == "validation":
        return VALIDATION_SEEDS
    if name == "test":
        return TEST_SEEDS
    raise ValueError(f"unknown evaluation suite: {name}")
