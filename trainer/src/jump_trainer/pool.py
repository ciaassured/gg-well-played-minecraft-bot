"""Fixed client pool with one blocking I/O actor thread per Fabric endpoint."""

from __future__ import annotations

import hashlib
import queue
import random
import threading
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from statistics import fmean
from time import monotonic
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from jump_trainer.config import TRAIN_SEED_MAX, TRAIN_SEED_MIN
from jump_trainer.endpoints import Endpoint
from jump_trainer.env import JUMP, NOOP, MinecraftJumpEnv
from jump_trainer.errors import InfrastructureError
from jump_trainer.evaluation import EpisodeMetrics, EvaluationReport


class BatchPolicy(Protocol):
    def actions(
        self,
        actor_indices: tuple[int, ...],
        observations: NDArray[np.float32],
        *,
        deterministic: bool,
    ) -> NDArray[np.int64]: ...

    def reset(self, actor_index: int) -> None: ...


class ModelBatchPolicy:
    """Batch adapter for SB3-compatible predict methods."""

    def __init__(self, model: Any):
        self.model = model

    def actions(
        self,
        actor_indices: tuple[int, ...],
        observations: NDArray[np.float32],
        *,
        deterministic: bool,
    ) -> NDArray[np.int64]:
        del actor_indices
        actions, _state = self.model.predict(observations, deterministic=deterministic)
        return np.asarray(actions, dtype=np.int64).reshape(-1)

    def reset(self, actor_index: int) -> None:
        del actor_index


class ScriptedBatchPolicy:
    """One-jump showcase policy with independent state for every actor."""

    def __init__(self, trigger_distance: float = 1.5):
        if not 0.0 < trigger_distance <= 8.0:
            raise ValueError("trigger distance must be in (0, 8]")
        self.trigger_distance = trigger_distance
        self._jumped: dict[int, bool] = defaultdict(bool)

    def actions(
        self,
        actor_indices: tuple[int, ...],
        observations: NDArray[np.float32],
        *,
        deterministic: bool,
    ) -> NDArray[np.int64]:
        del deterministic
        selected: list[int] = []
        for actor_index, observation in zip(actor_indices, observations, strict=True):
            distance = float(observation[0]) * 8.0
            on_ground = float(observation[4]) > 0.0
            if not self._jumped[actor_index] and on_ground and distance <= self.trigger_distance:
                self._jumped[actor_index] = True
                selected.append(JUMP)
            else:
                selected.append(NOOP)
        return np.asarray(selected, dtype=np.int64)

    def reset(self, actor_index: int) -> None:
        self._jumped[actor_index] = False


@dataclass(frozen=True)
class Transition:
    actor_index: int
    endpoint: str
    cycle: int
    observation: NDArray[np.float32]
    action: int
    reward: float
    next_observation: NDArray[np.float32]
    done: bool
    info: dict[str, Any]
    action_latency_ms: float


@dataclass(frozen=True)
class CollectionResult:
    requested_transitions: int
    actual_transitions: int
    first_transition: int
    last_cycle: int
    elapsed_seconds: float

    @property
    def throughput(self) -> float:
        collected = self.actual_transitions - self.first_transition
        return collected / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


@dataclass(frozen=True)
class _Command:
    kind: str
    seed: int | None = None
    action: int | None = None
    cycle: int = 0


@dataclass(frozen=True)
class _Event:
    actor_index: int
    kind: str
    observation: NDArray[np.float32] | None = None
    previous: NDArray[np.float32] | None = None
    action: int | None = None
    reward: float = 0.0
    terminated: bool = False
    truncated: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    cycle: int = 0
    latency_ms: float = 0.0
    failure: BaseException | None = None


EnvironmentFactory = Callable[[Endpoint], MinecraftJumpEnv]
TransitionSink = Callable[[tuple[Transition, ...], int], None]
CycleCallback = Callable[[int], None]


class TrainingSeedStreams:
    """Independent deterministic streams derived from (run seed, client ordinal)."""

    def __init__(self, run_seed: int, endpoints: tuple[Endpoint, ...]):
        self._random: dict[int, random.Random] = {}
        for endpoint in endpoints:
            identity = endpoint.ordinal if endpoint.ordinal is not None else endpoint.index
            digest = hashlib.sha256(f"{run_seed}:{identity}".encode()).digest()
            self._random[endpoint.index] = random.Random(int.from_bytes(digest[:16], "big"))

    def next(self, actor_index: int) -> int:
        return self._random[actor_index].randint(TRAIN_SEED_MIN, TRAIN_SEED_MAX)


