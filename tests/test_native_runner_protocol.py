from __future__ import annotations

import struct

import pytest

from evaluation.native_runner_protocol import (
    Message,
    Phase,
    ProtocolError,
    ProtocolState,
    Role,
    decode_message,
    encode_message,
)


NONCE = b"0123456789abcdef"


def exchange(sender: ProtocolState, receiver: ProtocolState, message: Message) -> None:
    receiver.receive(sender.send(message))


def test_complete_protocol_advances_both_peers_monotonically() -> None:
    supervisor = ProtocolState(Role.SUPERVISOR, NONCE)
    helper = ProtocolState(Role.HELPER, NONCE)

    exchange(helper, supervisor, Message.BOOTSTRAP_READY)
    assert supervisor.phase is helper.phase is Phase.BOOTSTRAP_READY
    exchange(supervisor, helper, Message.AUTHORIZE_USERNS)
    assert supervisor.phase is helper.phase is Phase.USERNS_AUTHORIZED
    exchange(helper, supervisor, Message.USERNS_READY)
    assert supervisor.phase is helper.phase is Phase.USERNS_READY
    exchange(supervisor, helper, Message.AUTHORIZE_ISOLATION)
    assert supervisor.phase is helper.phase is Phase.ISOLATION_AUTHORIZED
    exchange(helper, supervisor, Message.ISOLATION_READY)
    assert supervisor.phase is helper.phase is Phase.ISOLATION_READY
    exchange(supervisor, helper, Message.AUTHORIZE_EXEC)
    assert supervisor.phase is helper.phase is Phase.EXEC_AUTHORIZED



def test_wire_format_matches_fixed_cross_language_golden_vector() -> None:
    assert encode_message(Message.BOOTSTRAP_READY, NONCE).hex() == (
        "59554b490101000030313233343536373839616263646566"
    )

def test_codec_round_trip_and_exact_packet_size() -> None:
    packet = encode_message(Message.BOOTSTRAP_READY, NONCE)
    assert decode_message(packet, NONCE) is Message.BOOTSTRAP_READY
    with pytest.raises(ProtocolError):
        decode_message(packet + b"extra", NONCE)
    with pytest.raises(ProtocolError):
        decode_message(packet[:-1], NONCE)


@pytest.mark.parametrize("nonce", [b"", b"x" * 15, b"x" * 17])
def test_encoder_rejects_invalid_nonce(nonce: bytes) -> None:
    with pytest.raises(ValueError):
        encode_message(Message.ABORT, nonce)


def test_wrong_attempt_nonce_is_rejected() -> None:
    packet = encode_message(Message.BOOTSTRAP_READY, NONCE)
    with pytest.raises(ProtocolError, match="nonce"):
        decode_message(packet, b"fedcba9876543210")


def test_bad_magic_version_unknown_type_and_payload_length_are_rejected() -> None:
    packet = encode_message(Message.BOOTSTRAP_READY, NONCE)
    fields = struct.unpack("!4sBBH16s", packet)
    malformed = [
        struct.pack("!4sBBH16s", b"NOPE", fields[1], fields[2], 0, NONCE),
        struct.pack("!4sBBH16s", b"YUKI", 2, fields[2], 0, NONCE),
        struct.pack("!4sBBH16s", b"YUKI", 1, 255, 0, NONCE),
        struct.pack("!4sBBH16s", b"YUKI", 1, fields[2], 1, NONCE),
    ]
    for candidate in malformed:
        with pytest.raises(ProtocolError):
            decode_message(candidate, NONCE)


def test_role_direction_and_order_are_enforced() -> None:
    supervisor = ProtocolState(Role.SUPERVISOR, NONCE)
    helper = ProtocolState(Role.HELPER, NONCE)
    with pytest.raises(ProtocolError):
        supervisor.send(Message.AUTHORIZE_USERNS)
    with pytest.raises(ProtocolError):
        supervisor.receive(helper.send(Message.USERNS_READY))


def test_unexpected_ancillary_descriptors_are_rejected() -> None:
    supervisor = ProtocolState(Role.SUPERVISOR, NONCE)
    helper = ProtocolState(Role.HELPER, NONCE)
    packet = helper.send(Message.BOOTSTRAP_READY)
    with pytest.raises(ProtocolError, match="ancillary"):
        supervisor.receive(packet, ancillary_fd_count=1)


def test_abort_is_terminal_for_both_roles() -> None:
    supervisor = ProtocolState(Role.SUPERVISOR, NONCE)
    helper = ProtocolState(Role.HELPER, NONCE)
    supervisor.receive(helper.send(Message.ABORT))
    assert supervisor.phase is Phase.ABORTED
    with pytest.raises(ProtocolError, match="terminal"):
        supervisor.receive(encode_message(Message.BOOTSTRAP_READY, NONCE))


def test_terminal_exec_authorization_rejects_further_messages() -> None:
    supervisor = ProtocolState(Role.SUPERVISOR, NONCE)
    helper = ProtocolState(Role.HELPER, NONCE)
    exchange(helper, supervisor, Message.BOOTSTRAP_READY)
    exchange(supervisor, helper, Message.AUTHORIZE_USERNS)
    exchange(helper, supervisor, Message.USERNS_READY)
    exchange(supervisor, helper, Message.AUTHORIZE_ISOLATION)
    exchange(helper, supervisor, Message.ISOLATION_READY)
    exchange(supervisor, helper, Message.AUTHORIZE_EXEC)
    with pytest.raises(ProtocolError, match="terminal"):
        helper.receive(encode_message(Message.ABORT, NONCE))
