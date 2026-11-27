#define _GNU_SOURCE
#include "linux_runner_transport.h"

#include <errno.h>
#include <limits.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>

#define YUKI_HELPER_TIMEOUT_MS 30000

static int parse_unsigned(const char *text, unsigned long maximum,
                          unsigned long *value)
{
    char *end = NULL;
    unsigned long parsed;

    if (text == NULL || text[0] == '\0' || text[0] == '-' ||
        text[0] == '+') {
        return -1;
    }
    errno = 0;
    parsed = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed > maximum) {
        return -1;
    }
    *value = parsed;
    return 0;
}

static int hex_value(char value)
{
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    return -1;
}

static int restrict_descriptors(int control_fd)
{
    if (control_fd != 3) {
        return -1;
    }
#ifdef SYS_close_range
    /* This helper has no stdio or ambient descriptors: fd 3 is the allowlist. */
    if (syscall(SYS_close_range, 0u, 2u, 0u) != 0 ||
        syscall(SYS_close_range, 4u, UINT_MAX, 0u) != 0) {
        return -1;
    }
#else
    return -1;
#endif
    if (fcntl(control_fd, F_SETFD, FD_CLOEXEC) != 0) {
        return -1;
    }
    return 0;
}

static int parse_nonce(const char *text, uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE])
{
    size_t index;
    if (text == NULL || strlen(text) != YUKI_PROTOCOL_NONCE_SIZE * 2u) {
        return -1;
    }
    for (index = 0; index < YUKI_PROTOCOL_NONCE_SIZE; ++index) {
        int high = hex_value(text[index * 2u]);
        int low = hex_value(text[index * 2u + 1u]);
        if (high < 0 || low < 0) {
            return -1;
        }
        nonce[index] = (uint8_t)((high << 4) | low);
    }
    return 0;
}

int main(int argc, char **argv)
{
    unsigned long control_value;
    unsigned long pid_value;
    unsigned long uid_value;
    unsigned long gid_value;
    uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE];
    struct yuki_protocol_state state;
    enum yuki_protocol_result result;
    int control_fd;

    /* Arguments are supplied only by the trusted supervisor's spawn path. */
    if (argc != 6 ||
        parse_unsigned(argv[1], (unsigned long)INT_MAX, &control_value) != 0 ||
        parse_unsigned(argv[2], (unsigned long)INT_MAX, &pid_value) != 0 ||
        pid_value == 0 ||
        parse_unsigned(argv[3], (unsigned long)(uid_t)-1, &uid_value) != 0 ||
        parse_unsigned(argv[4], (unsigned long)(gid_t)-1, &gid_value) != 0 ||
        parse_nonce(argv[5], nonce) != 0) {
        return 64;
    }

    control_fd = (int)control_value;
    if (restrict_descriptors(control_fd) != 0) {
        return 65;
    }
    if (yuki_linux_transport_enable_credentials(control_fd) !=
        YUKI_PROTOCOL_OK) {
        return 65;
    }
    if (yuki_protocol_init(&state, YUKI_ROLE_HELPER, nonce) !=
        YUKI_PROTOCOL_OK) {
        return 65;
    }

    /* Announce only that this inert helper reached its protocol loop. */
    result = yuki_linux_transport_send(control_fd, &state,
                                       YUKI_MSG_BOOTSTRAP_READY,
                                       YUKI_HELPER_TIMEOUT_MS);
    if (result != YUKI_PROTOCOL_OK) {
        return 66;
    }

    /* This milestone intentionally supports abort only; no setup or exec. */
    result = yuki_linux_transport_receive(
        control_fd, &state, (pid_t)pid_value, (uid_t)uid_value,
        (gid_t)gid_value, YUKI_HELPER_TIMEOUT_MS);
    if (result != YUKI_PROTOCOL_OK) {
        return 67;
    }
    if (state.phase != YUKI_PHASE_ABORTED) {
        /* Fail closed if the supervisor requests a phase not implemented. */
        (void)yuki_linux_transport_send(control_fd, &state, YUKI_MSG_ABORT,
                                       YUKI_HELPER_TIMEOUT_MS);
        return 78;
    }

    (void)close(control_fd);
    return 0;
}
