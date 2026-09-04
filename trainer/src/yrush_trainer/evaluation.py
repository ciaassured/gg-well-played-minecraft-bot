"""Shared-round reconciliation, diagnostics, and promotion ordering."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

from yrush_trainer.errors import ProtocolStateError


@dataclass(frozen=True)
class PlayerRound:
    actor_index: int
    endpoint: str
    player_uuid: str
    player_name: str
    outcome: str
    winner_uuid: str | None
    completion_time_seconds: float
    best_remaining_target_distance: float
    episode_return: float
    episode_actions: int
    observation_clipped_features: int
    target_direction: str
    target_progress: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GlobalRound:
    round_sequence: int
    policy_version: int
    completed: bool
    stopped: bool
    winner_uuid: str | None
    participant_count: int
    completion_time_seconds: float | None
    best_remaining_target_distance: float
    elimination_count: int
    players: tuple[PlayerRound, ...]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["players"] = [player.as_dict() for player in self.players]
        return value


@dataclass(frozen=True)
class EvaluationReport:
    policy_id: str
    policy_version: int
    rounds: tuple[GlobalRound, ...]

    @property
    def round_completion_rate(self) -> float:
        return sum(round_.completed for round_ in self.rounds) / len(self.rounds)

    @property
    def mean_completion_time(self) -> float | None:
        values = [
            round_.completion_time_seconds
            for round_ in self.rounds
            if round_.completion_time_seconds is not None
        ]
        return fmean(values) if values else None

    @property
    def mean_best_draw_distance(self) -> float | None:
        values = [
            round_.best_remaining_target_distance
            for round_ in self.rounds
            if not round_.completed and not round_.stopped
        ]
        return fmean(values) if values else None

    @property
    def promotion_key(self) -> tuple[float, float, float]:
        completion_time = self.mean_completion_time
        draw_distance = self.mean_best_draw_distance
        return (
            self.round_completion_rate,
            -(completion_time if completion_time is not None else float("inf")),
            -(draw_distance if draw_distance is not None else float("inf")),
        )

    def as_dict(self) -> dict[str, Any]:
        players = [player for round_ in self.rounds for player in round_.players]
        global_outcomes = Counter(
            "COMPLETED" if round_.completed else "STOPPED" if round_.stopped else "DRAW"
            for round_ in self.rounds
        )
        player_outcomes = Counter(player.outcome for player in players)
        per_player: dict[str, Counter[str]] = {}
        for player in players:
            per_player.setdefault(player.player_uuid, Counter())[player.outcome] += 1
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "global_round_count": len(self.rounds),
            "global_round_completion_rate": self.round_completion_rate,
            "mean_completion_time_seconds": self.mean_completion_time,
            "mean_best_remaining_target_distance_in_draws": self.mean_best_draw_distance,
            "global_outcomes": dict(sorted(global_outcomes.items())),
            "player_outcomes": dict(sorted(player_outcomes.items())),
            "elimination_count": sum(round_.elimination_count for round_ in self.rounds),
            "participant_counts": [round_.participant_count for round_ in self.rounds],
            "per_player_outcomes": {
                player: dict(sorted(counts.items()))
                for player, counts in sorted(per_player.items())
            },
            "per_player_win_rate": {
                player: counts.get("WON", 0) / sum(counts.values())
                for player, counts in sorted(per_player.items())
            },
            "rounds": [round_.as_dict() for round_ in self.rounds],
        }


def reconcile_round(
    round_sequence: int,
    policy_version: int,
    results: dict[int, dict[str, Any]],
    endpoints: dict[int, str],
) -> GlobalRound:
    if not results or set(results) != set(endpoints):
        raise ProtocolStateError("global result does not contain the complete fixed pool")
    players = tuple(
        PlayerRound(
            actor_index=actor,
            endpoint=endpoints[actor],
            player_uuid=str(info["player_uuid"]),
            player_name=str(info["player_name"]),
            outcome=str(info["outcome"]),
            winner_uuid=info.get("winner_uuid"),
            completion_time_seconds=float(info["completion_time_seconds"]),
            best_remaining_target_distance=float(info["best_remaining_target_distance"]),
            episode_return=float(info["episode_return"]),
            episode_actions=int(info["episode_actions"]),
            observation_clipped_features=int(info["observation_clipped_features"]),
            target_direction=str(info["target_direction"]),
            target_progress=float(info["target_progress"]),
        )
        for actor, info in sorted(results.items())
    )
    if any(int(info["round_sequence"]) != round_sequence for info in results.values()):
        raise ProtocolStateError("clients reported different round sequences")
    if any(int(info["policy_version"]) != policy_version for info in results.values()):
        raise ProtocolStateError("clients reported different policy versions")
    participant_counts = {int(info["participant_count"]) for info in results.values()}
    if participant_counts != {len(endpoints)}:
        raise ProtocolStateError("YRush participant count does not match the fixed pool")
    if len({player.target_direction for player in players}) != 1:
        raise ProtocolStateError("clients disagree about the YRush target direction")

    winners = [player for player in players if player.outcome == "WON"]
    draws = [player for player in players if player.outcome == "DRAW"]
    stopped = [player for player in players if player.outcome == "STOPPED"]
    ordinary = [player for player in players if player.outcome not in {"ELIMINATED"}]
    winner_uuid: str | None = None
    completion_time: float | None = None
    if winners:
        if len(winners) != 1 or any(
            player.outcome not in {"WON", "LOST", "ELIMINATED"} for player in players
        ):
            raise ProtocolStateError("winning round has conflicting player outcomes")
        winner_uuid = winners[0].player_uuid
        if any(
            player.winner_uuid != winner_uuid
            for player in players
            if player.outcome != "ELIMINATED"
        ):
            raise ProtocolStateError("clients disagree about the YRush winner")
        completion_time = winners[0].completion_time_seconds
    elif stopped:
        if any(player.outcome not in {"STOPPED", "ELIMINATED"} for player in players):
            raise ProtocolStateError("stopped round has conflicting player outcomes")
    elif draws:
        if any(player.outcome not in {"DRAW", "ELIMINATED"} for player in players):
            raise ProtocolStateError("drawn round has conflicting player outcomes")
    elif ordinary:
        raise ProtocolStateError("global round has no winner, draw, or stop result")
    if not winners and any(player.winner_uuid is not None for player in players):
        raise ProtocolStateError("non-winning round reported a winner")

    return GlobalRound(
        round_sequence=round_sequence,
        policy_version=policy_version,
        completed=bool(winners),
        stopped=bool(stopped),
        winner_uuid=winner_uuid,
        participant_count=len(endpoints),
        completion_time_seconds=completion_time,
        best_remaining_target_distance=min(
            player.best_remaining_target_distance for player in players
        ),
        elimination_count=sum(player.outcome == "ELIMINATED" for player in players),
        players=players,
    )
