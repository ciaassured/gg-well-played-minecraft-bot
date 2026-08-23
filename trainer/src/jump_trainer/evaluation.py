"""Shared deterministic evaluation and checkpoint promotion metrics."""

from __future__ import annotations

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


@dataclass(frozen=True)
class EvaluationReport:
    policy_id: str
    suite: str
    episodes: tuple[EpisodeMetrics, ...]
    success_count: int
    mean_return: float
    mean_completion_ticks: float | None
    mean_jump_requests_successful: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "suite": self.suite,
            "episode_count": len(self.episodes),
            "success_count": self.success_count,
            "mean_return": self.mean_return,
            "mean_completion_ticks": self.mean_completion_ticks,
            "mean_jump_requests_successful": self.mean_jump_requests_successful,
            "episodes": [asdict(episode) for episode in self.episodes],
        }


def noop_policy(_observation: NDArray[np.float32]) -> int:
    return NOOP


def always_jump_policy(_observation: NDArray[np.float32]) -> int:
    return JUMP


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
    started_at: float,
) -> None:
    if completed == 0:
        detail = "starting"
    else:
        if mean_return is None:
            raise ValueError("completed evaluation progress requires a mean return")
        elapsed = monotonic() - started_at
        remaining = elapsed * (total - completed) / completed
        detail = (
            f"successes={successes}, mean_return={mean_return:.3f}, "
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
    started_at = monotonic()
    _report_progress(
        policy_id=policy_id,
        suite=suite,
        completed=0,
        total=len(episode_seeds),
        successes=0,
        mean_return=None,
        started_at=started_at,
    )
    for seed in episode_seeds:
        observation, _reset_info = env.reset(seed=seed)
        total_return = 0.0
        final_info: dict[str, Any] | None = None
        for _tick in range(201):
            action = policy(observation)
            observation, reward, terminated, truncated, info = env.step(action)
            total_return += reward
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


def final_passing_result(
    checkpoint: EvaluationReport,
    noop: EvaluationReport,
    always_jump: EvaluationReport,
) -> dict[str, Any]:
    jumps = checkpoint.mean_jump_requests_successful
    requirements = {
        "success_at_least_95": checkpoint.success_count >= 95,
        "return_above_noop": checkpoint.mean_return > noop.mean_return,
        "return_above_always_jump": checkpoint.mean_return > always_jump.mean_return,
        "at_most_two_jumps_on_success": jumps is not None and jumps <= 2.0,
    }
    return {"passed": all(requirements.values()), "requirements": requirements}
