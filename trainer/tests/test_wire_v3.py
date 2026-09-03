from __future__ import annotations

import socket
import struct
from collections import deque
from typing import Any

import pytest
from jump.v1 import jump_pb2 as pb

from jump_trainer.config import PROTOCOL_VERSION
from jump_trainer.errors import InfrastructureError, ProtocolStateError
from jump_trainer.wire import MAX_MESSAGE_BYTES, BenchmarkConnection, SocketMessageTransport


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
        value = self.messages.popleft()
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


def envelope(**payload: Any) -> Any:
    return pb.WireMessage(protocol_version=PROTOCOL_VERSION, **payload)


def handshake_messages(session_id: str = "test-session") -> list[Any]:
    return [
        envelope(
            connection_hello=pb.ConnectionHello(
                protocol_version=PROTOCOL_VERSION,
                session_id=session_id,
                client_tick=10,
            )
        ),
        envelope(
            connection_ready=pb.ConnectionReady(
                protocol_version=PROTOCOL_VERSION,
                session_id=session_id,
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
                protocol_version=PROTOCOL_VERSION,
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
                protocol_version=PROTOCOL_VERSION,
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
        shutdown=pb.Shutdown(
            protocol_version=PROTOCOL_VERSION,
            session_id="session",
            reason="done",
        )
    )
    left.send(message)
    assert right.receive() == message
    left_socket.sendall(struct.pack(">I", MAX_MESSAGE_BYTES + 1))
    with pytest.raises(ProtocolStateError, match="length"):
        right.receive()
    left.close()
    right.close()


def test_reset_and_step_sequences_are_validated() -> None:
    request_id, episode_id, seed = 100, 101, 42
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
                    protocol_version=PROTOCOL_VERSION,
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
                    protocol_version=PROTOCOL_VERSION,
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
    connection.shutdown(102, "test complete")
    assert transport.closed


def test_protocol_errors_are_infrastructure_failures() -> None:
    transport = ScriptedTransport(
        [
            *handshake_messages(),
            envelope(
                error=pb.ProtocolError(
                    protocol_version=PROTOCOL_VERSION,
                    code=pb.ERROR_CODE_SEQUENCE_VIOLATION,
                    message="bad action",
                )
            ),
        ]
    )
    connection = BenchmarkConnection(transport)
    with pytest.raises(InfrastructureError, match="bad action"):
        connection.reset(1, 2, 3, retries=1)


def test_failed_connection_handshake_closes_the_new_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ScriptedTransport([envelope()])
    monkeypatch.setattr(
        SocketMessageTransport,
        "connect",
        classmethod(lambda _cls, _host, _port, _timeout: transport),
    )
    with pytest.raises(ProtocolStateError, match="during handshake"):
        BenchmarkConnection.connect("client", 64123, 1.0)
    assert transport.closed


def test_protocol_v3_has_no_recording_or_capture_fields() -> None:
    assert PROTOCOL_VERSION == 3
    for name in (
        "mode",
        "capture_request",
        "capture_ready",
        "capture_complete",
        "command_finalize",
        "episode_artifact",
        "retention_acknowledgement",
        "batch_complete",
    ):
        assert name not in pb.WireMessage.DESCRIPTOR.fields_by_name
    assert not hasattr(pb, "EpisodeRecordingStatus")
    assert "disconnect_minecraft" not in pb.Shutdown.DESCRIPTOR.fields_by_name
    assert "reconnect_minecraft" not in pb.Shutdown.DESCRIPTOR.fields_by_name
