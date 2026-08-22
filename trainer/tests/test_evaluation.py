from __future__ import annotations

from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.evaluation import (
    always_jump_policy,
    evaluate_policy,
    final_passing_result,
    noop_policy,
    promotion_key,
)
from jump_trainer.run_directory import atomic_write_json
from jump_trainer.training import _promotion_metrics
from tests.fakes import SimulatedConnection


def test_scripted_baselines_and_promotion_order(tmp_path) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=30_000)
    seeds = (100_000, 100_001, 100_002)
    noop = evaluate_policy(env, noop_policy, seeds, "noop", "validation")
    always = evaluate_policy(env, always_jump_policy, seeds, "always-jump", "validation")
    assert noop.success_count == 0
    assert always.success_count == 3
    assert always.mean_jump_requests_successful == 1.0
    assert promotion_key(always) > promotion_key(noop)
    assert _promotion_metrics(noop)["mean_completion_ticks"] is None
    atomic_write_json(tmp_path / "no-success.json", _promotion_metrics(noop))
    env.close()


def test_final_acceptance_checks_returns_and_jump_count() -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=40_000)
    seeds = tuple(range(100))
    noop = evaluate_policy(env, noop_policy, seeds, "noop", "test")
    candidate = evaluate_policy(env, always_jump_policy, seeds, "candidate", "test")
    always = evaluate_policy(env, always_jump_policy, seeds, "always-jump", "test")
    result = final_passing_result(candidate, noop, always)
    assert result["requirements"]["success_at_least_95"] is True
    assert result["requirements"]["at_most_two_jumps_on_success"] is True
    assert result["requirements"]["return_above_noop"] is True
    assert result["requirements"]["return_above_always_jump"] is False
    assert result["passed"] is False
    env.close()
