from __future__ import annotations

import numpy as np
import pytest
from jump.v1 import jump_pb2 as pb

from jump_trainer.env import JUMP, NOOP, transition_reward
from jump_trainer.normalization import OBSERVATION_SPACE, normalize_observation
from tests.fakes import observation


def test_normalization_is_deterministic_and_bounded() -> None:
    raw = observation(distance=8.0, elapsed_ticks=0, on_ground=True)
    normalized = normalize_observation(raw)
    assert normalized.dtype == np.float32
    assert OBSERVATION_SPACE.contains(normalized)
    assert normalized.tolist() == pytest.approx([1.0, 0.0, 0.0, 2.0 / 3.0, 1.0, -1.0])
    assert np.array_equal(normalized, normalize_observation(raw))


def test_reward_is_only_progress_living_action_and_terminal_terms() -> None:
    previous = observation(distance=6.0)
    active = observation(sequence=1, elapsed_ticks=1, distance=5.8, phase=pb.EPISODE_PHASE_ACTIVE)
    success = observation(
        sequence=1,
        elapsed_ticks=1,
        distance=5.8,
        phase=pb.EPISODE_PHASE_TERMINAL,
        terminal_reason=pb.TERMINAL_REASON_SUCCESS,
    )
    missed = observation(
        sequence=1,
        elapsed_ticks=1,
        distance=5.8,
        phase=pb.EPISODE_PHASE_TERMINAL,
        terminal_reason=pb.TERMINAL_REASON_MISSED_JUMP,
    )
    truncated = observation(
        sequence=1,
        elapsed_ticks=200,
        distance=5.8,
        phase=pb.EPISODE_PHASE_TERMINAL,
        terminal_reason=pb.TERMINAL_REASON_TIME_LIMIT,
    )

    assert transition_reward(previous, active, NOOP) == pytest.approx(0.19)
    assert transition_reward(previous, active, JUMP) == pytest.approx(0.14)
    assert transition_reward(previous, success, JUMP) == pytest.approx(10.14)
    assert transition_reward(previous, missed, NOOP) == pytest.approx(-9.81)
    assert transition_reward(previous, truncated, NOOP) == pytest.approx(-9.81)
