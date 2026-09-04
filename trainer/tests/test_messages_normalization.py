from dataclasses import replace

import numpy as np
import pytest

from yrush_trainer.config import OBSERVATION_FEATURES, VOXEL_FEATURES
from yrush_trainer.errors import ProtocolStateError
from yrush_trainer.normalization import OBSERVATION_SPACE, normalize_observation_with_stats

from .fakes import raw_observation


def test_voxels_and_continuous_values_form_exact_flat_box() -> None:
    raw = raw_observation()
    normalized = normalize_observation_with_stats(raw)
    assert normalized.values.shape == (OBSERVATION_FEATURES,)
    assert normalized.values.dtype == np.float32
    assert OBSERVATION_SPACE.contains(normalized.values)
    assert tuple(normalized.values[:4]) == (0.0, 1.0, 0.0, 1.0)
    assert normalized.values[VOXEL_FEATURES] == pytest.approx(12.0 / 64.0)
    assert normalized.clipped_features == 0


def test_normalization_counts_clips_and_raw_validation_is_strict() -> None:
    clipped = normalize_observation_with_stats(
        replace(raw_observation(), forward_velocity=3.0, pitch_degrees=90.0)
    )
    assert clipped.clipped_features == 1
    assert clipped.values[VOXEL_FEATURES + 1] == 1.0
    with pytest.raises(ProtocolStateError, match="voxel"):
        replace(raw_observation(), block_properties=bytes(VOXEL_FEATURES - 1)).validate()
    with pytest.raises(ProtocolStateError, match="binary"):
        replace(raw_observation(), block_properties=bytes([2]) * VOXEL_FEATURES).validate()
