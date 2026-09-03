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
    validation_seeds,
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
    TrainConfig(total_timesteps=30_000).validate()
    with pytest.raises(ValueError, match="timesteps"):
        TrainConfig(total_timesteps=0).validate()
    with pytest.raises(ValueError, match="validation_interval"):
        TrainConfig(total_timesteps=30_000, validation_interval=0).validate()
    with pytest.raises(ValueError, match="validation episodes"):
        TrainConfig(total_timesteps=30_000, validation_episodes=0).validate()
    with pytest.raises(ValueError, match="validation episodes"):
        TrainConfig(total_timesteps=30_000, validation_episodes=101).validate()
    with pytest.raises(ValueError, match="buffer"):
        TrainConfig(total_timesteps=30_000, buffer_size=4, batch_size=8).validate()


@pytest.mark.parametrize("episode_count", (1, 20, 100))
def test_validation_seed_counts_select_fixed_prefixes(episode_count: int) -> None:
    assert validation_seeds(episode_count) == VALIDATION_SEEDS[:episode_count]


@pytest.mark.parametrize("episode_count", (-1, 0, 101))
def test_validation_seed_counts_reject_values_outside_partition(episode_count: int) -> None:
    with pytest.raises(ValueError, match=r"1\.\.100"):
        validation_seeds(episode_count)


def test_training_configuration_serializes_resolved_validation_count() -> None:
    config = TrainConfig(total_timesteps=30_000, validation_episodes=20)

    assert config.as_dict()["validation_episodes"] == 20
