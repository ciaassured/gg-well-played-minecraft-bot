from __future__ import annotations

import numpy as np
import pytest

import jump_trainer.training as training
from jump_trainer.env import JUMP, NOOP, MinecraftJumpEnv
from jump_trainer.evaluation import (
    Policy,
    evaluate_policy,
    promotion_key,
    scripted_one_jump_policy,
)
from jump_trainer.run_directory import RunDirectory, atomic_write_json
from jump_trainer.training import _promotion_metrics, _validate_candidate
from tests.fakes import SimulatedConnection


def _fixed_action_policy(action: int) -> Policy:
    def choose(_observation: np.ndarray) -> int:
        return action

    return choose


class _FixedActionModel:
    def predict(self, _observation: np.ndarray, *, deterministic: bool) -> tuple[np.ndarray, None]:
        assert deterministic is True
        return np.asarray(JUMP), None


def _implicit_seed(env: MinecraftJumpEnv) -> int:
    _observation, info = env.reset()
    return int(info["episode_seed"])


def _validation_run(tmp_path) -> RunDirectory:
    return RunDirectory.create(tmp_path / "runs", {"trainer": {}})


def test_fixed_action_metrics_and_promotion_order(tmp_path) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=30_000)
    seeds = (100_000, 100_001, 100_002)
    noop = evaluate_policy(env, _fixed_action_policy(NOOP), seeds, "noop", "validation")
    always = evaluate_policy(env, _fixed_action_policy(JUMP), seeds, "always-jump", "validation")
    assert noop.success_count == 0
    assert noop.success_rate == 0.0
    assert noop.terminal_reason_counts == {"TERMINAL_REASON_MISSED_JUMP": 3}
    assert always.success_count == 3
    assert always.success_rate == 1.0
    assert always.terminal_reason_counts == {"TERMINAL_REASON_SUCCESS": 3}
    assert always.mean_jump_requests_successful == 1.0
    assert always.mean_client_ticks_per_action == 1.0
    assert always.max_client_ticks_per_action == 1
    assert always.mean_server_ticks_per_action == 1.0
    assert always.max_server_ticks_per_action == 1
    assert promotion_key(always) > promotion_key(noop)
    assert _promotion_metrics(noop)["mean_completion_ticks"] is None
    atomic_write_json(tmp_path / "no-success.json", _promotion_metrics(noop))
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

    report = evaluate_policy(env, _fixed_action_policy(JUMP), range(12), "candidate", "validation")

    output = capsys.readouterr().err
    assert "[evaluate] validation/candidate: 0/12 episodes; starting" in output
    assert "[evaluate] validation/candidate: 10/12 episodes; successes=10, mean_return=" in output
    assert "client_ticks/action=1.00, server_ticks/action=1.00" in output
    assert "[evaluate] validation/candidate: 12/12 episodes; successes=12, mean_return=" in output
    assert report.success_count == 12
    env.close()


def test_validation_preserves_the_training_seed_stream(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training, "VALIDATION_SEEDS", (100_000, 100_001))
    control = MinecraftJumpEnv(
        connection_factory=SimulatedConnection,
        identifier_base=70_000,
    )
    validated = MinecraftJumpEnv(
        connection_factory=SimulatedConnection,
        identifier_base=80_000,
    )
    control.reset(seed=1_234)
    validated.reset(seed=1_234)

    expected_seed = _implicit_seed(control)
    _validate_candidate(_validation_run(tmp_path), validated, _FixedActionModel(), 10)

    assert _implicit_seed(validated) == expected_seed
    control.close()
    validated.close()


def test_repeated_validation_does_not_restart_training_seeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training, "VALIDATION_SEEDS", (100_000, 100_001))
    control = MinecraftJumpEnv(
        connection_factory=SimulatedConnection,
        identifier_base=90_000,
    )
    validated = MinecraftJumpEnv(
        connection_factory=SimulatedConnection,
        identifier_base=100_000,
    )
    control.reset(seed=5_678)
    validated.reset(seed=5_678)
    expected_seeds = (_implicit_seed(control), _implicit_seed(control))
    run = _validation_run(tmp_path)

    _validate_candidate(run, validated, _FixedActionModel(), 10)
    actual_seeds = [_implicit_seed(validated)]
    _validate_candidate(run, validated, _FixedActionModel(), 20)
    actual_seeds.append(_implicit_seed(validated))

    assert tuple(actual_seeds) == expected_seeds
    control.close()
    validated.close()


def test_validation_exception_restores_rng_state(tmp_path, monkeypatch) -> None:
    connection = SimulatedConnection()
    env = MinecraftJumpEnv(connection_factory=lambda: connection, identifier_base=110_000)
    env.reset(seed=9_876)
    original_random = env.np_random
    original_seed = env.np_random_seed

    def fail_evaluation(*args, **kwargs):
        del args, kwargs
        env.reset(seed=100_099)
        raise RuntimeError("scripted evaluation failure")

    monkeypatch.setattr(training, "evaluate_policy", fail_evaluation)
    with pytest.raises(RuntimeError, match="scripted evaluation failure"):
        _validate_candidate(_validation_run(tmp_path), env, _FixedActionModel(), 10)

    assert env.np_random is original_random
    assert env.np_random_seed == original_seed
    env.close()
