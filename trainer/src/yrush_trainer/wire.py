"""Length-prefixed transport and strict trainer-to-Fabric sequencing."""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from yrush.v1 import yrush_pb2 as pb

from yrush_trainer.config import ACTION_CARDINALITIES, ACTION_HOLD_TICKS, PROTOCOL_VERSION
from yrush_trainer.errors import InfrastructureError, ProtocolStateError, ProtocolTimeout
from yrush_trainer.messages import RawObservation, RoundResult

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


@dataclass(frozen=True)
class StepExchange:
    observation: RawObservation
    result: RoundResult | None
    action_applied: bool


class YRushConnection:
    """One validated Python-to-Fabric session for a persistent YRush client."""

    def __init__(
        self,
        transport: MessageTransport,
        *,
        message_timeout: float = 10.0,
        round_timeout: float = 600.0,
    ) -> None:
        self._transport = transport
        self._message_timeout = message_timeout
        self._round_timeout = round_timeout
        self.session_id = ""
        self.player_uuid = ""
        self.player_name = ""
        self.client_tick = 0
        self.current_round_sequence = 0
        self.current_policy_version = 0
        self.current_round_direction = pb.ROUND_DIRECTION_UNSPECIFIED
        self.current_target_y = 0
        self._closed = False
        self._handshake()

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        message_timeout: float,
        round_timeout: float,
    ) -> YRushConnection:
        transport = SocketMessageTransport.connect(host, port, message_timeout)
        try:
            return cls(
                transport,
                message_timeout=message_timeout,
                round_timeout=round_timeout,
            )
        except BaseException:
            transport.close()
            raise

    def _handshake(self) -> None:
        hello: Any | None = None
        ready: Any | None = None
        while hello is None or ready is None:
            message = self._receive()
            case = message.WhichOneof("payload")
            if case == "connection_hello":
                if hello is not None or ready is not None:
                    raise ProtocolStateError("Fabric sent duplicate or reordered handshake data")
                hello = message.connection_hello
                self._require_version(hello.protocol_version, "connection hello")
                if not hello.session_id or not hello.client_nonce:
                    raise ProtocolStateError("Fabric hello identity is blank")
                self.session_id = str(hello.session_id)
                self.client_tick = int(hello.client_tick)
            elif case == "connection_ready":
                if hello is None or ready is not None:
                    raise ProtocolStateError("Fabric readiness arrived outside handshake order")
                ready = message.connection_ready
                self._require_version(ready.protocol_version, "connection ready")
            else:
                raise ProtocolStateError(f"unexpected {case or 'empty'} message during handshake")
        if (
            ready.session_id != self.session_id
            or ready.minecraft_version != "26.2"
            or not ready.player_uuid
            or not ready.player_name
        ):
            raise ProtocolStateError("Fabric readiness does not match the client session")
        self.player_uuid = str(ready.player_uuid)
        self.player_name = str(ready.player_name)
        self.client_tick = max(self.client_tick, int(ready.client_tick))

    def arm(
        self,
        *,
        request_id: int,
        round_sequence: int,
        policy_version: int,
    ) -> RawObservation:
        request = pb.ArmEpisode(
            protocol_version=PROTOCOL_VERSION,
            request_id=request_id,
            session_id=self.session_id,
            round_sequence=round_sequence,
            policy_version=policy_version,
            client_tick=self.client_tick,
        )
        self._transport.send(pb.WireMessage(protocol_version=PROTOCOL_VERSION, arm_episode=request))
        ready: Any | None = None
        initial: RawObservation | None = None
        with self._temporary_timeout(self._round_timeout):
            while ready is None or initial is None:
                message = self._receive()
                case = message.WhichOneof("payload")
                if case == "episode_ready":
                    if ready is not None:
                        raise ProtocolStateError("Fabric sent duplicate episode readiness")
                    candidate = message.episode_ready
                    self._validate_ready(candidate, request)
                    ready = candidate
                    self.client_tick = max(self.client_tick, int(candidate.client_tick))
                elif case == "observation":
                    if ready is None:
                        raise ProtocolStateError(
                            "initial observation arrived before episode readiness"
                        )
                    candidate = RawObservation.from_proto(message.observation)
                    if candidate.round_sequence < round_sequence:
                        continue
                    self._validate_initial(candidate, request)
                    initial = candidate
                elif case == "episode_result":
                    stale = RoundResult.from_proto(message.episode_result)
                    if stale.round_sequence >= round_sequence:
                        raise ProtocolStateError("armed round ended before episode readiness")
                else:
                    raise ProtocolStateError(
                        f"unexpected {case or 'empty'} message while arming a round"
                    )
        self.current_round_sequence = round_sequence
        self.current_policy_version = policy_version
        self.current_round_direction = int(ready.direction)
        self.current_target_y = int(ready.target_y)
        self.client_tick = initial.client_tick
        return initial

    def step(
        self,
        previous: RawObservation,
        action_sequence: int,
        action: Sequence[int],
    ) -> StepExchange:
        choices = tuple(int(value) for value in action)
        self._validate_action(choices)
        request = pb.ActionRequest(
            protocol_version=PROTOCOL_VERSION,
            session_id=self.session_id,
            round_sequence=previous.round_sequence,
            policy_version=previous.policy_version,
            client_tick=previous.client_tick,
            observation_sequence=previous.observation_sequence,
            action_sequence=action_sequence,
            action=choices,
        )
        self._transport.send(
            pb.WireMessage(protocol_version=PROTOCOL_VERSION, action_request=request)
        )
        applied = False
        terminal_observation: RawObservation | None = None
        result: RoundResult | None = None
        with self._temporary_timeout(self._message_timeout):
            while True:
                message = self._receive()
                case = message.WhichOneof("payload")
                if case == "action_applied":
                    if applied:
                        raise ProtocolStateError("Fabric sent a duplicate action acknowledgement")
                    self._validate_applied(message.action_applied, request)
                    applied = True
                    self.client_tick = max(
                        self.client_tick, int(message.action_applied.client_tick)
                    )
                elif case == "observation":
                    observation = RawObservation.from_proto(message.observation)
                    self._validate_observation(observation, request, applied)
                    self.client_tick = observation.client_tick
                    if observation.phase == pb.ROUND_PHASE_ACTIVE:
                        if not applied:
                            raise ProtocolStateError(
                                "active observation arrived before its action acknowledgement"
                            )
                        return StepExchange(observation, None, True)
                    terminal_observation = observation
                    if result is not None:
                        return StepExchange(terminal_observation, result, applied)
                elif case == "episode_result":
                    result = RoundResult.from_proto(message.episode_result)
                    self._validate_result(result, request, applied)
                    if terminal_observation is not None:
                        return StepExchange(terminal_observation, result, applied)
                else:
                    raise ProtocolStateError(f"unexpected {case or 'empty'} message while stepping")

    def _receive(self) -> Any:
        message = self._transport.receive()
        self._require_version(message.protocol_version, "wire envelope")
        case = message.WhichOneof("payload")
        if case == "error":
            error = message.error
            name = pb.ErrorCode.Name(error.code)
            raise InfrastructureError(f"Fabric protocol error {name}: {error.message}")
        if case == "shutdown":
            raise InfrastructureError(f"Fabric bridge shut down: {message.shutdown.reason}")
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
                        round_sequence=self.current_round_sequence,
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

    def _validate_ready(self, ready: Any, request: Any) -> None:
        self._require_version(ready.protocol_version, "episode ready")
        if (
            ready.request_id != request.request_id
            or ready.session_id != self.session_id
            or ready.round_sequence != request.round_sequence
            or ready.policy_version != request.policy_version
            or ready.action_hold_ticks != ACTION_HOLD_TICKS
            or ready.direction not in {pb.ROUND_DIRECTION_UP, pb.ROUND_DIRECTION_DOWN}
            or ready.total_players <= 0
            or not 0 <= ready.active_players <= ready.total_players
        ):
            raise ProtocolStateError("episode readiness does not match its arm request")

    def _validate_initial(self, observation: RawObservation, request: Any) -> None:
        if (
            observation.session_id != self.session_id
            or observation.round_sequence != request.round_sequence
            or observation.policy_version != request.policy_version
            or observation.observation_sequence != 0
            or observation.action_sequence != 0
            or observation.phase != pb.ROUND_PHASE_ACTIVE
        ):
            raise ProtocolStateError("initial observation violates round invariants")

    def _validate_applied(self, acknowledgement: Any, request: Any) -> None:
        self._require_version(acknowledgement.protocol_version, "action acknowledgement")
        if (
            acknowledgement.session_id != request.session_id
            or acknowledgement.round_sequence != request.round_sequence
            or acknowledgement.policy_version != request.policy_version
            or acknowledgement.observation_sequence != request.observation_sequence
            or acknowledgement.action_sequence != request.action_sequence
            or tuple(acknowledgement.action) != tuple(request.action)
            or acknowledgement.hold_ticks != ACTION_HOLD_TICKS
        ):
            raise ProtocolStateError("action acknowledgement does not match its request")

    def _validate_observation(
        self, observation: RawObservation, request: Any, applied: bool
    ) -> None:
        if (
            observation.session_id != self.session_id
            or observation.round_sequence != request.round_sequence
            or observation.policy_version != request.policy_version
        ):
            raise ProtocolStateError("observation belongs to another session, round, or policy")
        expected_sequence = request.action_sequence if applied else request.observation_sequence
        if (
            observation.observation_sequence != expected_sequence
            or observation.action_sequence != expected_sequence
        ):
            raise ProtocolStateError("observation violates action sequencing")

    def _validate_result(self, result: RoundResult, request: Any, applied: bool) -> None:
        expected_sequence = request.action_sequence if applied else request.observation_sequence
        if (
            result.session_id != self.session_id
            or result.round_sequence != request.round_sequence
            or result.policy_version != request.policy_version
            or result.observation_sequence != expected_sequence
        ):
            raise ProtocolStateError("result belongs to another session, round, or policy")

    @staticmethod
    def _validate_action(action: tuple[int, ...]) -> None:
        if len(action) != len(ACTION_CARDINALITIES) or any(
            value < 0 or value >= upper
            for value, upper in zip(action, ACTION_CARDINALITIES, strict=True)
        ):
            raise ValueError(f"action must fit MultiDiscrete{ACTION_CARDINALITIES}")

    @staticmethod
    def _require_version(version: int, description: str) -> None:
        if version != PROTOCOL_VERSION:
            raise ProtocolStateError(
                f"{description} uses protocol version {version}, expected {PROTOCOL_VERSION}"
            )

    @contextmanager
    def _temporary_timeout(self, timeout: float) -> Iterator[None]:
        setter = getattr(self._transport, "set_timeout", None)
        if setter is None:
            yield
            return
        previous = setter(timeout)
        try:
            yield
        finally:
            setter(previous)


ConnectionFactory = Callable[[], YRushConnection]
