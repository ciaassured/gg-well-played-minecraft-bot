"""Serializable PPO, protocol, and client-pool configuration."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
YRUSH_PACKET_SCHEMA_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 64_123
ACTION_CARDINALITIES = (3, 3, 2, 2, 5, 5)
ACTION_HOLD_TICKS = 4
POLICY_RATE_HZ = 5
VOXEL_EDGE = 5
VOXEL_PROPERTIES = 4
VOXEL_FEATURES = VOXEL_EDGE**3 * VOXEL_PROPERTIES
OBSERVATION_FEATURES = VOXEL_FEATURES + 13


@dataclass(frozen=True)
class TrainConfig:
    """PPO defaults for one fixed-pool run."""

    updates: int
    random_seed: int = 20_260_904
    learning_rate: float = 3e-4
    gamma: float = 0.999
    gae_lambda: float = 0.98
    clip_range: float = 0.2
    target_kl: float = 0.03
    entropy_coefficient: float = 0.01
    value_coefficient: float = 0.5
    maximum_gradient_norm: float = 0.5
    rollout_length: int = 256
    batch_size: int = 256
    optimization_epochs: int = 10
    policy_width: int = 128
    evaluation_rounds: int = 0
    message_timeout_seconds: float = 10.0
    round_timeout_seconds: float = 600.0
    pool_startup_timeout_seconds: float = 900.0
    endpoints: tuple[str, ...] = ()
    expected_client_count: int = 1
    server_identity: str = "local"
    world_seed: str = "unknown"

    def validate(self) -> None:
        if self.updates <= 0:
            raise ValueError("updates must be positive")
        if self.rollout_length != 256:
            raise ValueError("rollout length is fixed at 256 transitions per client")
        if self.batch_size <= 1:
            raise ValueError("batch size must be greater than one")
        if self.optimization_epochs <= 0:
            raise ValueError("optimization epochs must be positive")
        if self.policy_width != 128:
            raise ValueError("policy and value network width is fixed at 128")
        if self.learning_rate <= 0.0:
            raise ValueError("learning rate must be positive")
        if not 0.0 < self.gamma <= 1.0:
            raise ValueError("gamma must be in (0, 1]")
        if not 0.0 <= self.gae_lambda <= 1.0:
            raise ValueError("GAE lambda must be in [0, 1]")
        if not 0.0 < self.clip_range < 1.0:
            raise ValueError("clip range must be in (0, 1)")
        if self.target_kl <= 0.0:
            raise ValueError("target KL must be positive")
        if self.entropy_coefficient < 0.0 or self.value_coefficient < 0.0:
            raise ValueError("loss coefficients must be nonnegative")
        if self.maximum_gradient_norm <= 0.0:
            raise ValueError("maximum gradient norm must be positive")
        if self.evaluation_rounds < 0:
            raise ValueError("evaluation rounds must be nonnegative")
        if (
            min(
                self.message_timeout_seconds,
                self.round_timeout_seconds,
                self.pool_startup_timeout_seconds,
            )
            <= 0.0
        ):
            raise ValueError("timeouts must be positive")
        if self.expected_client_count <= 0:
            raise ValueError("expected client count must be positive")
        if self.endpoints and len(self.endpoints) != self.expected_client_count:
            raise ValueError("endpoint count does not match expected client count")
        if len(set(self.endpoints)) != len(self.endpoints):
            raise ValueError("configured endpoints must be unique")
        if not self.server_identity:
            raise ValueError("server identity must not be blank")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_toml(cls, path: Path, **overrides: Any) -> TrainConfig:
        """Load the `[training]` table and apply explicit CLI overrides."""

        document = tomllib.loads(path.read_text(encoding="utf-8"))
        training = document.get("training")
        if not isinstance(training, dict):
            raise ValueError("trainer TOML must contain a [training] table")
        allowed = {field.name for field in fields(cls)}
        unknown = set(training) - allowed
        if unknown:
            raise ValueError(f"unknown training settings: {sorted(unknown)}")
        values = dict(training)
        values.update({key: value for key, value in overrides.items() if value is not None})
        if "endpoints" in values:
            values["endpoints"] = tuple(str(value) for value in values["endpoints"])
        config = cls(**values)
        config.validate()
        return config
