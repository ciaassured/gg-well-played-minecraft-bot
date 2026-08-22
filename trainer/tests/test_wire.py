from __future__ import annotations

import socket
import struct
from collections import deque
from typing import Any

import pytest
from jump.v1 import jump_pb2 as pb

from jump_trainer.errors import InfrastructureError, ProtocolStateError
from jump_trainer.wire import (
    MAX_MESSAGE_BYTES,
    BenchmarkConnection,
    SocketMessageTransport,
)


class ScriptedTransport:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = deque(messages)
        self.sent: list[Any] = []
        self.closed = False

    def send(self, message: Any) -> None:
        self.sent.append(message)

    def receive(self) -> Any:
        if not self.messages:
            raise AssertionError("scripted transport has no message")
        return self.messages.popleft()

    def close(self) -> None:
        self.closed = True


def envelope(**payload: Any) -> Any:
    return pb.WireMessage(protocol_version=1, **payload)


def handshake_messages() -> list[Any]:
    return [
        envelope(
            connection_hello=pb.ConnectionHello(
                protocol_version=1,
                session_id="test-session",
                mode=pb.CLIENT_MODE_TRAINING,
                client_tick=10,
            )
        ),
        envelope(
            connection_ready=pb.ConnectionReady(
                protocol_version=1,
                session_id="test-session",
                mode=pb.CLIENT_MODE_TRAINING,
                minecraft_version="26.2",
                client_tick=11,
                server_tick=20,
            )
        ),
    ]


def initial_messages(request_id: int, episode_id: int, seed: int) -> list[Any]:
    return [
        envelope(
            episode_ready=pb.EpisodeReady(
                protocol_version=1,
                request_id=request_id,
                session_id="test-session",
                episode_id=episode_id,
                seed=seed,
                starting_gap=6.0,
                client_tick=30,
                initial_server_tick=40,
            )
        ),
        envelope(
            observation=pb.Observation(
                protocol_version=1,
                session_id="test-session",
                episode_id=episode_id,
                client_tick=31,
                server_tick=40,
                observation_sequence=0,
                action_sequence=0,
                phase=pb.EPISODE_PHASE_READY,
                signed_wall_distance=6.0,
                on_ground=True,
                elapsed_ticks=0,
            )
        ),
    ]


def test_framing_round_trip_and_size_limit() -> None:
    left_socket, right_socket = socket.socketpair()
    left = SocketMessageTransport(left_socket)
    right = SocketMessageTransport(right_socket)
    message = envelope(
        shutdown=pb.Shutdown(protocol_version=1, session_id="session", reason="done")
    )
    left.send(message)
    assert right.receive() == message
    left_socket.sendall(struct.pack(">I", MAX_MESSAGE_BYTES + 1))
    with pytest.raises(ProtocolStateError, match="length"):
        right.receive()
    left.close()
    right.close()


def test_reset_and_step_sequences_are_validated() -> None:
    request_id = 100
    episode_id = 101
    seed = 42
    transport = ScriptedTransport(
        handshake_messages() + initial_messages(request_id, episode_id, seed)
    )
    connection = BenchmarkConnection(transport)
    initial = connection.reset(request_id, episode_id, seed, retries=1)
    assert transport.sent[-1].reset_request.seed == seed

    transport.messages.extend(
        [
            envelope(
                action_applied=pb.ActionApplied(
                    protocol_version=1,
                    session_id="test-session",
                    episode_id=episode_id,
                    client_tick=32,
                    server_tick=40,
                    observation_sequence=0,
                    action_sequence=1,
                    requested_action=pb.ACTION_JUMP,
                )
            ),
            envelope(
                observation=pb.Observation(
                    protocol_version=1,
                    session_id="test-session",
                    episode_id=episode_id,
                    client_tick=33,
                    server_tick=41,
                    observation_sequence=1,
                    action_sequence=1,
                    phase=pb.EPISODE_PHASE_TERMINAL,
                    terminal_reason=pb.TERMINAL_REASON_SUCCESS,
                    signed_wall_distance=-1.0,
                    on_ground=True,
                    elapsed_ticks=1,
                )
            ),
        ]
    )
    terminal = connection.step(initial, 1, pb.ACTION_JUMP)
    assert terminal.terminal_reason == pb.TERMINAL_REASON_SUCCESS
    assert transport.sent[-1].action_request.action_sequence == 1
    connection.shutdown(102, "test complete")
    assert transport.closed


def test_protocol_errors_are_infrastructure_failures() -> None:
    transport = ScriptedTransport(
        [
            *handshake_messages(),
            envelope(
                error=pb.ProtocolError(
                    protocol_version=1,
                    code=pb.ERROR_CODE_SEQUENCE_VIOLATION,
                    message="bad action",
                )
            ),
        ]
    )
    connection = BenchmarkConnection(transport)
    with pytest.raises(InfrastructureError, match="bad action"):
        connection.reset(1, 2, 3, retries=1)


def test_handshake_rejects_wrong_mode() -> None:
    messages = handshake_messages()
    messages[0].connection_hello.mode = pb.CLIENT_MODE_RECORDING
    with pytest.raises(ProtocolStateError, match="mode"):
        BenchmarkConnection(ScriptedTransport(messages))