class _Actor:
    def __init__(
        self,
        endpoint: Endpoint,
        output: queue.Queue[_Event],
        environment_factory: EnvironmentFactory,
        startup_deadline: float,
    ) -> None:
        self.endpoint = endpoint
        self.output = output
        self.environment_factory = environment_factory
        self.startup_deadline = startup_deadline
        self.commands: queue.Queue[_Command] = queue.Queue(maxsize=1)
        self.latest_observation: NDArray[np.float32] | None = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"jump-client-actor-{endpoint.index}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def submit(self, command: _Command) -> None:
        try:
            self.commands.put_nowait(command)
        except queue.Full as exception:
            raise InfrastructureError(
                f"client actor {self.endpoint.address} already has an in-flight operation"
            ) from exception

    def stop(self) -> None:
        self.stopping.set()
        with suppress(queue.Full):
            self.commands.put_nowait(_Command("stop"))

    def _run(self) -> None:
        env: MinecraftJumpEnv | None = None
        current_seed: int | None = None
        current_observation: NDArray[np.float32] | None = None
        last_failure: BaseException | None = None
        try:
            env = self.environment_factory(self.endpoint)
            while not self.stopping.is_set() and monotonic() < self.startup_deadline:
                try:
                    env.connect()
                    self.output.put(_Event(self.endpoint.index, "ready"))
                    break
                except (InfrastructureError, OSError) as exception:
                    last_failure = exception
                    self.stopping.wait(min(1.0, max(0.0, self.startup_deadline - monotonic())))
            else:
                raise InfrastructureError(
                    f"startup timed out for {self.endpoint.address}: {last_failure or 'not ready'}"
                )

            while not self.stopping.is_set():
                command = self.commands.get()
                if command.kind == "stop":
                    break
                if command.kind == "reset":
                    if command.seed is None:
                        raise AssertionError("reset command has no seed")
                    current_seed = command.seed
                    observation, info = env.reset(seed=current_seed)
                    current_observation = np.asarray(observation, dtype=np.float32).copy()
                    info = dict(info)
                    info["episode_seed"] = current_seed
                    self.output.put(
                        _Event(
                            self.endpoint.index,
                            "reset",
                            observation=current_observation,
                            info=info,
                            cycle=command.cycle,
                        )
                    )
                    continue
                if command.kind != "action" or command.action is None:
                    raise AssertionError(f"invalid actor command: {command.kind}")
                if current_observation is None:
                    raise RuntimeError("actor action has no active observation")
                before = current_observation
                started = monotonic()
                observation, reward, terminated, truncated, info = env.step(command.action)
                latency_ms = (monotonic() - started) * 1000.0
                current_observation = np.asarray(observation, dtype=np.float32).copy()
                info = dict(info)
                info["episode_seed"] = current_seed
                self.output.put(
                    _Event(
                        self.endpoint.index,
                        "step",
                        observation=current_observation,
                        previous=before,
                        action=command.action,
                        reward=float(reward),
                        terminated=bool(terminated),
                        truncated=bool(truncated),
                        info=info,
                        cycle=command.cycle,
                        latency_ms=latency_ms,
                    )
                )
        except BaseException as exception:
            if not self.stopping.is_set():
                self.output.put(_Event(self.endpoint.index, "error", failure=exception))
        finally:
            if env is not None:
                env.close()


@dataclass
class _EpisodeAccumulator:
    seed: int
    total_return: float = 0.0
    client_deltas: list[int] = field(default_factory=list)
    server_deltas: list[int] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)


