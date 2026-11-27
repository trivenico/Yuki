"""Reference model for the native runner's bounded control protocol.

This is a protocol specification/test oracle only. It is not a native launcher,
does not perform syscalls, and must never authorize candidate execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum, auto
import hmac
import struct


_MAGIC = b"YUKI"
_VERSION = 1
_HEADER = struct.Struct("!4sBBH16s")
_PACKET_SIZE = _HEADER.size


class ProtocolError(ValueError):
    """Malformed, unauthenticated, or out-of-order protocol input."""


class Role(Enum):
    SUPERVISOR = auto()
    HELPER = auto()


class Message(IntEnum):
    BOOTSTRAP_READY = 1
    AUTHORIZE_USERNS = 2
    USERNS_READY = 3
    AUTHORIZE_ISOLATION = 4
    ISOLATION_READY = 5
    AUTHORIZE_EXEC = 6
    ABORT = 7


class Phase(Enum):
    START = auto()
    BOOTSTRAP_READY = auto()
    USERNS_AUTHORIZED = auto()
    USERNS_READY = auto()
    ISOLATION_AUTHORIZED = auto()
    ISOLATION_READY = auto()
    EXEC_AUTHORIZED = auto()
    ABORTED = auto()


def encode_message(message: Message, nonce: bytes) -> bytes:
    """Encode one header-only SOCK_SEQPACKET message."""
    if not isinstance(message, Message):
        raise TypeError("message must be a Message")
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        raise ValueError("attempt nonce must contain exactly 16 bytes")
    return _HEADER.pack(_MAGIC, _VERSION, int(message), 0, nonce)


def decode_message(packet: bytes, expected_nonce: bytes) -> Message:
    """Decode exactly one packet; reject extensions, truncation, and bad nonce."""
    if not isinstance(packet, bytes):
        raise TypeError("packet must be bytes")
    if not isinstance(expected_nonce, bytes) or len(expected_nonce) != 16:
        raise ValueError("expected nonce must contain exactly 16 bytes")
    if len(packet) != _PACKET_SIZE:
        raise ProtocolError("control packet has an invalid length")
    magic, version, raw_message, payload_length, nonce = _HEADER.unpack(packet)
    if magic != _MAGIC or version != _VERSION:
        raise ProtocolError("control packet magic or version is unsupported")
    if payload_length != 0:
        raise ProtocolError("control packet payloads are not supported")
    if not hmac.compare_digest(nonce, expected_nonce):
        raise ProtocolError("control packet attempt nonce does not match")
    try:
        return Message(raw_message)
    except ValueError as exc:
        raise ProtocolError("control packet message type is unknown") from exc


@dataclass(slots=True)
class ProtocolState:
    """Monotonic role-aware state machine; abort is the only failure transition."""

    role: Role
    nonce: bytes
    phase: Phase = Phase.START

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            raise TypeError("role must be a Role")
        if not isinstance(self.nonce, bytes) or len(self.nonce) != 16:
            raise ValueError("attempt nonce must contain exactly 16 bytes")
        if self.phase is not Phase.START:
            raise ValueError("protocol state must start at START")

    def _apply(self, message: Message, sender: Role) -> None:
        if self.phase in (Phase.EXEC_AUTHORIZED, Phase.ABORTED):
            raise ProtocolError("protocol is already terminal")
        if message is Message.ABORT:
            self.phase = Phase.ABORTED
            return
        transitions = {
            (Phase.START, Role.HELPER, Message.BOOTSTRAP_READY): Phase.BOOTSTRAP_READY,
            (Phase.BOOTSTRAP_READY, Role.SUPERVISOR, Message.AUTHORIZE_USERNS): Phase.USERNS_AUTHORIZED,
            (Phase.USERNS_AUTHORIZED, Role.HELPER, Message.USERNS_READY): Phase.USERNS_READY,
            (Phase.USERNS_READY, Role.SUPERVISOR, Message.AUTHORIZE_ISOLATION): Phase.ISOLATION_AUTHORIZED,
            (Phase.ISOLATION_AUTHORIZED, Role.HELPER, Message.ISOLATION_READY): Phase.ISOLATION_READY,
            (Phase.ISOLATION_READY, Role.SUPERVISOR, Message.AUTHORIZE_EXEC): Phase.EXEC_AUTHORIZED,
        }
        next_phase = transitions.get((self.phase, sender, message))
        if next_phase is None:
            raise ProtocolError("message is invalid for sender role or current phase")
        self.phase = next_phase

    def send(self, message: Message) -> bytes:
        """Advance local state and encode one outbound protocol message."""
        if not isinstance(message, Message):
            raise TypeError("message must be a Message")
        self._apply(message, self.role)
        return encode_message(message, self.nonce)

    def receive(self, packet: bytes, *, ancillary_fd_count: int = 0) -> Message:
        """Validate peer direction, ancillary FD count, nonce, and phase transition."""
        if type(ancillary_fd_count) is not int or ancillary_fd_count != 0:
            raise ProtocolError("unexpected ancillary file descriptors")
        message = decode_message(packet, self.nonce)
        peer = Role.HELPER if self.role is Role.SUPERVISOR else Role.SUPERVISOR
        self._apply(message, peer)
        return message
