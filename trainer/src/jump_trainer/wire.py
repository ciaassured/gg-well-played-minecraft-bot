"""Length-prefixed loopback transport and strict benchmark message sequencing."""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from jump.v1 import jump_pb2 as pb

from jump_trainer.config import PROTOCOL_VERSION
from jump_trainer.errors import InfrastructureError, ProtocolStateError, ProtocolTimeout
from jump_trainer.messages import RawObservation

MAX_MESSAGE_BYTES = 1024 * 1024


class MessageTransport(Protocol):
    def send(self, message: Any) -> None: ...

    def receive(self) -> Any: ...

    def close(self) -> None: ...


class SocketMessageTransport:
    """Four-byte unsigned big-endian framing around one WireMessage."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection

    @classmethod
    def connect(cls, host: str, port: int, timeout: float) -> SocketMessageTransport:
        try:
            connection = socket.create_connection((host, port), timeout=timeout)
            connection.settimeout(timeout)
        except TimeoutError as exception:
            raise ProtocolTimeout(f"timed out connecting to Fabric at {host}:{port}") from exception
        except OSError as exception:
            raise InfrastructureError(
                f"cannot connect to Fabric at {host}:{port}: {exception}"
            ) from exception
        return cls(connection)

    def send(self, message: Any) -> None:
        payload = message.SerializeToString()
        if not payload or len(payload) > MAX_MESSAGE_BYTES:
            raise ProtocolStateError("outgoing Protobuf frame is empty or exceeds 1 MiB")
        try:
            self._connection.sendall(struct.pack(">I", len(payload)) + payload)
        except TimeoutError as exception:
            raise ProtocolTimeout("timed out sending a Protobuf frame") from exception
        except OSError as exception:
            raise InfrastructureError(f"Fabric transport send failed: {exception}") from exception

    def receive(self) -> Any:
        header = self._read_exact(4)
        size = struct.unpack(">I", header)[0]
        if size <= 0 or size > MAX_MESSAGE_BYTES:
            raise ProtocolStateError(f"invalid Protobuf frame length: {size}")
        payload = self._read_exact(size)
        message = pb.WireMessage()
        try:
            message.ParseFromString(payload)
        except Exception as exception:
            raise ProtocolStateError("Fabric sent invalid Protobuf") from exception
        return message

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            try:
                chunk = self._connection.recv(size - len(chunks))
            except TimeoutError as exception:
                raise ProtocolTimeout("timed out waiting for Fabric") from exception
            except OSError as exception:
                raise InfrastructureError(
                    f"Fabric transport receive failed: {exception}"
                ) from exception
            if not chunk:
                raise InfrastructureError("Fabric closed the trainer connection")
            chunks.extend(chunk)
        return bytes(chunks)

    def close(self) -> None:
        with suppress(OSError):
            self._connection.close()


class BenchmarkConnection:
    """One validated Python-to-Fabric session."""

    def __init__(
        self,
        transport: MessageTransport,
        expected_mode: int = pb.CLIENT_MODE_TRAINING,
    ) -> None:
        self._transport = transport
        self.expected_mode = expected_mode
        self.session_id = ""
        self.client_tick = 0
        self.server_tick = 0
        self.current_episode_id = 0
        self._closed = False
        self._handshake()

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        timeout: float,
        expected_mode: int = pb.CLIENT_MODE_TRAINING,
    ) -> BenchmarkConnection:
        return cls(SocketMessageTransport.connect(host, port, timeout), expected_mode)

    def _handshake(self) -> None:
        hello: Any | None = None
        ready: Any | None = None
        while hello is None or ready is None:
            message = self._receive()
            case = message.WhichOneof("payload")
            if case == "connection_hello":
                hello = message.connection_hello
                self._require_version(hello.protocol_version, "connection hello")
                if not hello.session_id or hello.mode != self.expected_mode:
                    raise ProtocolStateError("Fabric hello has the wrong session or client mode")
                self.session_id = str(hello.session_id)
                self.client_tick = int(hello.client_tick)
            elif case == "connection_ready":
                ready = message.connection_ready
                self._require_version(ready.protocol_version, "connection ready")
            else:
                raise ProtocolStateError(f"unexpected {case or 'empty'} message during handshake")

        if (
            ready.session_id != self.session_id
            or ready.mode != self.expected_mode
            or ready.minecraft_version != "26.2"
        ):
            raise ProtocolStateError("Paper acknowledgement does not match the Fabric session")
        self.client_tick = max(self.client_tick, int(ready.client_tick))
        self.server_tick = int(ready.server_tick)

    def reset(
        self,
        request_id: int,
        episode_id: int,
        seed: int,
        retries: int,
    ) -> RawObservation:
        request = pb.ResetRequest(
            protocol_version=PROTOCOL_VERSION,
            request_id=request_id,
            session_id=self.session_id,
            episode_id=episode_id,
            seed=seed,
            client_tick=self.client_tick,
        )
        envelope = pb.WireMessage(protocol_version=PROTOCOL_VERSION, reset_request=request)
        ready: Any | None = None
        observation: RawObservation | None = None

        for attempt in range(retries):
            self._transport.send(envelope)
            try:
                while ready is None or observation is None:
                    message = self._receive()
                    case = message.WhichOneof("payload")
                    if case == "episode_ready":
                        candidate = message.episode_ready
                        self._validate_ready(candidate, request_id, episode_id, seed)
                        ready = candidate
                        self.client_tick = max(self.client_tick, int(candidate.client_tick))
                        self.server_tick = max(self.server_tick, int(candidate.initial_server_tick))
                    elif case == "observation":
                        candidate_observation = RawObservation.from_proto(message.observation)
                        self._validate_initial_observation(candidate_observation, episode_id)
                        observation = candidate_observation
                    elif case in {"connection_hello", "connection_ready"}:
                        continue
                    else:
                        raise ProtocolStateError(
                            f"unexpected {case or 'empty'} message while resetting"
                        )
            except ProtocolTimeout:
                if attempt + 1 >= retries:
                    raise
                continue
            break

        if ready is None or observation is None:
            raise ProtocolStateError("reset completed without readiness and initial observation")
        self.current_episode_id = episode_id
        self.client_tick = observation.client_tick
        self.server_tick = observation.server_tick
        return observation

    def step(self, previous: RawObservation, action_sequence: int, action: int) -> RawObservation:
        if action not in {pb.ACTION_NOOP, pb.ACTION_JUMP}:
            raise ValueError("wire action must be NOOP or JUMP")
        request = pb.ActionRequest(
            protocol_version=PROTOCOL_VERSION,
            session_id=self.session_id,
            episode_id=previous.episode_id,
            client_tick=previous.client_tick,
            server_tick=previous.server_tick,
            observation_sequence=previous.observation_sequence,
            action_sequence=action_sequence,
            action=action,
        )
        self._transport.send(
            pb.WireMessage(protocol_version=PROTOCOL_VERSION, action_request=request)
        )
        applied = False
        while True:
            message = self._receive()
            case = message.WhichOneof("payload")
            if case == "action_applied":
                acknowledgement = message.action_applied
                self._validate_applied(acknowledgement, request)
                applied = True
                self.client_tick = int(acknowledgement.client_tick)
                self.server_tick = max(self.server_tick, int(acknowledgement.server_tick))
            elif case == "observation":
                if not applied:
                    raise ProtocolStateError(
                        "observation arrived before its action acknowledgement"
                    )
                observation = RawObservation.from_proto(message.observation)
                self._validate_step_observation(observation, request)
                self.client_tick = observation.client_tick
                self.server_tick = observation.server_tick
                return observation
            elif case in {"connection_hello", "connection_ready"}:
                continue
            else:
                raise ProtocolStateError(f"unexpected {case or 'empty'} message while stepping")

    def _receive(self) -> Any:
        message = self._transport.receive()
        self._require_version(message.protocol_version, "wire envelope")
        case = message.WhichOneof("payload")
        if case == "error":
            error = message.error
            raise InfrastructureError(
                f"Fabric/Paper protocol error {pb.ErrorCode.Name(error.code)}: {error.message}"
            )
        if case == "shutdown":
            raise InfrastructureError(f"benchmark service shut down: {message.shutdown.reason}")
        return message

    def shutdown(self, request_id: int, reason: str) -> None:
        if self._closed:
            return
        try:
            self._transport.send(
                pb.WireMessage(
                    protocol_version=PROTOCOL_VERSION,
                    shutdown=pb.Shutdown(
                        protocol_version=PROTOCOL_VERSION,
                        request_id=request_id,
                        session_id=self.session_id,
                        episode_id=self.current_episode_id,
                        reason=reason,
                        disconnect_minecraft=False,
                    ),
                )
            )
        except InfrastructureError:
            pass
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._transport.close()

    def _validate_ready(self, ready: Any, request_id: int, episode_id: int, seed: int) -> None:
        self._require_version(ready.protocol_version, "episode ready")
        if (
            ready.request_id != request_id
            or ready.session_id != self.session_id
            or ready.episode_id != episode_id
            or ready.seed != seed
        ):
            raise ProtocolStateError("episode ready does not match the reset request")
        if not 4.0 <= ready.starting_gap <= 8.0:
            raise ProtocolStateError("episode ready contains an invalid starting gap")

    def _validate_initial_observation(self, observation: RawObservation, episode_id: int) -> None:
        if (
            observation.session_id != self.session_id
            or observation.episode_id != episode_id
            or observation.observation_sequence != 0
            or observation.action_sequence != 0
            or observation.elapsed_ticks != 0
            or observation.phase != pb.EPISODE_PHASE_READY
            or observation.terminal_reason != pb.TERMINAL_REASON_UNSPECIFIED
        ):
            raise ProtocolStateError("initial observation violates reset invariants")

    def _validate_applied(self, acknowledgement: Any, request: Any) -> None:
        self._require_version(acknowledgement.protocol_version, "action acknowledgement")
        expected = (
            acknowledgement.session_id == request.session_id
            and acknowledgement.episode_id == request.episode_id
            and acknowledgement.observation_sequence == request.observation_sequence
            and acknowledgement.action_sequence == request.action_sequence
            and acknowledgement.requested_action == request.action
        )
        if not expected:
            raise ProtocolStateError("action acknowledgement does not match its request")

    def _validate_step_observation(self, observation: RawObservation, request: Any) -> None:
        if (
            observation.session_id != self.session_id
            or observation.episode_id != request.episode_id
            or observation.observation_sequence != request.action_sequence
            or observation.action_sequence != request.action_sequence
            or observation.elapsed_ticks <= 0
        ):
            raise ProtocolStateError("next observation violates action sequencing")
        if observation.server_tick < request.server_tick:
            raise ProtocolStateError("server tick moved backwards")

    @staticmethod
    def _require_version(version: int, description: str) -> None:
        if version != PROTOCOL_VERSION:
            raise ProtocolStateError(f"{description} uses protocol version {version}, expected 1")


ConnectionFactory = Callable[[], BenchmarkConnection]