class ClientPool:
    """A fail-fast fixed pool; configured clients are never silently replaced or removed."""

    def __init__(
        self,
        endpoints: tuple[Endpoint, ...],
        *,
        startup_timeout: float,
        message_timeout: float,
        reset_retries: int,
        environment_factory: EnvironmentFactory | None = None,
    ) -> None:
        if not endpoints:
            raise ValueError("client pool must contain at least one endpoint")
        if startup_timeout <= 0:
            raise ValueError("pool startup timeout must be positive")
        self.endpoints = endpoints
        self.startup_timeout = startup_timeout
        self._events: queue.Queue[_Event] = queue.Queue()
        self._latencies: dict[int, list[float]] = defaultdict(list)
        self._client_ticks: dict[int, list[int]] = defaultdict(list)
        self._server_ticks: dict[int, list[int]] = defaultdict(list)
        self._transition_counts: dict[int, int] = defaultdict(int)
        self._started_at = monotonic()
        selected_factory = environment_factory
        if selected_factory is None:

            def default_environment_factory(endpoint: Endpoint) -> MinecraftJumpEnv:
                return MinecraftJumpEnv(
                    host=endpoint.host,
                    port=endpoint.port,
                    timeout=message_timeout,
                    reset_retries=reset_retries,
                )

            selected_factory = default_environment_factory

        deadline = monotonic() + startup_timeout
        self._actors = {
            endpoint.index: _Actor(endpoint, self._events, selected_factory, deadline)
            for endpoint in endpoints
        }
        self._closed = False

    @property
    def width(self) -> int:
        return len(self.endpoints)

    def start(self) -> None:
        for actor in self._actors.values():
            actor.start()
        waiting = set(self._actors)
        deadline = monotonic() + self.startup_timeout
        try:
            while waiting:
                event = self._next_event(deadline)
                if event.kind != "ready" or event.actor_index not in waiting:
                    raise InfrastructureError("client pool emitted an invalid startup event")
                waiting.remove(event.actor_index)
        except BaseException:
            self.close()
            raise

    def collect(
        self,
        *,
        requested_total: int,
        actual_total: int,
        first_cycle: int,
        seeds: TrainingSeedStreams,
        policy: BatchPolicy,
        transition_sink: TransitionSink,
        after_cycle: CycleCallback | None = None,
    ) -> CollectionResult:
        if requested_total <= actual_total:
            raise ValueError("requested transition boundary must exceed the actual count")
        segment_started = monotonic()
        segment_first = actual_total
        active = tuple(sorted(self._actors))
        self._reset(active, {index: seeds.next(index) for index in active}, policy, first_cycle)
        cycle = first_cycle
        while actual_total < requested_total:
            cycle += 1
            observations = self._observations(active)
            actions = self._validate_actions(
                policy.actions(active, observations, deterministic=False), len(active)
            )
            for actor_index, action in zip(active, actions, strict=True):
                self._actors[actor_index].submit(
                    _Command("action", action=int(action), cycle=cycle)
                )
            events = self._await_kind("step", set(active))
            transitions: list[Transition] = []
            terminal: list[int] = []
            for event in events:
                transition = self._transition(event)
                transitions.append(transition)
                actual_total += 1
                if transition.done:
                    terminal.append(event.actor_index)
            transition_sink(tuple(transitions), cycle)
            if after_cycle is not None:
                after_cycle(cycle)
            if actual_total >= requested_total:
                break
            if terminal:
                self._reset(
                    tuple(terminal),
                    {index: seeds.next(index) for index in terminal},
                    policy,
                    cycle,
                )
        return CollectionResult(
            requested_transitions=requested_total,
            actual_transitions=actual_total,
            first_transition=segment_first,
            last_cycle=cycle,
            elapsed_seconds=monotonic() - segment_started,
        )

    def evaluate(
        self,
        policy: BatchPolicy,
        seeds: Iterable[int],
        *,
        policy_id: str,
        suite: str,
        require_unique_seeds: bool = True,
    ) -> EvaluationReport:
        ordered_seeds = tuple(int(seed) for seed in seeds)
        if not ordered_seeds:
            raise ValueError("evaluation requires at least one seed")
        if require_unique_seeds and len(set(ordered_seeds)) != len(ordered_seeds):
            raise ValueError("evaluation seeds must be unique")
        pending = deque(ordered_seeds)
        active: dict[int, _EpisodeAccumulator] = {}
        ready: dict[int, NDArray[np.float32]] = {}
        in_flight: set[int] = set()
        results: list[EpisodeMetrics] = []
        evaluation_latencies: list[float] = []
        started = monotonic()

        for actor_index in sorted(self._actors):
            if not pending:
                break
            seed = pending.popleft()
            active[actor_index] = _EpisodeAccumulator(seed)
            policy.reset(actor_index)
            self._actors[actor_index].submit(_Command("reset", seed=seed))
            in_flight.add(actor_index)

        while active:
            event = self._next_event(monotonic() + max(5.0, self.startup_timeout))
            in_flight.discard(event.actor_index)
            if event.kind == "reset":
                if event.observation is None:
                    raise InfrastructureError("client reset returned no observation")
                ready[event.actor_index] = event.observation
            elif event.kind == "step":
                accumulator = active[event.actor_index]
                accumulator.total_return += event.reward
                accumulator.latencies.append(event.latency_ms)
                evaluation_latencies.append(event.latency_ms)
                accumulator.client_deltas.append(int(event.info["client_tick_delta"]))
                accumulator.server_deltas.append(int(event.info["server_tick_delta"]))
                self._record_step(event)
                if event.terminated or event.truncated:
                    results.append(self._episode_metrics(event, accumulator))
                    del active[event.actor_index]
                    if pending:
                        seed = pending.popleft()
                        active[event.actor_index] = _EpisodeAccumulator(seed)
                        policy.reset(event.actor_index)
                        self._actors[event.actor_index].submit(_Command("reset", seed=seed))
                        in_flight.add(event.actor_index)
                else:
                    if event.observation is None:
                        raise InfrastructureError("client step returned no observation")
                    ready[event.actor_index] = event.observation
            else:
                raise InfrastructureError(f"unexpected pool event during evaluation: {event.kind}")

            if ready:
                indices = tuple(sorted(ready))
                observations = np.stack([ready.pop(index) for index in indices]).astype(np.float32)
                actions = self._validate_actions(
                    policy.actions(indices, observations, deterministic=True), len(indices)
                )
                for actor_index, action in zip(indices, actions, strict=True):
                    self._actors[actor_index].submit(
                        _Command("action", action=int(action), cycle=0)
                    )
                    in_flight.add(actor_index)

        results.sort(key=lambda episode: (episode.seed, episode.client_index or 0))
        if Counter(episode.seed for episode in results) != Counter(ordered_seeds):
            raise InfrastructureError("evaluation did not execute every seed exactly once")
        return self._evaluation_report(
            policy_id,
            suite,
            results,
            monotonic() - started,
            evaluation_latencies,
        )

    def stats(self) -> dict[str, Any]:
        all_latencies = [value for values in self._latencies.values() for value in values]
        all_client_ticks = [value for values in self._client_ticks.values() for value in values]
        all_server_ticks = [value for values in self._server_ticks.values() for value in values]
        elapsed = monotonic() - self._started_at
        transitions = sum(self._transition_counts.values())
        return {
            "client_count": self.width,
            "clients": [
                {
                    **endpoint.as_dict(),
                    "transitions": self._transition_counts[endpoint.index],
                    "action_latency_ms": _percentiles(self._latencies[endpoint.index]),
                    "mean_client_ticks_per_action": _mean(self._client_ticks[endpoint.index]),
                    "client_tick_delta": _percentiles(self._client_ticks[endpoint.index]),
                    "mean_server_ticks_per_action": _mean(self._server_ticks[endpoint.index]),
                    "server_tick_delta": _percentiles(self._server_ticks[endpoint.index]),
                    "throughput_transitions_per_second": (
                        self._transition_counts[endpoint.index] / elapsed if elapsed > 0 else 0.0
                    ),
                }
                for endpoint in self.endpoints
            ],
            "action_latency_ms": _percentiles(all_latencies),
            "client_tick_delta": _percentiles(all_client_ticks),
            "server_tick_delta": _percentiles(all_server_ticks),
            "transitions": transitions,
            "elapsed_seconds": elapsed,
            "throughput_transitions_per_second": transitions / elapsed if elapsed > 0 else 0.0,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for actor in self._actors.values():
            actor.stop()
        for actor in self._actors.values():
            actor.thread.join(timeout=10.0)

    def __enter__(self) -> ClientPool:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _reset(
        self,
        indices: tuple[int, ...],
        seeds: dict[int, int],
        policy: BatchPolicy,
        cycle: int,
    ) -> None:
        for actor_index in indices:
            policy.reset(actor_index)
            self._actors[actor_index].submit(
                _Command("reset", seed=seeds[actor_index], cycle=cycle)
            )
        self._await_kind("reset", set(indices))

    def _observations(self, indices: tuple[int, ...]) -> NDArray[np.float32]:
        # Actor environments retain the active normalized observation after reset/step. To avoid
        # sharing mutable Gymnasium state, obtain it from a zero-action-free cached event map.
        # Collection is lock-step, so the most recent reset/step events are available here.
        observations: list[NDArray[np.float32]] = []
        for actor_index in indices:
            actor = self._actors[actor_index]
            env_observation = actor.latest_observation
            if env_observation is None:
                raise InfrastructureError(f"client {actor.endpoint.address} has no observation")
            observations.append(env_observation)
        return np.stack(observations).astype(np.float32)

    def _await_kind(self, kind: str, expected: set[int]) -> tuple[_Event, ...]:
        events: list[_Event] = []
        deadline = monotonic() + max(5.0, self.startup_timeout)
        while expected:
            event = self._next_event(deadline)
            if event.kind != kind or event.actor_index not in expected:
                raise InfrastructureError(
                    f"unexpected {event.kind} event from client {event.actor_index}; "
                    f"expected {kind}"
                )
            expected.remove(event.actor_index)
            if event.observation is not None:
                self._actors[event.actor_index].latest_observation = event.observation
            events.append(event)
        return tuple(events)

    def _next_event(self, deadline: float) -> _Event:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise InfrastructureError("timed out waiting for the configured client pool")
        try:
            event = self._events.get(timeout=remaining)
        except queue.Empty as exception:
            raise InfrastructureError(
                "timed out waiting for the configured client pool"
            ) from exception
        if event.kind == "error":
            endpoint = self._actors[event.actor_index].endpoint.address
            detail = str(event.failure) if event.failure is not None else "unknown actor failure"
            raise InfrastructureError(f"configured client {endpoint} disappeared: {detail}")
        return event

    def _transition(self, event: _Event) -> Transition:
        if event.previous is None or event.observation is None or event.action is None:
            raise InfrastructureError("client step event is incomplete")
        self._actors[event.actor_index].latest_observation = event.observation
        self._record_step(event)
        endpoint = self._actors[event.actor_index].endpoint.address
        return Transition(
            actor_index=event.actor_index,
            endpoint=endpoint,
            cycle=event.cycle,
            observation=event.previous,
            action=event.action,
            reward=event.reward,
            next_observation=event.observation,
            done=event.terminated or event.truncated,
            info=event.info,
            action_latency_ms=event.latency_ms,
        )

    def _record_step(self, event: _Event) -> None:
        self._latencies[event.actor_index].append(event.latency_ms)
        self._client_ticks[event.actor_index].append(int(event.info["client_tick_delta"]))
        self._server_ticks[event.actor_index].append(int(event.info["server_tick_delta"]))
        self._transition_counts[event.actor_index] += 1

    def _episode_metrics(self, event: _Event, accumulator: _EpisodeAccumulator) -> EpisodeMetrics:
        endpoint = self._actors[event.actor_index].endpoint
        return EpisodeMetrics(
            seed=accumulator.seed,
            success=bool(event.info["success"]),
            terminal_reason=str(event.info["terminal_reason"]),
            return_value=accumulator.total_return,
            completion_ticks=int(event.info["elapsed_ticks"]),
            jump_requests=int(event.info["jump_requests"]),
            mean_client_ticks_per_action=fmean(accumulator.client_deltas),
            max_client_ticks_per_action=max(accumulator.client_deltas),
            mean_server_ticks_per_action=fmean(accumulator.server_deltas),
            max_server_ticks_per_action=max(accumulator.server_deltas),
            client_index=event.actor_index,
            client_ordinal=endpoint.ordinal,
            endpoint=endpoint.address,
            action_latency_ms_p95=float(np.percentile(accumulator.latencies, 95)),
            action_latency_ms_p99=float(np.percentile(accumulator.latencies, 99)),
        )

    def _evaluation_report(
        self,
        policy_id: str,
        suite: str,
        results: list[EpisodeMetrics],
        elapsed_seconds: float,
        all_latencies: list[float],
    ) -> EvaluationReport:
        successful = [episode for episode in results if episode.success]
        client_deltas = [episode.mean_client_ticks_per_action for episode in results]
        server_deltas = [episode.mean_server_ticks_per_action for episode in results]
        per_client: dict[str, Any] = {}
        for endpoint in self.endpoints:
            episodes = [episode for episode in results if episode.client_index == endpoint.index]
            per_client[endpoint.address] = {
                "index": endpoint.index,
                "ordinal": endpoint.ordinal,
                "episode_count": len(episodes),
                "seeds": [episode.seed for episode in episodes],
                "success_count": sum(episode.success for episode in episodes),
            }
        action_count = sum(episode.completion_ticks for episode in results)
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
            mean_client_ticks_per_action=fmean(client_deltas),
            max_client_ticks_per_action=max(
                episode.max_client_ticks_per_action for episode in results
            ),
            mean_server_ticks_per_action=fmean(server_deltas),
            max_server_ticks_per_action=max(
                episode.max_server_ticks_per_action for episode in results
            ),
            action_latency_ms=_percentiles(all_latencies),
            throughput_transitions_per_second=(
                action_count / elapsed_seconds if elapsed_seconds > 0 else 0.0
            ),
            per_client=per_client,
        )

    @staticmethod
    def _validate_actions(actions: NDArray[np.int64], expected: int) -> NDArray[np.int64]:
        flattened = np.asarray(actions, dtype=np.int64).reshape(-1)
        if len(flattened) != expected or not np.isin(flattened, [NOOP, JUMP]).all():
            raise InfrastructureError("batched policy returned invalid actions")
        return flattened


def _mean(values: list[int]) -> float | None:
    return fmean(values) if values else None


def _percentiles(values: list[float] | list[int]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    return {
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": max(values),
    }
