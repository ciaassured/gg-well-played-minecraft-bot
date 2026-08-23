from __future__ import annotations

import numpy as np

from jump_trainer.env import MinecraftJumpEnv
from jump_trainer.evaluation import (
    always_jump_policy,
    evaluate_policy,
    final_passing_result,
    noop_policy,
    promotion_key,
    scripted_one_jump_policy,
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


def test_scripted_smoke_policy_jumps_once_and_resets_between_episodes() -> None:
    policy = scripted_one_jump_policy()
    far = np.asarray([0.5, 0, 0, 0, 1, -1], dtype=np.float32)
    trigger = np.asarray([1.4 / 8.0, 0, 0, 0, 1, -0.9], dtype=np.float32)
    airborne = np.asarray([1.0 / 8.0, 0.5, 0.2, 0.2, -1, -0.8], dtype=np.float32)

    assert policy(far) == 0
    assert policy(trigger) == 1
    assert policy(airborne) == 0
    assert policy(trigger) == 0
    assert policy(far) == 0
    assert policy(trigger) == 1


def test_evaluation_reports_visible_progress(capsys) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=60_000)

    report = evaluate_policy(env, always_jump_policy, range(12), "candidate", "validation")

    output = capsys.readouterr().err
    assert "[evaluate] validation/candidate: 0/12 episodes; starting" in output
    assert "[evaluate] validation/candidate: 10/12 episodes; successes=10" in output
    assert "[evaluate] validation/candidate: 12/12 episodes; successes=12" in output
    assert report.success_count == 12
    env.close()
