#include "native_runner_protocol.h"

#include <string.h>

#define YUKI_MAGIC_0 ((uint8_t)'Y')
#define YUKI_MAGIC_1 ((uint8_t)'U')
#define YUKI_MAGIC_2 ((uint8_t)'K')
#define YUKI_MAGIC_3 ((uint8_t)'I')
#define YUKI_VERSION ((uint8_t)1)

static uint16_t read_u16_be(const uint8_t *p)
{
    return (uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

static void write_u16_be(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)(value & 0xffu);
}

static int valid_message(enum yuki_message message)
{
    return message >= YUKI_MSG_BOOTSTRAP_READY &&
           message <= YUKI_MSG_ABORT;
}

/* Avoid an early exit so nonce comparison does not reveal the mismatch offset. */
static int nonce_equal(const uint8_t *left, const uint8_t *right)
{
    uint8_t difference = 0;
    size_t i;

    for (i = 0; i < YUKI_PROTOCOL_NONCE_SIZE; ++i) {
        difference |= (uint8_t)(left[i] ^ right[i]);
    }
    return difference == 0;
}

enum yuki_protocol_result
yuki_protocol_init(struct yuki_protocol_state *state,
                   enum yuki_role local_role,
                   const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE])
{
    if (state == NULL || nonce == NULL ||
        (local_role != YUKI_ROLE_SUPERVISOR &&
         local_role != YUKI_ROLE_HELPER)) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }

    state->local_role = local_role;
    state->phase = YUKI_PHASE_START;
    memcpy(state->nonce, nonce, YUKI_PROTOCOL_NONCE_SIZE);
    return YUKI_PROTOCOL_OK;
}

enum yuki_protocol_result
yuki_protocol_decode(const uint8_t *packet, size_t packet_len,
                     const uint8_t expected_nonce[YUKI_PROTOCOL_NONCE_SIZE],
                     enum yuki_message *message)
{
    uint16_t payload_len;
    uint8_t raw_message;

    if (packet == NULL || expected_nonce == NULL || message == NULL) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    if (packet_len != YUKI_PROTOCOL_PACKET_SIZE) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    if (packet[0] != YUKI_MAGIC_0 || packet[1] != YUKI_MAGIC_1 ||
        packet[2] != YUKI_MAGIC_2 || packet[3] != YUKI_MAGIC_3 ||
        packet[4] != YUKI_VERSION) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }

    raw_message = packet[5];
    payload_len = read_u16_be(&packet[6]);
    if (payload_len != 0u) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    if (raw_message < YUKI_MSG_BOOTSTRAP_READY ||
        raw_message > YUKI_MSG_ABORT) {
        return YUKI_PROTOCOL_UNKNOWN_MESSAGE;
    }
    if (!nonce_equal(&packet[8], expected_nonce)) {
        return YUKI_PROTOCOL_BAD_NONCE;
    }

    *message = (enum yuki_message)raw_message;
    return YUKI_PROTOCOL_OK;
}

static enum yuki_protocol_result
apply_message(struct yuki_protocol_state *state,
              enum yuki_message message,
              enum yuki_role sender)
{
    enum yuki_phase next;

    if (state->phase == YUKI_PHASE_EXEC_AUTHORIZED ||
        state->phase == YUKI_PHASE_ABORTED) {
        return YUKI_PROTOCOL_BAD_TRANSITION;
    }
    if (message == YUKI_MSG_ABORT) {
        state->phase = YUKI_PHASE_ABORTED;
        return YUKI_PROTOCOL_OK;
    }

    if (state->phase == YUKI_PHASE_START &&
        sender == YUKI_ROLE_HELPER &&
        message == YUKI_MSG_BOOTSTRAP_READY) {
        next = YUKI_PHASE_BOOTSTRAP_READY;
    } else if (state->phase == YUKI_PHASE_BOOTSTRAP_READY &&
               sender == YUKI_ROLE_SUPERVISOR &&
               message == YUKI_MSG_AUTHORIZE_USERNS) {
        next = YUKI_PHASE_USERNS_AUTHORIZED;
    } else if (state->phase == YUKI_PHASE_USERNS_AUTHORIZED &&
               sender == YUKI_ROLE_HELPER &&
               message == YUKI_MSG_USERNS_READY) {
        next = YUKI_PHASE_USERNS_READY;
    } else if (state->phase == YUKI_PHASE_USERNS_READY &&
               sender == YUKI_ROLE_SUPERVISOR &&
               message == YUKI_MSG_AUTHORIZE_ISOLATION) {
        next = YUKI_PHASE_ISOLATION_AUTHORIZED;
    } else if (state->phase == YUKI_PHASE_ISOLATION_AUTHORIZED &&
               sender == YUKI_ROLE_HELPER &&
               message == YUKI_MSG_ISOLATION_READY) {
        next = YUKI_PHASE_ISOLATION_READY;
    } else if (state->phase == YUKI_PHASE_ISOLATION_READY &&
               sender == YUKI_ROLE_SUPERVISOR &&
               message == YUKI_MSG_AUTHORIZE_EXEC) {
        next = YUKI_PHASE_EXEC_AUTHORIZED;
    } else {
        return YUKI_PROTOCOL_BAD_TRANSITION;
    }

    state->phase = next;
    return YUKI_PROTOCOL_OK;
}

enum yuki_protocol_result
yuki_protocol_receive(struct yuki_protocol_state *state,
                      const uint8_t *packet, size_t packet_len,
                      size_t ancillary_fd_count)
{
    enum yuki_message message;
    enum yuki_role peer_role;
    enum yuki_protocol_result result;

    if (state == NULL) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    if (ancillary_fd_count != 0u) {
        return YUKI_PROTOCOL_UNEXPECTED_FD;
    }
    result = yuki_protocol_decode(packet, packet_len, state->nonce, &message);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }
    peer_role = state->local_role == YUKI_ROLE_SUPERVISOR
                    ? YUKI_ROLE_HELPER
                    : YUKI_ROLE_SUPERVISOR;
    return apply_message(state, message, peer_role);
}

enum yuki_protocol_result
yuki_protocol_send(struct yuki_protocol_state *state,
                   enum yuki_message message,
                   uint8_t packet_out[YUKI_PROTOCOL_PACKET_SIZE])
{
    enum yuki_protocol_result result;
    enum yuki_role sender;

    if (state == NULL || packet_out == NULL || !valid_message(message)) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    sender = state->local_role;
    result = apply_message(state, message, sender);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }

    packet_out[0] = YUKI_MAGIC_0;
    packet_out[1] = YUKI_MAGIC_1;
    packet_out[2] = YUKI_MAGIC_2;
    packet_out[3] = YUKI_MAGIC_3;
    packet_out[4] = YUKI_VERSION;
    packet_out[5] = (uint8_t)message;
    write_u16_be(&packet_out[6], 0u);
    memcpy(&packet_out[8], state->nonce, YUKI_PROTOCOL_NONCE_SIZE);
    return YUKI_PROTOCOL_OK;
}
