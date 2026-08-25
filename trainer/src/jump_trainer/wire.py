"""Length-prefixed loopback transport and strict benchmark message sequencing."""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jump.v1 import jump_pb2 as pb

from jump_trainer.config import PROTOCOL_VERSION
from jump_trainer.errors import InfrastructureError, ProtocolStateError, ProtocolTimeout
from jump_trainer.messages import RawObservation

MAX_MESSAGE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RecordingArtifact:
    request_id: int
    session_id: str
    ordinal: int
    episode_id: int
    seed: int
    recording_status: int
    terminal_reason: int
    staging_path: Path
    sha256: bytes
    size_bytes: int


@dataclass(frozen=True)
class RecordingBatch:
    request_id: int
    session_id: str
    expected_artifacts: int
    offered_artifacts: int
    retained_artifacts: int
    preserved_artifacts: int
    warnings: tuple[str, ...]
    reconnecting_minecraft: bool


ArtifactHandler = Callable[[RecordingArtifact], tuple[bool, str]]


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

    def set_timeout(self, timeout: float | None) -> float | None:
        previous = self._connection.gettimeout()
        self._connection.settimeout(timeout)
        return previous

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

    def __init__(self, transport: MessageTransport) -> None:
        self._transport = transport
        self.session_id = ""
        self.client_tick = 0
        self.server_tick = 0
        self.current_episode_id = 0
        self._episode_active = False
        self._episode_count = 0
        self._closed = False
        self._finalized = False
        self._handshake()

    @classmethod
    def connect(cls, host: str, port: int, timeout: float) -> BenchmarkConnection:
        return cls(SocketMessageTransport.connect(host, port, timeout))

    def _handshake(self) -> None:
        hello: Any | None = None
        ready: Any | None = None
        while hello is None or ready is None:
            message = self._receive()
            case = message.WhichOneof("payload")
            if case == "connection_hello":
                hello = message.connection_hello
                self._require_version(hello.protocol_version, "connection hello")
                if not hello.session_id:
                    raise ProtocolStateError("Fabric hello has a blank session id")
                self.session_id = str(hello.session_id)
                self.client_tick = int(hello.client_tick)
            elif case == "connection_ready":
                ready = message.connection_ready
                self._require_version(ready.protocol_version, "connection ready")
            else:
                raise ProtocolStateError(f"unexpected {case or 'empty'} message during handshake")

        if ready.session_id != self.session_id or ready.minecraft_version != "26.2":
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
        if self._finalized:
            raise ProtocolStateError("cannot reset after command recording finalization")
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
        self._episode_active = True
        self._episode_count += 1
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
                if observation.phase in {
                    pb.EPISODE_PHASE_TERMINAL,
                    pb.EPISODE_PHASE_ABORTED,
                }:
                    self._episode_active = False
                return observation
            elif case in {"connection_hello", "connection_ready"}:
                continue
            else:
                raise ProtocolStateError(f"unexpected {case or 'empty'} message while stepping")

    def finalize_recordings(
        self,
        request_id: int,
        *,
        interrupted: bool,
        timeout: float,
        artifact_handler: ArtifactHandler,
    ) -> RecordingBatch:
        if self._closed:
            raise InfrastructureError("cannot finalize recordings on a closed connection")
        if self._finalized:
            raise ProtocolStateError("command recordings were already finalized")
        if request_id <= 0:
            raise ValueError("finalization request id must be positive")
        if timeout <= 0 or timeout > 2**32 - 1:
            raise ValueError("recording finalization timeout is outside the supported range")
        transfer_timeout_seconds = max(1, min(2**32 - 1, round(timeout)))
        request = pb.CommandFinalize(
            protocol_version=PROTOCOL_VERSION,
            request_id=request_id,
            session_id=self.session_id,
            active_episode_id=self.current_episode_id if self._episode_active else 0,
            reason="trainer command interrupted" if interrupted else "trainer command complete",
            interrupted=interrupted,
            transfer_timeout_seconds=transfer_timeout_seconds,
        )
        self._transport.send(
            pb.WireMessage(protocol_version=PROTOCOL_VERSION, command_finalize=request)
        )
        self._finalized = True
        prior_timeout = self._set_transport_timeout(timeout)
        last_ordinal = -1
        offered_count = 0
        positive_acknowledgements = 0
        try:
            while True:
                message = self._receive()
                case = message.WhichOneof("payload")
                if case == "episode_artifact":
                    artifact = self._artifact_from_proto(
                        message.episode_artifact, request_id, last_ordinal
                    )
                    try:
                        retained, detail = artifact_handler(artifact)
                    except Exception as exception:
                        retained = False
                        detail = str(exception) or exception.__class__.__name__
                    if not retained and not detail:
                        detail = "trainer retention failed without a diagnostic"
                    acknowledgement = pb.RetentionAcknowledgement(
                        protocol_version=PROTOCOL_VERSION,
                        request_id=request_id,
                        session_id=self.session_id,
                        ordinal=artifact.ordinal,
                        episode_id=artifact.episode_id,
                        sha256=artifact.sha256,
                        retained=retained,
                        detail=detail,
                    )
                    self._transport.send(
                        pb.WireMessage(
                            protocol_version=PROTOCOL_VERSION,
                            retention_acknowledgement=acknowledgement,
                        )
                    )
                    positive_acknowledgements += int(retained)
                    last_ordinal = artifact.ordinal
                    offered_count += 1
                    continue
                if case == "batch_complete":
                    batch = self._batch_from_proto(message.batch_complete, request_id)
                    if batch.offered_artifacts != offered_count:
                        raise ProtocolStateError(
                            "recording batch offered count does not match streamed artifacts"
                        )
                    if batch.retained_artifacts != positive_acknowledgements:
                        raise ProtocolStateError(
                            "recording batch retained count does not match acknowledgements"
                        )
                    if batch.expected_artifacts != self._episode_count:
                        raise ProtocolStateError(
                            "recording batch expected count does not match command resets"
                        )
                    return batch
                if case in {
                    "connection_hello",
                    "connection_ready",
                    "episode_ready",
                    "action_applied",
                    "observation",
                }:
                    self._validate_drained_data_plane_message(message, case)
                    continue
                raise ProtocolStateError(
                    f"unexpected {case or 'empty'} message while finalizing recordings"
                )
        finally:
            self._set_transport_timeout(prior_timeout)

    def _set_transport_timeout(self, timeout: float | None) -> float | None:
        setter = getattr(self._transport, "set_timeout", None)
        if setter is None:
            return None
        previous: float | None = setter(timeout)
        return previous

    def _validate_drained_data_plane_message(self, message: Any, case: str) -> None:
        """Validate and discard data sent before Fabric handled CommandFinalize."""

        payload = getattr(message, case)
        self._require_version(payload.protocol_version, f"drained {case.replace('_', ' ')}")
        if payload.session_id != self.session_id:
            raise ProtocolStateError(
                f"drained {case.replace('_', ' ')} does not belong to the command session"
            )
        episode_id = getattr(payload, "episode_id", 0)
        if episode_id and episode_id != self.current_episode_id:
            raise ProtocolStateError(
                f"drained {case.replace('_', ' ')} does not belong to the active episode"
            )

    def _artifact_from_proto(
        self, artifact: Any, request_id: int, after_ordinal: int
    ) -> RecordingArtifact:
        self._require_version(artifact.protocol_version, "episode artifact")
        status = int(artifact.recording_status)
        reason = int(artifact.terminal_reason)
        complete_reasons = {
            pb.TERMINAL_REASON_SUCCESS,
            pb.TERMINAL_REASON_MISSED_JUMP,
            pb.TERMINAL_REASON_TIME_LIMIT,
        }
        valid_status = (
            status == pb.EPISODE_RECORDING_STATUS_COMPLETE and reason in complete_reasons
        ) or (
            status == pb.EPISODE_RECORDING_STATUS_PARTIAL
            and reason == pb.TERMINAL_REASON_INFRASTRUCTURE_ERROR
        )
        if (
            artifact.request_id != request_id
            or artifact.session_id != self.session_id
            or artifact.ordinal <= after_ordinal
            or artifact.ordinal >= self._episode_count
            or artifact.episode_id <= 0
            or not valid_status
            or not artifact.staging_path
            or artifact.size_bytes <= 0
            or len(artifact.sha256) != 32
        ):
            raise ProtocolStateError("episode artifact violates recording batch invariants")
        return RecordingArtifact(
            request_id=int(artifact.request_id),
            session_id=str(artifact.session_id),
            ordinal=int(artifact.ordinal),
            episode_id=int(artifact.episode_id),
            seed=int(artifact.seed),
            recording_status=status,
            terminal_reason=reason,
            staging_path=Path(artifact.staging_path),
            sha256=bytes(artifact.sha256),
            size_bytes=int(artifact.size_bytes),
        )

    def _batch_from_proto(self, completed: Any, request_id: int) -> RecordingBatch:
        self._require_version(completed.protocol_version, "recording batch completion")
        if (
            completed.request_id != request_id
            or completed.session_id != self.session_id
            or completed.offered_artifacts > completed.expected_artifacts
            or completed.retained_artifacts > completed.offered_artifacts
            or not completed.reconnecting_minecraft
        ):
            raise ProtocolStateError("recording batch completion violates invariants")
        return RecordingBatch(
            request_id=int(completed.request_id),
            session_id=str(completed.session_id),
            expected_artifacts=int(completed.expected_artifacts),
            offered_artifacts=int(completed.offered_artifacts),
            retained_artifacts=int(completed.retained_artifacts),
            preserved_artifacts=int(completed.preserved_artifacts),
            warnings=tuple(str(warning) for warning in completed.warnings),
            reconnecting_minecraft=bool(completed.reconnecting_minecraft),
        )

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
            raise ProtocolStateError(
                f"{description} uses protocol version {version}, expected {PROTOCOL_VERSION}"
            )


ConnectionFactory = Callable[[], BenchmarkConnection]
