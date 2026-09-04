import numpy as np
import pytest

from yrush_trainer.rollout import RolloutCollector, Transition

from .fakes import normalized_observation


def transition(
    actor: int,
    step: int,
    *,
    version: int = 3,
    terminated: bool = False,
    truncated: bool = False,
    next_value: float = 0.5,
) -> Transition:
    return Transition(
        actor_index=actor,
        round_sequence=step + 1,
        policy_version=version,
        observation=normalized_observation(step / 10.0),
        action=np.asarray([2, 1, 0, 0, 2, 2], dtype=np.int64),
        reward=1.0,
        next_observation=normalized_observation((step + 1) / 10.0),
        terminated=terminated,
        truncated=truncated,
        episode_start=step == 0,
        log_probability=-1.0,
        value_estimate=0.5,
        next_value_estimate=next_value,
    )


def test_exact_per_client_length_and_policy_isolation() -> None:
    collector = RolloutCollector((0, 1), policy_version=3, length_per_client=3)
    for step in range(3):
        assert collector.add(transition(0, step, terminated=step == 1))
        assert collector.add(transition(1, step, truncated=step == 1))
    assert collector.complete
    assert not collector.add(transition(0, 4))
    assert collector.discarded_after_close == 1
    prepared = collector.prepare(gamma=0.9, gae_lambda=0.8)
    assert prepared.observations.shape == (3, 2, 513)
    assert prepared.actions.shape == (3, 2, 6)
    assert np.isfinite(prepared.advantages).all()
    # A true termination does not bootstrap, while a truncation does for its one-step delta.
    assert prepared.advantages[1, 1] > prepared.advantages[1, 0]

    mixed = RolloutCollector((0,), policy_version=2, length_per_client=1)
    with pytest.raises(ValueError, match="mix"):
        mixed.add(transition(0, 0, version=3))


def test_gae_does_not_cross_episode_boundaries() -> None:
    collector = RolloutCollector((0,), policy_version=3, length_per_client=3)
    collector.add(transition(0, 0))
    collector.add(transition(0, 1, terminated=True))
    collector.add(transition(0, 2))
    prepared = collector.prepare(gamma=1.0, gae_lambda=1.0)
    # Step 1 return is its reward only; step 2 cannot leak backward through the boundary.
    assert prepared.returns[1, 0] == pytest.approx(1.0)
    assert prepared.returns[0, 0] == pytest.approx(2.0)
