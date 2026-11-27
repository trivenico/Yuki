#define _GNU_SOURCE
#include "linux_runner_transport.h"

#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define YUKI_CONTROL_BUFFER_SIZE 256u

static int64_t monotonic_milliseconds(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return -1;
    }
    return ((int64_t)now.tv_sec * 1000) + (now.tv_nsec / 1000000);
}

static enum yuki_protocol_result wait_ready(int fd, short events,
                                             int timeout_ms)
{
    int64_t start;
    int64_t deadline;
    struct pollfd pfd;

    if (fd < 0 || timeout_ms < 0) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    start = monotonic_milliseconds();
    if (start < 0) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    deadline = start + timeout_ms;
    pfd.fd = fd;
    pfd.events = events;
    pfd.revents = 0;

    for (;;) {
        int64_t now = monotonic_milliseconds();
        int remaining;
        int result;
        if (now < 0) {
            return YUKI_PROTOCOL_BAD_PACKET;
        }
        if (now >= deadline) {
            remaining = 0;
        } else {
            remaining = (deadline - now > 2147483647LL)
                            ? 2147483647
                            : (int)(deadline - now);
        }
        pfd.revents = 0;
        result = poll(&pfd, 1, remaining);
        if (result > 0) {
            if ((pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0 ||
                (pfd.revents & events) == 0) {
                return YUKI_PROTOCOL_BAD_PACKET;
            }
            return YUKI_PROTOCOL_OK;
        }
        if (result == 0) {
            return YUKI_PROTOCOL_BAD_PACKET;
        }
        if (errno != EINTR) {
            return YUKI_PROTOCOL_BAD_PACKET;
        }
    }
}

static enum yuki_protocol_result check_seqpacket(int fd)
{
    int socket_type = 0;
    socklen_t length = (socklen_t)sizeof(socket_type);
    if (getsockopt(fd, SOL_SOCKET, SO_TYPE, &socket_type, &length) != 0 ||
        length != sizeof(socket_type) || socket_type != SOCK_SEQPACKET) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    return YUKI_PROTOCOL_OK;
}


enum yuki_protocol_result
yuki_linux_transport_enable_credentials(int fd)
{
    int enabled = 1;
    enum yuki_protocol_result result;

    if (fd < 0) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    result = check_seqpacket(fd);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }
    if (setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled,
                   sizeof(enabled)) != 0) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    return YUKI_PROTOCOL_OK;
}

enum yuki_protocol_result
yuki_linux_transport_check_peer(int fd, pid_t expected_pid,
                                uid_t expected_uid, gid_t expected_gid)
{
    struct ucred credentials;
    socklen_t length = (socklen_t)sizeof(credentials);
    enum yuki_protocol_result result;

    if (fd < 0 || expected_pid <= 0) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    result = check_seqpacket(fd);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }
    memset(&credentials, 0, sizeof(credentials));
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &length) != 0 ||
        length != sizeof(credentials)) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    if (credentials.pid != expected_pid ||
        credentials.uid != expected_uid ||
        credentials.gid != expected_gid) {
        return YUKI_PROTOCOL_BAD_PEER;
    }
    return YUKI_PROTOCOL_OK;
}

enum yuki_protocol_result
yuki_linux_transport_send(int fd, struct yuki_protocol_state *state,
                          enum yuki_message message, int timeout_ms)
{
    uint8_t packet[YUKI_PROTOCOL_PACKET_SIZE];
    enum yuki_phase previous_phase;
    enum yuki_protocol_result result;
    ssize_t sent;

    if (state == NULL || fd < 0 || timeout_ms < 0) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    result = check_seqpacket(fd);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }
    previous_phase = state->phase;
    result = yuki_protocol_send(state, message, packet);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }
    result = wait_ready(fd, POLLOUT, timeout_ms);
    if (result != YUKI_PROTOCOL_OK) {
        state->phase = previous_phase;
        return result;
    }
    sent = send(fd, packet, sizeof(packet), MSG_NOSIGNAL | MSG_DONTWAIT);
    if (sent != (ssize_t)sizeof(packet)) {
        state->phase = previous_phase;
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    return YUKI_PROTOCOL_OK;
}

