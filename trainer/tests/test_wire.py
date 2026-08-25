from __future__ import annotations

import socket
import struct
from collections import deque
from pathlib import Path
from typing import Any

import pytest
from jump.v1 import jump_pb2 as pb

from jump_trainer.config import PROTOCOL_VERSION
from jump_trainer.errors import InfrastructureError, ProtocolStateError, ProtocolTimeout
from jump_trainer.wire import MAX_MESSAGE_BYTES, BenchmarkConnection, SocketMessageTransport


class ScriptedTransport:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = deque(messages)
        self.sent: list[Any] = []
        self.closed = False
        self.timeout: float | None = 5.0
        self.timeout_changes: list[float | None] = []

    def send(self, message: Any) -> None:
        self.sent.append(message)

    def receive(self) -> Any:
        if not self.messages:
            raise AssertionError("scripted transport has no message")
        message = self.messages.popleft()
        if isinstance(message, BaseException):
            raise message
        return message

    def set_timeout(self, timeout: float | None) -> float | None:
        previous = self.timeout
        self.timeout = timeout
        self.timeout_changes.append(timeout)
        return previous

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
    assert transport.sent[-1].action_request.action_sequence == 1
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


def test_protocol_v2_removes_mode_and_capture_fields() -> None:
    assert PROTOCOL_VERSION == 2
    assert "mode" not in pb.ConnectionHello.DESCRIPTOR.fields_by_name
    assert "mode" not in pb.ConnectionReady.DESCRIPTOR.fields_by_name
    assert "capture_request" not in pb.WireMessage.DESCRIPTOR.fields_by_name
    assert "capture_ready" not in pb.WireMessage.DESCRIPTOR.fields_by_name
    assert "capture_complete" not in pb.WireMessage.DESCRIPTOR.fields_by_name
    assert "disconnect_minecraft" not in pb.Shutdown.DESCRIPTOR.fields_by_name
    assert "reconnect_minecraft" not in pb.Shutdown.DESCRIPTOR.fields_by_name


def test_artifacts_are_acknowledged_sequentially_before_batch_completion() -> None:
    first_digest = b"a" * 32
    second_digest = b"b" * 32
    transport = ScriptedTransport(
        [
            *handshake_messages(),
            *initial_messages(200, 201, 41),
            *initial_messages(202, 203, 42),
            envelope(
                episode_artifact=pb.EpisodeArtifact(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=204,
                    session_id="test-session",
                    ordinal=0,
                    episode_id=201,
                    seed=41,
                    recording_status=pb.EPISODE_RECORDING_STATUS_PARTIAL,
                    terminal_reason=pb.TERMINAL_REASON_INFRASTRUCTURE_ERROR,
                    staging_path="/tmp/first.mcpr",
                    sha256=first_digest,
                    size_bytes=100,
                )
            ),
            envelope(
                episode_artifact=pb.EpisodeArtifact(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=204,
                    session_id="test-session",
                    ordinal=1,
                    episode_id=203,
                    seed=42,
                    recording_status=pb.EPISODE_RECORDING_STATUS_PARTIAL,
                    terminal_reason=pb.TERMINAL_REASON_INFRASTRUCTURE_ERROR,
                    staging_path="/tmp/second.mcpr",
                    sha256=second_digest,
                    size_bytes=200,
                )
            ),
            envelope(
                batch_complete=pb.BatchComplete(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=204,
                    session_id="test-session",
                    expected_artifacts=2,
                    offered_artifacts=2,
                    retained_artifacts=1,
                    preserved_artifacts=1,
                    warnings=["second retained in staging"],
                    reconnecting_minecraft=True,
                )
            ),
        ]
    )
    connection = BenchmarkConnection(transport)
    connection.reset(200, 201, 41, retries=1)
    connection.reset(202, 203, 42, retries=1)
    handled: list[Path] = []

    def retain(artifact: Any) -> tuple[bool, str]:
        handled.append(artifact.staging_path)
        return (artifact.ordinal == 0, "copied" if artifact.ordinal == 0 else "copy failed")

    batch = connection.finalize_recordings(
        204,
        interrupted=True,
        timeout=300,
        artifact_handler=retain,
    )

    assert handled == [Path("/tmp/first.mcpr"), Path("/tmp/second.mcpr")]
    lifecycle = [message.WhichOneof("payload") for message in transport.sent[-3:]]
    assert lifecycle == [
        "command_finalize",
        "retention_acknowledgement",
        "retention_acknowledgement",
    ]
    assert transport.sent[-3].command_finalize.active_episode_id == 203
    assert transport.sent[-3].command_finalize.interrupted
    assert transport.sent[-2].retention_acknowledgement.retained
    assert not transport.sent[-1].retention_acknowledgement.retained
    assert batch.retained_artifacts == 1
    assert batch.preserved_artifacts == 1
    assert batch.warnings == ("second retained in staging",)
    assert transport.timeout_changes == [300, 5.0]


def test_finalization_timeout_is_restored() -> None:
    transport = ScriptedTransport(
        [
            *handshake_messages(),
            ProtocolTimeout("scripted finalization timeout"),
        ]
    )
    connection = BenchmarkConnection(transport)
    with pytest.raises(ProtocolTimeout, match="scripted"):
        connection.finalize_recordings(
            300,
            interrupted=False,
            timeout=17,
            artifact_handler=lambda _artifact: (True, "retained"),
        )
    assert transport.timeout_changes == [17, 5.0]


def test_finalization_drains_an_action_already_in_flight() -> None:
    digest = b"c" * 32
    transport = ScriptedTransport(
        [
            *handshake_messages(),
            *initial_messages(400, 401, 42),
            envelope(
                action_applied=pb.ActionApplied(
                    protocol_version=PROTOCOL_VERSION,
                    session_id="test-session",
                    episode_id=401,
                    client_tick=32,
                    server_tick=40,
                    observation_sequence=0,
                    action_sequence=1,
                    requested_action=pb.ACTION_NOOP,
                )
            ),
            envelope(
                observation=pb.Observation(
                    protocol_version=PROTOCOL_VERSION,
                    session_id="test-session",
                    episode_id=401,
                    client_tick=33,
                    server_tick=41,
                    observation_sequence=1,
                    action_sequence=1,
                    phase=pb.EPISODE_PHASE_ACTIVE,
                    elapsed_ticks=1,
                )
            ),
            envelope(
                episode_artifact=pb.EpisodeArtifact(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=402,
                    session_id="test-session",
                    ordinal=0,
                    episode_id=401,
                    seed=42,
                    recording_status=pb.EPISODE_RECORDING_STATUS_PARTIAL,
                    terminal_reason=pb.TERMINAL_REASON_INFRASTRUCTURE_ERROR,
                    staging_path="/tmp/partial.mcpr",
                    sha256=digest,
                    size_bytes=100,
                )
            ),
            envelope(
                batch_complete=pb.BatchComplete(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=402,
                    session_id="test-session",
                    expected_artifacts=1,
                    offered_artifacts=1,
                    retained_artifacts=1,
                    reconnecting_minecraft=True,
                )
            ),
        ]
    )
    connection = BenchmarkConnection(transport)
    connection.reset(400, 401, 42, retries=1)

    batch = connection.finalize_recordings(
        402,
        interrupted=True,
        timeout=300,
        artifact_handler=lambda _artifact: (True, "retained"),
    )

    assert batch.retained_artifacts == 1
    assert transport.sent[-1].retention_acknowledgement.retained
