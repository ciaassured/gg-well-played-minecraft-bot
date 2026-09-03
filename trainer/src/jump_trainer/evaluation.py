"""Shared deterministic evaluation and checkpoint promotion metrics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from statistics import fmean
from time import monotonic
from typing import Any

import numpy as np
from numpy.typing import NDArray

from jump_trainer.console import emit, format_duration
from jump_trainer.env import JUMP, NOOP, MinecraftJumpEnv

Policy = Callable[[NDArray[np.float32]], int]
PROGRESS_EPISODE_INTERVAL = 10


@dataclass(frozen=True)
class EpisodeMetrics:
    seed: int
    success: bool
    terminal_reason: str
    return_value: float
    completion_ticks: int
    jump_requests: int
    mean_client_ticks_per_action: float
    max_client_ticks_per_action: int
    mean_server_ticks_per_action: float
    max_server_ticks_per_action: int
    client_index: int | None = None
    client_ordinal: int | None = None
    endpoint: str | None = None
    action_latency_ms_p95: float | None = None
    action_latency_ms_p99: float | None = None


@dataclass(frozen=True)
class EvaluationReport:
    policy_id: str
    suite: str
    episodes: tuple[EpisodeMetrics, ...]
    success_count: int
    mean_return: float
    mean_completion_ticks: float | None
    mean_jump_requests_successful: float | None
    mean_client_ticks_per_action: float
    max_client_ticks_per_action: int
    mean_server_ticks_per_action: float
    max_server_ticks_per_action: int
    action_latency_ms: dict[str, float | None] | None = None
    throughput_transitions_per_second: float | None = None
    per_client: dict[str, Any] | None = None

    @property
    def success_rate(self) -> float:
        return self.success_count / len(self.episodes)

    @property
    def terminal_reason_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(episode.terminal_reason for episode in self.episodes).items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "suite": self.suite,
            "episode_count": len(self.episodes),
            "success_count": self.success_count,
            "success_rate": self.success_rate,
            "terminal_reason_counts": self.terminal_reason_counts,
            "mean_return": self.mean_return,
            "mean_completion_ticks": self.mean_completion_ticks,
            "mean_jump_requests_successful": self.mean_jump_requests_successful,
            "mean_client_ticks_per_action": self.mean_client_ticks_per_action,
            "max_client_ticks_per_action": self.max_client_ticks_per_action,
            "mean_server_ticks_per_action": self.mean_server_ticks_per_action,
            "max_server_ticks_per_action": self.max_server_ticks_per_action,
            "action_latency_ms": self.action_latency_ms,
            "throughput_transitions_per_second": self.throughput_transitions_per_second,
            "per_client": self.per_client,
            "episodes": [asdict(episode) for episode in self.episodes],
        }


def scripted_one_jump_policy(trigger_distance: float = 1.5) -> Policy:
    """Return a reset-aware smoke policy that requests one near-wall jump."""

    if trigger_distance <= 0.0 or trigger_distance > 8.0:
        raise ValueError("trigger distance must be in (0, 8]")
    jump_requested = False

    def choose(observation: NDArray[np.float32]) -> int:
        nonlocal jump_requested
        if float(observation[5]) <= -1.0:
            jump_requested = False
        distance = float(observation[0]) * 8.0
        on_ground = float(observation[4]) > 0.0
        if not jump_requested and on_ground and distance <= trigger_distance:
            jump_requested = True
            return JUMP
        return NOOP

    return choose


def model_policy(model: Any) -> Policy:
    def choose(observation: NDArray[np.float32]) -> int:
        action, _state = model.predict(observation, deterministic=True)
        return int(np.asarray(action).item())

    return choose


def _report_progress(
    *,
    policy_id: str,
    suite: str,
    completed: int,
    total: int,
    successes: int,
    mean_return: float | None,
    mean_client_ticks_per_action: float | None,
    mean_server_ticks_per_action: float | None,
    started_at: float,
) -> None:
    if completed == 0:
        detail = "starting"
    else:
        if (
            mean_return is None
            or mean_client_ticks_per_action is None
            or mean_server_ticks_per_action is None
        ):
            raise ValueError("completed evaluation progress requires return and cadence metrics")
        elapsed = monotonic() - started_at
        remaining = elapsed * (total - completed) / completed
        detail = (
            f"successes={successes}, mean_return={mean_return:.3f}, "
            f"client_ticks/action={mean_client_ticks_per_action:.2f}, "
            f"server_ticks/action={mean_server_ticks_per_action:.2f}, "
            f"elapsed={format_duration(elapsed)}, eta={format_duration(remaining)}"
        )
    emit(
        "evaluate",
        f"{suite}/{policy_id}",
        f"{completed}/{total} episodes; {detail}",
    )


def evaluate_policy(
    env: MinecraftJumpEnv,
    policy: Policy,
    seeds: Iterable[int],
    policy_id: str,
    suite: str,
) -> EvaluationReport:
    """Evaluate without exploration, learning, or replay-buffer mutation."""

    episode_seeds = tuple(int(seed) for seed in seeds)
    if not episode_seeds:
        raise ValueError("evaluation requires at least one seed")
    results: list[EpisodeMetrics] = []
    client_tick_deltas: list[int] = []
    server_tick_deltas: list[int] = []
    started_at = monotonic()
    _report_progress(
        policy_id=policy_id,
        suite=suite,
        completed=0,
        total=len(episode_seeds),
        successes=0,
        mean_return=None,
        mean_client_ticks_per_action=None,
        mean_server_ticks_per_action=None,
        started_at=started_at,
    )
    for seed in episode_seeds:
        observation, _reset_info = env.reset(seed=seed)
        total_return = 0.0
        final_info: dict[str, Any] | None = None
        episode_client_tick_deltas: list[int] = []
        episode_server_tick_deltas: list[int] = []
        for _tick in range(201):
            action = policy(observation)
            observation, reward, terminated, truncated, info = env.step(action)
            total_return += reward
            client_delta = int(info["client_tick_delta"])
            server_delta = int(info["server_tick_delta"])
            episode_client_tick_deltas.append(client_delta)
            episode_server_tick_deltas.append(server_delta)
            client_tick_deltas.append(client_delta)
            server_tick_deltas.append(server_delta)
            if terminated or truncated:
                final_info = info
                break
        if final_info is None:
            raise RuntimeError(f"episode seed {seed} exceeded the authoritative time limit")
        results.append(
            EpisodeMetrics(
                seed=seed,
                success=bool(final_info["success"]),
                terminal_reason=str(final_info["terminal_reason"]),
                return_value=total_return,
                completion_ticks=int(final_info["elapsed_ticks"]),
                jump_requests=int(final_info["jump_requests"]),
                mean_client_ticks_per_action=fmean(episode_client_tick_deltas),
                max_client_ticks_per_action=max(episode_client_tick_deltas),
                mean_server_ticks_per_action=fmean(episode_server_tick_deltas),
                max_server_ticks_per_action=max(episode_server_tick_deltas),
            )
        )
        completed = len(results)
        if completed % PROGRESS_EPISODE_INTERVAL == 0 or completed == len(episode_seeds):
            _report_progress(
                policy_id=policy_id,
                suite=suite,
                completed=completed,
                total=len(episode_seeds),
                successes=sum(episode.success for episode in results),
                mean_return=fmean(episode.return_value for episode in results),
                mean_client_ticks_per_action=fmean(client_tick_deltas),
                mean_server_ticks_per_action=fmean(server_tick_deltas),
                started_at=started_at,
            )

    successful = [episode for episode in results if episode.success]
    return EvaluationReport(
        policy_id=policy_id,
        suite=suite,
        episodes=tuple(results),
        success_count=len(successful),
        mean_return=fmean(episode.return_value for episode in results),
        mean_completion_ticks=(
            fmean(episode.completion_ticks for episode in successful) if successful else None
        ),
        mean_jump_requests_successful=(
            fmean(episode.jump_requests for episode in successful) if successful else None
        ),
        mean_client_ticks_per_action=fmean(client_tick_deltas),
        max_client_ticks_per_action=max(client_tick_deltas),
        mean_server_ticks_per_action=fmean(server_tick_deltas),
        max_server_ticks_per_action=max(server_tick_deltas),
    )


def promotion_key(report: EvaluationReport) -> tuple[int, float, float]:
    """Lexicographic success, completion time, and jump-request ordering."""

    ticks = report.mean_completion_ticks
    jumps = report.mean_jump_requests_successful
    return (
        report.success_count,
        -(ticks if ticks is not None else float("inf")),
        -(jumps if jumps is not None else float("inf")),
    )