static enum yuki_protocol_result
validate_control_messages(struct msghdr *message, pid_t expected_pid,
                          uid_t expected_uid, gid_t expected_gid)
{
    struct cmsghdr *header;
    int credentials_seen = 0;
    int unexpected = 0;
    int bad_peer = 0;

    for (header = CMSG_FIRSTHDR(message); header != NULL;
         header = CMSG_NXTHDR(message, header)) {
        if (header->cmsg_level == SOL_SOCKET &&
            header->cmsg_type == SCM_RIGHTS &&
            header->cmsg_len >= CMSG_LEN(0)) {
            size_t bytes = header->cmsg_len - CMSG_LEN(0);
            size_t count = bytes / sizeof(int);
            int *descriptors = (int *)CMSG_DATA(header);
            size_t index;
            unexpected = 1;
            for (index = 0; index < count; ++index) {
                if (descriptors[index] >= 0) {
                    (void)close(descriptors[index]);
                }
            }
        } else if (header->cmsg_level == SOL_SOCKET &&
                   header->cmsg_type == SCM_CREDENTIALS &&
                   header->cmsg_len == CMSG_LEN(sizeof(struct ucred))) {
            struct ucred credentials;
            if (credentials_seen != 0) {
                unexpected = 1;
                continue;
            }
            memcpy(&credentials, CMSG_DATA(header), sizeof(credentials));
            credentials_seen = 1;
            if (credentials.pid != expected_pid ||
                credentials.uid != expected_uid ||
                credentials.gid != expected_gid) {
                bad_peer = 1;
            }
        } else {
            unexpected = 1;
        }
    }

    if (bad_peer != 0 || credentials_seen == 0) {
        return YUKI_PROTOCOL_BAD_PEER;
    }
    if (unexpected != 0) {
        return YUKI_PROTOCOL_UNEXPECTED_FD;
    }
    return YUKI_PROTOCOL_OK;
}

enum yuki_protocol_result
yuki_linux_transport_receive(int fd, struct yuki_protocol_state *state,
                             pid_t expected_pid, uid_t expected_uid,
                             gid_t expected_gid, int timeout_ms)
{
    uint8_t packet[YUKI_PROTOCOL_PACKET_SIZE];
    union {
        struct cmsghdr alignment;
        uint8_t bytes[YUKI_CONTROL_BUFFER_SIZE];
    } control;
    struct iovec vector;
    struct msghdr message;
    enum yuki_protocol_result result;
    ssize_t received;
    enum yuki_protocol_result control_result;
    int pass_credentials = 0;
    socklen_t pass_credentials_length = (socklen_t)sizeof(pass_credentials);

    if (state == NULL || fd < 0 || timeout_ms < 0) {
        return YUKI_PROTOCOL_INVALID_ARGUMENT;
    }
    result = check_seqpacket(fd);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }
    if (getsockopt(fd, SOL_SOCKET, SO_PASSCRED, &pass_credentials,
                   &pass_credentials_length) != 0 ||
        pass_credentials_length != sizeof(pass_credentials) ||
        pass_credentials != 1) {
        return YUKI_PROTOCOL_BAD_PEER;
    }
    result = wait_ready(fd, POLLIN, timeout_ms);
    if (result != YUKI_PROTOCOL_OK) {
        return result;
    }

    memset(&message, 0, sizeof(message));
    memset(&control, 0, sizeof(control));
    vector.iov_base = packet;
    vector.iov_len = sizeof(packet);
    message.msg_iov = &vector;
    message.msg_iovlen = 1;
    message.msg_control = control.bytes;
    message.msg_controllen = sizeof(control.bytes);

    received = recvmsg(fd, &message, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
    if (received < 0) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }

    control_result = validate_control_messages(&message, expected_pid,
                                               expected_uid, expected_gid);
    if ((message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0 ||
        received != (ssize_t)sizeof(packet)) {
        return YUKI_PROTOCOL_BAD_PACKET;
    }
    if (control_result != YUKI_PROTOCOL_OK) {
        return control_result;
    }
    return yuki_protocol_receive(state, packet, (size_t)received, 0u);
}
