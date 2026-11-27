#ifndef YUKI_NATIVE_RUNNER_PROTOCOL_H
#define YUKI_NATIVE_RUNNER_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define YUKI_PROTOCOL_NONCE_SIZE 16u
#define YUKI_PROTOCOL_PACKET_SIZE 24u

enum yuki_role {
    YUKI_ROLE_SUPERVISOR = 1,
    YUKI_ROLE_HELPER = 2
};

enum yuki_message {
    YUKI_MSG_BOOTSTRAP_READY = 1,
    YUKI_MSG_AUTHORIZE_USERNS = 2,
    YUKI_MSG_USERNS_READY = 3,
    YUKI_MSG_AUTHORIZE_ISOLATION = 4,
    YUKI_MSG_ISOLATION_READY = 5,
    YUKI_MSG_AUTHORIZE_EXEC = 6,
    YUKI_MSG_ABORT = 7
};

enum yuki_phase {
    YUKI_PHASE_START = 0,
    YUKI_PHASE_BOOTSTRAP_READY,
    YUKI_PHASE_USERNS_AUTHORIZED,
    YUKI_PHASE_USERNS_READY,
    YUKI_PHASE_ISOLATION_AUTHORIZED,
    YUKI_PHASE_ISOLATION_READY,
    YUKI_PHASE_EXEC_AUTHORIZED,
    YUKI_PHASE_ABORTED
};

enum yuki_protocol_result {
    YUKI_PROTOCOL_OK = 0,
    YUKI_PROTOCOL_INVALID_ARGUMENT,
    YUKI_PROTOCOL_BAD_PACKET,
    YUKI_PROTOCOL_BAD_NONCE,
    YUKI_PROTOCOL_UNKNOWN_MESSAGE,
    YUKI_PROTOCOL_UNEXPECTED_FD,
    YUKI_PROTOCOL_BAD_TRANSITION,
    YUKI_PROTOCOL_BAD_PEER
};

struct yuki_protocol_state {
    enum yuki_role local_role;
    enum yuki_phase phase;
    uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE];
};

/* Initializes a local state machine. No transport or syscalls are performed. */
enum yuki_protocol_result
yuki_protocol_init(struct yuki_protocol_state *state,
                   enum yuki_role local_role,
                   const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE]);

/* Decodes exactly one 24-byte network-order header and validates its nonce. */
enum yuki_protocol_result
yuki_protocol_decode(const uint8_t *packet, size_t packet_len,
                     const uint8_t expected_nonce[YUKI_PROTOCOL_NONCE_SIZE],
                     enum yuki_message *message);

/* Applies a message sent by the remote peer; ancillary FDs must be zero. */
enum yuki_protocol_result
yuki_protocol_receive(struct yuki_protocol_state *state,
                      const uint8_t *packet, size_t packet_len,
                      size_t ancillary_fd_count);

/* Applies a local outbound message and encodes its 24-byte packet. */
enum yuki_protocol_result
yuki_protocol_send(struct yuki_protocol_state *state,
                   enum yuki_message message,
                   uint8_t packet_out[YUKI_PROTOCOL_PACKET_SIZE]);

#endif
