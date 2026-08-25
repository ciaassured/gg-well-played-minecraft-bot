from __future__ import annotations

import pytest

from jump_trainer.config import (
    EVALUATION_SEED_START,
    SHOWCASE_SEED,
    TRAIN_SEED_MAX,
    TRAIN_SEED_MIN,
    VALIDATION_SEEDS,
    TrainConfig,
    evaluation_seeds,
)


def test_seed_partitions_are_exact_and_disjoint() -> None:
    assert (TRAIN_SEED_MIN, TRAIN_SEED_MAX) == (0, 99_999)
    assert tuple(range(100_000, 100_100)) == VALIDATION_SEEDS
    assert EVALUATION_SEED_START == 200_000
    assert evaluation_seeds(1) == (200_000,)
    assert evaluation_seeds(100) == tuple(range(200_000, 200_100))
    assert SHOWCASE_SEED == 100_000
    assert set(VALIDATION_SEEDS).isdisjoint(evaluation_seeds(100))
    with pytest.raises(ValueError, match="positive"):
        evaluation_seeds(0)


def test_training_configuration_validation() -> None:
    TrainConfig().validate()
    with pytest.raises(ValueError, match="timesteps"):
        TrainConfig(total_timesteps=0).validate()
    with pytest.raises(ValueError, match="buffer"):
        TrainConfig(buffer_size=4, batch_size=8).validate()
