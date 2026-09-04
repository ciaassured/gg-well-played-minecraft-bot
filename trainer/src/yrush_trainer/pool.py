"""Asynchronous fixed-client scheduler with global round boundaries."""

from __future__ import annotations

import queue
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from statistics import fmean
from time import monotonic
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from yrush_trainer.endpoints import Endpoint
from yrush_trainer.env import YRushEnv
from yrush_trainer.errors import InfrastructureError
from yrush_trainer.evaluation import GlobalRound, reconcile_round
from yrush_trainer.policy import PolicyBatch
from yrush_trainer.rollout import Transition


class BatchPolicy(Protocol):
    version: int

    def sample(self, observations: NDArray[np.float32], *, deterministic: bool) -> PolicyBatch: ...

    def values(self, observations: NDArray[np.float32]) -> NDArray[np.float32]: ...


@dataclass(frozen=True)
class _Command:
    kind: str
    round_sequence: int = 0
    policy_version: int = 0
    action: NDArray[np.int64] | None = None
    log_probability: float = 0.0
    value_estimate: float = 0.0
    episode_start: bool = False


@dataclass(frozen=True)
class _Event:
    actor_index: int
    kind: str
    observation: NDArray[np.float32] | None = None
    previous: NDArray[np.float32] | None = None
    action: NDArray[np.int64] | None = None
    reward: float = 0.0
    terminated: bool = False
    truncated: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    log_probability: float = 0.0
    value_estimate: float = 0.0
    episode_start: bool = False
    latency_ms: float = 0.0
    failure: BaseException | None = None


EnvironmentFactory = Callable[[Endpoint], YRushEnv]
TransitionSink = Callable[[Transition], None]
RoundSink = Callable[[GlobalRound], None]
BoundaryCallback = Callable[[GlobalRound, BatchPolicy], tuple[BatchPolicy, bool]]


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
        self.stopping = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"yrush-client-actor-{endpoint.index}",
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
        env: YRushEnv | None = None
        observation: NDArray[np.float32] | None = None
        last_failure: BaseException | None = None
        try:
            env = self.environment_factory(self.endpoint)
            while not self.stopping.is_set() and monotonic() < self.startup_deadline:
                try:
                    env.connect()
                    self.output.put(
                        _Event(
                            self.endpoint.index,
                            "ready",
                            info={
                                "player_uuid": env.player_uuid,
                                "player_name": env.player_name,
                            },
                        )
                    )
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
                if command.kind == "arm":
                    observation, info = env.reset(
                        options={
                            "round_sequence": command.round_sequence,
                            "policy_version": command.policy_version,
                        }
                    )
                    self.output.put(
                        _Event(
                            self.endpoint.index,
                            "armed",
                            observation=np.asarray(observation, dtype=np.float32).copy(),
                            info=dict(info),
                            episode_start=True,
                        )
                    )
                    continue
                if command.kind != "action" or command.action is None:
                    raise AssertionError(f"invalid actor command: {command.kind}")
                if observation is None:
                    raise RuntimeError("actor action has no active observation")
                previous = observation
                started = monotonic()
                observation, reward, terminated, truncated, info = env.step(command.action)
                self.output.put(
                    _Event(
                        self.endpoint.index,
                        "step",
                        observation=np.asarray(observation, dtype=np.float32).copy(),
                        previous=previous,
                        action=command.action.copy(),
                        reward=float(reward),
                        terminated=bool(terminated),
                        truncated=bool(truncated),
                        info=dict(info),
                        log_probability=command.log_probability,
                        value_estimate=command.value_estimate,
                        episode_start=command.episode_start,
                        latency_ms=(monotonic() - started) * 1000.0,
                    )
                )
                if terminated or truncated:
                    observation = None
        except BaseException as exception:
            if not self.stopping.is_set():
                self.output.put(_Event(self.endpoint.index, "error", failure=exception))
        finally:
            if env is not None:
                env.close()


@dataclass(frozen=True)
class DriveResult:
    rounds: tuple[GlobalRound, ...]
    valid_transitions: int
    discarded_transitions: int
    elapsed_seconds: float


