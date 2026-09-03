"""Spawned SB3 learner and read-only batched inference policy."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import random
import traceback
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray
from stable_baselines3 import DQN
from stable_baselines3.common.logger import Logger
from stable_baselines3.common.utils import polyak_update

from jump_trainer.config import TrainConfig
from jump_trainer.errors import InfrastructureError
from jump_trainer.normalization import OBSERVATION_SPACE
from jump_trainer.pool import BatchPolicy, Transition


@dataclass(frozen=True)
class ScheduleUpdate:
    previous_transitions: int
    transitions: int
    gradient_steps: int
    target_updates: int


class AggregateSchedule:
    """Client-count-independent DQN update schedule."""

    def __init__(self, learning_starts: int, train_frequency: int, target_interval: int):
        if learning_starts < 0:
            raise ValueError("learning_starts must be nonnegative")
        if train_frequency <= 0 or target_interval <= 0:
            raise ValueError("update intervals must be positive")
        self.learning_starts = learning_starts
        self.train_frequency = train_frequency
        self.target_interval = target_interval
        self.transitions = 0
        self.gradient_updates = 0
        self.target_updates = 0

    def advance(self, count: int) -> ScheduleUpdate:
        if count <= 0:
            raise ValueError("transition batch must not be empty")
        previous = self.transitions
        current = previous + count
        baseline = max(previous, self.learning_starts)
        gradient_steps = max(
            0,
            current // self.train_frequency - baseline // self.train_frequency,
        )
        target_updates = current // self.target_interval - previous // self.target_interval
        self.transitions = current
        self.gradient_updates += gradient_steps
        self.target_updates += target_updates
        return ScheduleUpdate(previous, current, gradient_steps, target_updates)


@dataclass(frozen=True)
class _TransitionsCommand:
    cycle: int
    transitions: tuple[Transition, ...]


@dataclass(frozen=True)
class _CheckpointCommand:
    token: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class _StopCommand:
    pass


@dataclass(frozen=True)
class LearnerStatus:
    kind: str
    cycle: int
    transitions: int
    gradient_updates: int
    target_updates: int
    epsilon: float
    weights: dict[str, NDArray[np.float32]] | None = None
    token: str | None = None
    detail: str | None = None


class _OfflineEnvironment(gym.Env[NDArray[np.float32], int]):
    """Spaces-only environment used to construct standard, loadable SB3 checkpoints."""

    def __init__(self) -> None:
        self.observation_space = OBSERVATION_SPACE
        self.action_space = gym.spaces.Discrete(2)

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        super().reset(seed=seed)
        del options
        shape = self.observation_space.shape
        assert shape is not None
        return np.zeros(shape, dtype=np.float32), {}

    def step(self, action: int) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        del action
        raise RuntimeError("the learner environment does not collect experience")


def _build_model(config: TrainConfig) -> DQN:
    model = DQN(
        "MlpPolicy",
        _OfflineEnvironment(),
        learning_rate=config.learning_rate,
        buffer_size=config.buffer_size,
        learning_starts=config.learning_starts,
        batch_size=config.batch_size,
        gamma=config.gamma,
        train_freq=config.train_frequency,
        gradient_steps=config.gradient_steps,
        target_update_interval=config.target_update_interval,
        exploration_fraction=config.exploration_fraction,
        exploration_initial_eps=config.exploration_initial_epsilon,
        exploration_final_eps=config.exploration_final_epsilon,
        policy_kwargs={"net_arch": [config.policy_width, config.policy_width]},
        seed=config.random_seed,
        device="cpu",
        verbose=0,
    )
    model.set_logger(Logger(folder=None, output_formats=[]))
    return model


def _weights(model: DQN) -> dict[str, NDArray[np.float32]]:
    return {
        name: value.detach().cpu().numpy().astype(np.float32, copy=True)
        for name, value in model.q_net.state_dict().items()
    }


def _save_atomic(model: DQN, destination: str) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}-{os.getpid()}.tmp.zip")
    model.save(temporary)
    os.replace(temporary, path)


def _learner_main(
    commands: Any,
    responses: Any,
    config: TrainConfig,
    untrained_checkpoint: str,
    latest_checkpoint: str,
) -> None:
    model: DQN | None = None
    schedule = AggregateSchedule(
        config.learning_starts,
        config.train_frequency,
        config.target_update_interval,
    )
    last_cycle = 0
    try:
        model = _build_model(config)
        _save_atomic(model, untrained_checkpoint)
        _save_atomic(model, latest_checkpoint)
        responses.put(
            LearnerStatus(
                "ready",
                0,
                0,
                0,
                0,
                config.exploration_initial_epsilon,
                _weights(model),
            )
        )
        while True:
            command = commands.get()
            if isinstance(command, _StopCommand):
                return
            if isinstance(command, _CheckpointCommand):
                for path in command.paths:
                    _save_atomic(model, path)
                responses.put(
                    LearnerStatus(
                        "checkpoint",
                        last_cycle,
                        schedule.transitions,
                        schedule.gradient_updates,
                        schedule.target_updates,
                        float(model.exploration_rate),
                        _weights(model),
                        token=command.token,
                    )
                )
                continue
            if not isinstance(command, _TransitionsCommand):
                raise TypeError(f"unknown learner command: {type(command).__name__}")
            if command.cycle <= last_cycle or not command.transitions:
                raise RuntimeError("learner transition cycles must be nonempty and increasing")
            assert model.replay_buffer is not None
            for transition in command.transitions:
                model.replay_buffer.add(
                    transition.observation.reshape(1, -1),
                    transition.next_observation.reshape(1, -1),
                    np.asarray([transition.action], dtype=np.int64),
                    np.asarray([transition.reward], dtype=np.float32),
                    np.asarray([transition.done], dtype=np.float32),
                    [transition.info],
                )
            update = schedule.advance(len(command.transitions))
            model.num_timesteps = update.transitions
            progress_remaining = max(0.0, 1.0 - update.transitions / config.total_timesteps)
            model.exploration_rate = model.exploration_schedule(progress_remaining)
            # SB3 refreshes the target in its per-step hook before the gradient update that
            # follows a completed train-frequency interval.
            for _ in range(update.target_updates):
                polyak_update(model.q_net.parameters(), model.q_net_target.parameters(), model.tau)
                polyak_update(model.batch_norm_stats, model.batch_norm_stats_target, 1.0)
            if update.gradient_steps:
                model.train(gradient_steps=update.gradient_steps, batch_size=config.batch_size)
            last_cycle = command.cycle
            responses.put(
                LearnerStatus(
                    "policy",
                    last_cycle,
                    schedule.transitions,
                    schedule.gradient_updates,
                    schedule.target_updates,
                    float(model.exploration_rate),
                    _weights(model),
                )
            )
    except BaseException as exception:
        if model is not None:
            with suppress(BaseException):
                _save_atomic(model, latest_checkpoint)
        responses.put(
            LearnerStatus(
                "failure",
                last_cycle,
                schedule.transitions,
                schedule.gradient_updates,
                schedule.target_updates,
                0.0,
                detail=f"{exception}\n{traceback.format_exc()}",
            )
        )
        raise


class InferencePolicy(BatchPolicy):
    """NumPy inference-only copy refreshed from the learner between action batches."""

    def __init__(self, run_seed: int, actor_indices: tuple[int, ...]):
        self._weights: dict[str, NDArray[np.float32]] = {}
        self.epsilon = 1.0
        self.version_cycle = 0
        self.version_transitions = 0
        self._random: dict[int, random.Random] = {}
        for actor_index in actor_indices:
            digest = uuid.uuid5(uuid.NAMESPACE_OID, f"{run_seed}:{actor_index}:policy").int
            self._random[actor_index] = random.Random(digest)

    def load(self, status: LearnerStatus) -> None:
        if status.weights is None:
            return
        self._weights = status.weights
        self.epsilon = status.epsilon
        self.version_cycle = status.cycle
        self.version_transitions = status.transitions

    def actions(
        self,
        actor_indices: tuple[int, ...],
        observations: NDArray[np.float32],
        *,
        deterministic: bool,
    ) -> NDArray[np.int64]:
        if not self._weights:
            raise InfrastructureError("learner has not published an inference policy")
        values = np.asarray(observations, dtype=np.float32)
        layers = sorted(
            (
                (int(name.split(".")[-2]), value, self._weights[name.replace("weight", "bias")])
                for name, value in self._weights.items()
                if name.endswith(".weight") and name.startswith("q_net.")
            ),
            key=lambda layer: layer[0],
        )
        for index, (_position, weight, bias) in enumerate(layers):
            values = values @ weight.T + bias
            if index + 1 < len(layers):
                values = np.maximum(values, 0.0)
        greedy = np.argmax(values, axis=1).astype(np.int64)
        if deterministic:
            return greedy
        for offset, actor_index in enumerate(actor_indices):
            rng = self._random[actor_index]
            if rng.random() < self.epsilon:
                greedy[offset] = rng.randrange(2)
        return greedy

    def reset(self, actor_index: int) -> None:
        del actor_index


class LearnerProcess:
    """Fail-fast parent-side controller for exactly one spawned learner."""

    def __init__(self, config: TrainConfig, untrained: Path, latest: Path, actor_count: int):
        context = mp.get_context("spawn")
        self._commands = context.Queue(maxsize=2)
        self._responses = context.Queue(maxsize=4)
        self._process = context.Process(
            target=_learner_main,
            name="jump-dqn-learner",
            args=(self._commands, self._responses, config, str(untrained), str(latest)),
        )
        self.policy = InferencePolicy(config.random_seed, tuple(range(actor_count)))
        self.latest_status: LearnerStatus | None = None
        self.max_backlog = 0
        self.max_policy_lag = 0
        self._closed = False

    def start(self, timeout: float = 120.0) -> None:
        self._process.start()
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            status = self._receive(min(1.0, deadline - monotonic()))
            if status is None:
                self._ensure_alive()
                continue
            self._accept(status)
            if status.kind == "ready":
                return
        raise InfrastructureError("learner subprocess did not initialize before its deadline")

    def submit(self, transitions: tuple[Transition, ...], cycle: int) -> None:
        self.refresh()
        self._ensure_alive()
        try:
            self._commands.put_nowait(_TransitionsCommand(cycle, transitions))
        except queue.Full as exception:
            raise InfrastructureError(
                "learner transition queue reached its two-batch limit"
            ) from exception
        self._sample_backlog()

    def after_cycle(self, cycle: int) -> None:
        self.refresh()
        self._ensure_alive()
        lag = cycle - self.policy.version_cycle
        self.max_policy_lag = max(self.max_policy_lag, lag)
        if lag > 2:
            raise InfrastructureError(
                f"learner policy lag is {lag} action cycles; maximum permitted is 2"
            )
        self._sample_backlog()

    def barrier(self, *paths: Path, timeout: float = 120.0) -> LearnerStatus:
        self.refresh()
        self._ensure_alive()
        token = uuid.uuid4().hex
        try:
            self._commands.put(
                _CheckpointCommand(token, tuple(str(path) for path in paths)), timeout=5
            )
        except queue.Full as exception:
            raise InfrastructureError(
                "learner queue did not drain for checkpoint barrier"
            ) from exception
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            status = self._receive(min(1.0, deadline - monotonic()))
            if status is None:
                self._ensure_alive()
                continue
            self._accept(status)
            if status.kind == "checkpoint" and status.token == token:
                return status
        raise InfrastructureError("learner checkpoint barrier timed out")

    def refresh(self) -> None:
        while True:
            try:
                status = self._responses.get_nowait()
            except queue.Empty:
                return
            self._accept(status)

    def metrics(self) -> dict[str, int | float]:
        status = self.latest_status
        return {
            "transitions": status.transitions if status else 0,
            "gradient_updates": status.gradient_updates if status else 0,
            "target_updates": status.target_updates if status else 0,
            "epsilon": status.epsilon if status else 1.0,
            "max_backlog_batches": self.max_backlog,
            "max_policy_lag_action_cycles": self.max_policy_lag,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.is_alive():
            with suppress(queue.Full):
                self._commands.put(_StopCommand(), timeout=2)
            self._process.join(timeout=10)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
        self._commands.close()
        self._responses.close()

    def _receive(self, timeout: float) -> LearnerStatus | None:
        if timeout <= 0:
            return None
        try:
            value: LearnerStatus = self._responses.get(timeout=timeout)
            return value
        except queue.Empty:
            return None

    def _accept(self, status: LearnerStatus) -> None:
        if status.kind == "failure":
            raise InfrastructureError(f"learner subprocess failed: {status.detail}")
        self.latest_status = status
        self.policy.load(status)

    def _ensure_alive(self) -> None:
        if not self._process.is_alive():
            self.refresh()
            raise InfrastructureError(
                f"learner subprocess exited unexpectedly with code {self._process.exitcode}"
            )

    def _sample_backlog(self) -> None:
        with suppress(NotImplementedError, OSError):
            self.max_backlog = max(self.max_backlog, int(self._commands.qsize()))

    def __enter__(self) -> LearnerProcess:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
