import numpy as np
from gymnasium.utils.env_checker import check_env
from yrush.v1 import yrush_pb2 as pb

from yrush_trainer.config import ACTION_CARDINALITIES, OBSERVATION_FEATURES
from yrush_trainer.env import YRushEnv, transition_reward

from .fakes import FakeConnection, raw_observation, round_result


def test_exact_spaces_and_gymnasium_contract() -> None:
    connection = FakeConnection()
    env = YRushEnv(connection_factory=lambda: connection, identifier_base=100)
    assert env.action_space.nvec.tolist() == list(ACTION_CARDINALITIES)
    assert env.observation_space.shape == (OBSERVATION_FEATURES,)
    check_env(env, skip_render_check=True)
    env.close()


def test_reward_terminal_values_and_stop_truncation() -> None:
    previous = raw_observation(target_difference=10.0)
    current = raw_observation(target_difference=9.5, sequence=1)
    assert transition_reward(previous, current, None) == 0.049
    assert (
        transition_reward(previous, current, round_result(outcome=pb.PLAYER_OUTCOME_WON)) == 10.049
    )
    assert (
        transition_reward(previous, current, round_result(outcome=pb.PLAYER_OUTCOME_ELIMINATED))
        == -9.951
    )
    assert (
        transition_reward(
            previous,
            current,
            round_result(outcome=pb.PLAYER_OUTCOME_LOST, winner_uuid="player-1"),
        )
        == -0.951
    )
    assert (
        transition_reward(
            previous,
            current,
            round_result(outcome=pb.PLAYER_OUTCOME_DRAW, winner_uuid=""),
        )
        == -1.951
    )
    assert (
        transition_reward(
            previous,
            current,
            round_result(outcome=pb.PLAYER_OUTCOME_STOPPED, winner_uuid=""),
        )
        == 0.049
    )

    connection = FakeConnection([pb.PLAYER_OUTCOME_STOPPED])
    env = YRushEnv(connection_factory=lambda: connection, identifier_base=200)
    env.reset(options={"round_sequence": 1, "policy_version": 0})
    action = np.asarray([2, 1, 0, 0, 2, 2], dtype=np.int64)
    _observation, _reward, terminated, truncated, _info = env.step(action)
    assert not terminated and not truncated
    _observation, _reward, terminated, truncated, info = env.step(action)
    assert not terminated and truncated
    assert info["outcome"] == "STOPPED"
