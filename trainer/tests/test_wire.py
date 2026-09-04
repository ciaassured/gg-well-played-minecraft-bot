from collections import deque
from typing import Any

import pytest
from yrush.v1 import yrush_pb2 as pb

from yrush_trainer.config import PROTOCOL_VERSION, VOXEL_FEATURES
from yrush_trainer.errors import ProtocolStateError
from yrush_trainer.wire import YRushConnection


class Transport:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = deque(messages)
        self.sent: list[Any] = []
        self.timeout: float | None = 1.0

    def send(self, message: Any) -> None:
        self.sent.append(message)

    def receive(self) -> Any:
        return self.messages.popleft()

    def set_timeout(self, timeout: float | None) -> float | None:
        previous = self.timeout
        self.timeout = timeout
        return previous

    def close(self) -> None:
        return None


def envelope(**payload: Any) -> Any:
    return pb.WireMessage(protocol_version=PROTOCOL_VERSION, **payload)


def hello() -> list[Any]:
    return [
        envelope(
            connection_hello=pb.ConnectionHello(
                protocol_version=PROTOCOL_VERSION,
                session_id="session",
                client_nonce="nonce",
                client_tick=1,
            )
        ),
        envelope(
            connection_ready=pb.ConnectionReady(
                protocol_version=PROTOCOL_VERSION,
                session_id="session",
                minecraft_version="26.2",
                player_uuid="player-0",
                player_name="player-zero",
                client_tick=2,
            )
        ),
    ]


def observation(sequence: int, phase: int = pb.ROUND_PHASE_ACTIVE) -> Any:
    return pb.Observation(
        protocol_version=PROTOCOL_VERSION,
        session_id="session",
        round_sequence=1,
        policy_version=0,
        client_tick=10 + sequence * 4,
        observation_sequence=sequence,
        action_sequence=sequence,
        phase=phase,
        block_properties=bytes([0]) * VOXEL_FEATURES,
        signed_target_height_difference=10.0 - sequence,
        fractional_x=0.1,
        fractional_y=0.2,
        fractional_z=0.3,
        grounded=True,
        remaining_time_fraction=0.9,
        health_fraction=1.0,
        air_fraction=1.0,
        active_players=2,
        total_players=2,
    )


def test_handshake_arm_and_acknowledged_action_ordering() -> None:
    action = (2, 1, 1, 0, 2, 2)
    transport = Transport(
        [
            *hello(),
            envelope(
                episode_ready=pb.EpisodeReady(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=3,
                    session_id="session",
                    round_sequence=1,
                    policy_version=0,
                    client_tick=10,
                    direction=pb.ROUND_DIRECTION_UP,
                    target_y=80,
                    active_players=2,
                    total_players=2,
                    action_hold_ticks=4,
                )
            ),
            envelope(observation=observation(0)),
            envelope(
                action_applied=pb.ActionApplied(
                    protocol_version=PROTOCOL_VERSION,
                    session_id="session",
                    round_sequence=1,
                    policy_version=0,
                    client_tick=11,
                    observation_sequence=0,
                    action_sequence=1,
                    action=action,
                    hold_ticks=4,
                )
            ),
            envelope(observation=observation(1)),
        ]
    )
    connection = YRushConnection(transport)
    initial = connection.arm(request_id=3, round_sequence=1, policy_version=0)
    exchange = connection.step(initial, 1, action)
    assert exchange.action_applied
    assert exchange.observation.observation_sequence == 1
    assert transport.sent[0].WhichOneof("payload") == "arm_episode"
    assert transport.sent[1].WhichOneof("payload") == "action_request"


def test_version_and_partial_action_are_rejected() -> None:
    wrong = hello()
    wrong[0].protocol_version = 9
    with pytest.raises(ProtocolStateError, match="version"):
        YRushConnection(Transport(wrong))
    connection = YRushConnection(Transport(hello()))
    with pytest.raises(ValueError, match="MultiDiscrete"):
        connection.step(observation(0), 1, (1, 2))


def test_terminal_before_full_action_is_not_a_learning_transition() -> None:
    terminal = observation(0, pb.ROUND_PHASE_COMPLETE)
    terminal.client_tick = 12
    result = pb.EpisodeResult(
        protocol_version=PROTOCOL_VERSION,
        session_id="session",
        round_sequence=1,
        policy_version=0,
        client_tick=12,
        observation_sequence=0,
        outcome=pb.PLAYER_OUTCOME_ELIMINATED,
        participant_count=2,
        completion_time_seconds=0.1,
        best_remaining_target_distance=10.0,
    )
    transport = Transport(
        [
            *hello(),
            envelope(
                episode_ready=pb.EpisodeReady(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=3,
                    session_id="session",
                    round_sequence=1,
                    policy_version=0,
                    client_tick=10,
                    direction=pb.ROUND_DIRECTION_UP,
                    target_y=80,
                    active_players=2,
                    total_players=2,
                    action_hold_ticks=4,
                )
            ),
            envelope(observation=observation(0)),
            envelope(observation=terminal),
            envelope(episode_result=result),
        ]
    )
    connection = YRushConnection(transport)
    initial = connection.arm(request_id=3, round_sequence=1, policy_version=0)
    exchange = connection.step(initial, 1, (2, 1, 1, 0, 2, 2))
    assert exchange.result is not None
    assert exchange.result.outcome == pb.PLAYER_OUTCOME_ELIMINATED
    assert not exchange.action_applied
