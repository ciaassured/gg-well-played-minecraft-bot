import pytest

from yrush_trainer.errors import ProtocolStateError
from yrush_trainer.evaluation import EvaluationReport, reconcile_round


def info(actor: int, outcome: str, winner: str | None) -> dict[str, object]:
    return {
        "round_sequence": 7,
        "policy_version": 2,
        "player_uuid": f"player-{actor}",
        "player_name": f"player-{actor}",
        "outcome": outcome,
        "winner_uuid": winner,
        "participant_count": 2,
        "completion_time_seconds": 12.0 + actor,
        "best_remaining_target_distance": float(actor),
        "episode_return": 1.0,
        "episode_actions": 10,
        "observation_clipped_features": 0,
        "target_direction": "UP",
        "target_progress": 3.0,
    }


def test_global_results_are_reconciled_and_ranked_lexicographically() -> None:
    complete = reconcile_round(
        7,
        2,
        {0: info(0, "WON", "player-0"), 1: info(1, "LOST", "player-0")},
        {0: "a:1", 1: "b:1"},
    )
    draw = reconcile_round(
        7,
        2,
        {0: info(0, "DRAW", None), 1: info(1, "ELIMINATED", None)},
        {0: "a:1", 1: "b:1"},
    )
    report = EvaluationReport("candidate", 2, (complete, draw))
    assert report.round_completion_rate == 0.5
    assert report.mean_completion_time == 12.0
    assert report.mean_best_draw_distance == 0.0
    assert report.as_dict()["global_outcomes"] == {"COMPLETED": 1, "DRAW": 1}
    assert report.as_dict()["elimination_count"] == 1
    assert report.as_dict()["per_player_win_rate"]["player-0"] == 0.5
    assert report.as_dict()["per_player_outcomes"]["player-1"] == {
        "ELIMINATED": 1,
        "LOST": 1,
    }


def test_conflicting_winners_and_partial_rounds_are_rejected() -> None:
    with pytest.raises(ProtocolStateError, match="complete fixed pool"):
        reconcile_round(7, 2, {0: info(0, "WON", "player-0")}, {0: "a", 1: "b"})
    with pytest.raises(ProtocolStateError, match="winner"):
        reconcile_round(
            7,
            2,
            {0: info(0, "WON", "player-0"), 1: info(1, "LOST", "player-1")},
            {0: "a", 1: "b"},
        )
    with pytest.raises(ProtocolStateError, match="winner"):
        reconcile_round(
            7,
            2,
            {0: info(0, "WON", "player-0"), 1: info(1, "LOST", None)},
            {0: "a", 1: "b"},
        )
