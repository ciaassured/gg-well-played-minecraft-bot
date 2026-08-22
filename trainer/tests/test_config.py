from __future__ import annotations

import pytest

from jump_trainer.config import (
    SHOWCASE_SEED,
    TEST_SEEDS,
    TRAIN_SEED_MAX,
    TRAIN_SEED_MIN,
    VALIDATION_SEEDS,
    TrainConfig,
    seeds_for_suite,
)


def test_seed_partitions_are_exact_and_disjoint() -> None:
    assert (TRAIN_SEED_MIN, TRAIN_SEED_MAX) == (0, 99_999)
    assert tuple(range(100_000, 100_100)) == VALIDATION_SEEDS
    assert tuple(range(200_000, 200_100)) == TEST_SEEDS
    assert SHOWCASE_SEED == 100_000
    assert set(VALIDATION_SEEDS).isdisjoint(TEST_SEEDS)
    assert seeds_for_suite("validation") == VALIDATION_SEEDS
    assert seeds_for_suite("test") == TEST_SEEDS


def test_training_configuration_validation() -> None:
    TrainConfig().validate()
    with pytest.raises(ValueError, match="timesteps"):
        TrainConfig(total_timesteps=0).validate()
    with pytest.raises(ValueError, match="buffer"):
        TrainConfig(buffer_size=4, batch_size=8).validate()