class ClientPool:
    """Fail-fast pool: every configured endpoint must remain connected."""

    def __init__(
        self,
        endpoints: tuple[Endpoint, ...],
        *,
        startup_timeout: float,
        message_timeout: float,
        round_timeout: float,
        environment_factory: EnvironmentFactory | None = None,
    ) -> None:
        if not endpoints:
            raise ValueError("client pool must contain at least one endpoint")
        if min(startup_timeout, message_timeout, round_timeout) <= 0.0:
            raise ValueError("pool timeouts must be positive")
        self.endpoints = endpoints
        self.startup_timeout = startup_timeout
        self._events: queue.Queue[_Event] = queue.Queue()
        self._latencies: dict[int, list[float]] = defaultdict(list)
        self._client_ticks: dict[int, list[int]] = defaultdict(list)
        self._action_counts: list[Counter[int]] = [Counter() for _ in range(6)]
        self._valid_transitions = 0
        self._discarded_transitions = 0
        self._round_sequence = 0
        self._started_at = monotonic()
        self.identities: dict[int, dict[str, str]] = {}
        selected_factory = environment_factory
        if selected_factory is None:

            def selected_factory(endpoint: Endpoint) -> YRushEnv:
                return YRushEnv(
                    host=endpoint.host,
                    port=endpoint.port,
                    message_timeout=message_timeout,
                    round_timeout=round_timeout,
                )

        deadline = monotonic() + startup_timeout
        self._actors = {
            endpoint.index: _Actor(endpoint, self._events, selected_factory, deadline)
            for endpoint in endpoints
        }
        self._closed = False
        self._started = False

    @property
    def width(self) -> int:
        return len(self.endpoints)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for actor in self._actors.values():
            actor.start()
        waiting = set(self._actors)
        deadline = monotonic() + self.startup_timeout
        try:
            while waiting:
                event = self._next_event(deadline)
                if event.kind != "ready" or event.actor_index not in waiting:
                    raise InfrastructureError("client pool emitted an invalid startup event")
                self.identities[event.actor_index] = {
                    "player_uuid": str(event.info["player_uuid"]),
                    "player_name": str(event.info["player_name"]),
                }
                waiting.remove(event.actor_index)
            uuids = [identity["player_uuid"] for identity in self.identities.values()]
            if len(set(uuids)) != self.width:
                raise InfrastructureError("fixed pool contains duplicate player identities")
        except BaseException:
            self.close()
            raise

    def drive(
        self,
        policy: BatchPolicy,
        *,
        deterministic: bool,
        rounds: int | None = None,
        transition_sink: TransitionSink | None = None,
        round_sink: RoundSink | None = None,
        boundary_callback: BoundaryCallback | None = None,
    ) -> DriveResult:
        if not self._started:
            self.start()
        if rounds is not None and rounds <= 0:
            raise ValueError("round count must be positive")
        started = monotonic()
        completed: list[GlobalRound] = []
        starting_valid = self._valid_transitions
        starting_discarded = self._discarded_transitions
        current_policy = policy
        self._round_sequence += 1
        current_round = self._round_sequence
        self._arm_all(current_round, current_policy.version)
        available: dict[int, tuple[NDArray[np.float32], bool]] = {}
        results: dict[int, dict[str, Any]] = {}

        while True:
            event = self._next_event(None)
            batch = [event]
            while True:
                try:
                    batch.append(self._events.get_nowait())
                except queue.Empty:
                    break

            step_events = [item for item in batch if item.kind == "step"]
            next_values: dict[int, float] = {}
            valid_for_values = [
                item
                for item in step_events
                if bool(item.info.get("valid_transition")) and item.observation is not None
            ]
            if valid_for_values:
                values = current_policy.values(
                    np.stack(
                        [cast(NDArray[np.float32], item.observation) for item in valid_for_values]
                    )
                )
                next_values = {
                    item.actor_index: float(value)
                    for item, value in zip(valid_for_values, values, strict=True)
                }

            for item in batch:
                if item.kind == "error":
                    failure = item.failure or InfrastructureError("client actor failed")
                    raise InfrastructureError(
                        f"fixed client {self._endpoint(item.actor_index)} departed: {failure}"
                    ) from failure
                if item.kind == "armed":
                    if int(item.info["round_sequence"]) != current_round:
                        raise InfrastructureError("client armed an unexpected round sequence")
                    if item.observation is None:
                        raise InfrastructureError("armed client supplied no observation")
                    available[item.actor_index] = (item.observation, True)
                    continue
                if (
                    item.kind != "step"
                    or item.observation is None
                    or item.previous is None
                    or item.action is None
                ):
                    raise InfrastructureError(f"unexpected client actor event: {item.kind}")
                self._latencies[item.actor_index].append(item.latency_ms)
                tick_delta = item.info.get("client_tick_delta")
                if tick_delta is not None:
                    self._client_ticks[item.actor_index].append(int(tick_delta))
                if bool(item.info.get("valid_transition")):
                    transition = Transition(
                        actor_index=item.actor_index,
                        round_sequence=current_round,
                        policy_version=current_policy.version,
                        observation=item.previous,
                        action=item.action,
                        reward=item.reward,
                        next_observation=item.observation,
                        terminated=item.terminated,
                        truncated=item.truncated,
                        episode_start=item.episode_start,
                        log_probability=item.log_probability,
                        value_estimate=item.value_estimate,
                        next_value_estimate=next_values[item.actor_index],
                    )
                    self._valid_transitions += 1
                    if transition_sink is not None:
                        transition_sink(transition)
                else:
                    self._discarded_transitions += 1
                if item.terminated or item.truncated:
                    results[item.actor_index] = item.info
                else:
                    available[item.actor_index] = (item.observation, False)

            if len(results) == self.width:
                summary = reconcile_round(
                    current_round,
                    current_policy.version,
                    results,
                    {endpoint.index: endpoint.address for endpoint in self.endpoints},
                )
                completed.append(summary)
                if round_sink is not None:
                    round_sink(summary)
                stop = rounds is not None and len(completed) >= rounds
                if boundary_callback is not None:
                    current_policy, callback_stop = boundary_callback(summary, current_policy)
                    stop = stop or callback_stop
                if stop:
                    break
                self._round_sequence += 1
                current_round = self._round_sequence
                results = {}
                available = {}
                self._arm_all(current_round, current_policy.version)

            if available:
                indices = tuple(sorted(available))
                observations = np.stack([available[index][0] for index in indices])
                sampled = current_policy.sample(observations, deterministic=deterministic)
                if sampled.actions.shape != (len(indices), 6):
                    raise InfrastructureError("policy returned an invalid action batch shape")
                for row, actor_index in enumerate(indices):
                    action = sampled.actions[row]
                    for head, value in enumerate(action):
                        self._action_counts[head][int(value)] += 1
                    self._actors[actor_index].submit(
                        _Command(
                            "action",
                            action=action,
                            log_probability=float(sampled.log_probabilities[row]),
                            value_estimate=float(sampled.values[row]),
                            episode_start=available[actor_index][1],
                        )
                    )
                available = {}

        return DriveResult(
            rounds=tuple(completed),
            valid_transitions=self._valid_transitions - starting_valid,
            discarded_transitions=self._discarded_transitions - starting_discarded,
            elapsed_seconds=monotonic() - started,
        )

    def _arm_all(self, round_sequence: int, policy_version: int) -> None:
        for actor in self._actors.values():
            actor.submit(
                _Command(
                    "arm",
                    round_sequence=round_sequence,
                    policy_version=policy_version,
                )
            )

    def _next_event(self, deadline: float | None) -> _Event:
        timeout = None if deadline is None else max(0.0, deadline - monotonic())
        try:
            event = self._events.get(timeout=timeout)
        except queue.Empty as exception:
            raise InfrastructureError("client pool startup timed out") from exception
        if event.kind == "error":
            failure = event.failure or InfrastructureError("client actor failed")
            raise InfrastructureError(
                f"fixed client {self._endpoint(event.actor_index)} failed: {failure}"
            ) from failure
        return event

    def _endpoint(self, actor_index: int) -> str:
        return next(
            endpoint.address for endpoint in self.endpoints if endpoint.index == actor_index
        )

    def stats(self) -> dict[str, Any]:
        elapsed = max(monotonic() - self._started_at, 1e-9)
        latencies = [value for values in self._latencies.values() for value in values]
        ticks = [value for values in self._client_ticks.values() for value in values]
        return {
            "client_count": self.width,
            "valid_transitions": self._valid_transitions,
            "discarded_transitions": self._discarded_transitions,
            "throughput_transitions_per_second": self._valid_transitions / elapsed,
            "mean_action_latency_ms": fmean(latencies) if latencies else 0.0,
            "mean_client_ticks_per_action": fmean(ticks) if ticks else 0.0,
            "max_client_ticks_per_action": max(ticks, default=0),
            "action_distributions": [
                {str(choice): count for choice, count in sorted(head.items())}
                for head in self._action_counts
            ],
            "identities": self.identities,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for actor in self._actors.values():
            actor.stop()
        for actor in self._actors.values():
            actor.thread.join(timeout=5.0)

    def __enter__(self) -> ClientPool:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
