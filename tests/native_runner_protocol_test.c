#include "../evaluation/native_runner_protocol.h"

#include <assert.h>
#include <stdint.h>
#include <string.h>

static const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE] = {
    '0','1','2','3','4','5','6','7','8','9','a','b','c','d','e','f'
};

static void exchange(struct yuki_protocol_state *sender,
                     struct yuki_protocol_state *receiver,
                     enum yuki_message message)
{
    uint8_t packet[YUKI_PROTOCOL_PACKET_SIZE];
    assert(yuki_protocol_send(sender, message, packet) == YUKI_PROTOCOL_OK);
    assert(yuki_protocol_receive(receiver, packet, sizeof(packet), 0u) ==
           YUKI_PROTOCOL_OK);
}

int main(void)
{
    struct yuki_protocol_state supervisor;
    struct yuki_protocol_state helper;
    uint8_t packet[YUKI_PROTOCOL_PACKET_SIZE];
    uint8_t malformed[YUKI_PROTOCOL_PACKET_SIZE];
    enum yuki_message decoded;

    assert(yuki_protocol_init(&supervisor, YUKI_ROLE_SUPERVISOR, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_protocol_init(&helper, YUKI_ROLE_HELPER, nonce) ==
           YUKI_PROTOCOL_OK);

    exchange(&helper, &supervisor, YUKI_MSG_BOOTSTRAP_READY);
    exchange(&supervisor, &helper, YUKI_MSG_AUTHORIZE_USERNS);
    exchange(&helper, &supervisor, YUKI_MSG_USERNS_READY);
    exchange(&supervisor, &helper, YUKI_MSG_AUTHORIZE_ISOLATION);
    exchange(&helper, &supervisor, YUKI_MSG_ISOLATION_READY);
    exchange(&supervisor, &helper, YUKI_MSG_AUTHORIZE_EXEC);
    assert(supervisor.phase == YUKI_PHASE_EXEC_AUTHORIZED);
    assert(helper.phase == YUKI_PHASE_EXEC_AUTHORIZED);

    assert(yuki_protocol_send(&helper, YUKI_MSG_ABORT, packet) ==
           YUKI_PROTOCOL_BAD_TRANSITION);

    assert(yuki_protocol_init(&supervisor, YUKI_ROLE_SUPERVISOR, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_protocol_init(&helper, YUKI_ROLE_HELPER, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_protocol_send(&helper, YUKI_MSG_BOOTSTRAP_READY, packet) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_protocol_receive(&supervisor, packet, sizeof(packet), 1u) ==
           YUKI_PROTOCOL_UNEXPECTED_FD);

    memcpy(malformed, packet, sizeof(malformed));
    malformed[4] = 2u;
    assert(yuki_protocol_decode(malformed, sizeof(malformed), nonce, &decoded) ==
           YUKI_PROTOCOL_BAD_PACKET);
    memcpy(malformed, packet, sizeof(malformed));
    malformed[5] = 255u;
    assert(yuki_protocol_decode(malformed, sizeof(malformed), nonce, &decoded) ==
           YUKI_PROTOCOL_UNKNOWN_MESSAGE);
    memcpy(malformed, packet, sizeof(malformed));
    malformed[7] = 1u;
    assert(yuki_protocol_decode(malformed, sizeof(malformed), nonce, &decoded) ==
           YUKI_PROTOCOL_BAD_PACKET);
    memcpy(malformed, packet, sizeof(malformed));
    malformed[8] ^= 1u;
    assert(yuki_protocol_decode(malformed, sizeof(malformed), nonce, &decoded) ==
           YUKI_PROTOCOL_BAD_NONCE);
    assert(yuki_protocol_decode(packet, sizeof(packet) - 1u, nonce, &decoded) ==
           YUKI_PROTOCOL_BAD_PACKET);

    return 0;
}
