#define _GNU_SOURCE
#include "../evaluation/linux_runner_transport.h"

#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

static const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE] = {
    '0','1','2','3','4','5','6','7','8','9','a','b','c','d','e','f'
};

int main(void)
{
    int sockets[2];
    struct yuki_protocol_state supervisor;
    struct yuki_protocol_state helper;
    uid_t uid = getuid();
    gid_t gid = getgid();
    pid_t pid = getpid();

    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) == 0);
    assert(yuki_protocol_init(&supervisor, YUKI_ROLE_SUPERVISOR, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_protocol_init(&helper, YUKI_ROLE_HELPER, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_linux_transport_enable_credentials(sockets[0]) ==\n           YUKI_PROTOCOL_OK);\n    assert(yuki_linux_transport_check_peer(sockets[0], pid, uid, gid) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_linux_transport_send(sockets[1], &helper,
                                     YUKI_MSG_BOOTSTRAP_READY, 500) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_linux_transport_receive(sockets[0], &supervisor,
                                        pid, uid, gid, 500) ==
           YUKI_PROTOCOL_OK);
    assert(supervisor.phase == YUKI_PHASE_BOOTSTRAP_READY);
    assert(helper.phase == YUKI_PHASE_BOOTSTRAP_READY);

    assert(yuki_linux_transport_check_peer(sockets[0], pid + 1, uid, gid) ==
           YUKI_PROTOCOL_BAD_PEER);
    close(sockets[0]);
    close(sockets[1]);


    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) == 0);
    assert(yuki_protocol_init(&supervisor, YUKI_ROLE_SUPERVISOR, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_protocol_init(&helper, YUKI_ROLE_HELPER, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_linux_transport_send(sockets[1], &helper,
                                     YUKI_MSG_BOOTSTRAP_READY, 500) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_linux_transport_receive(sockets[0], &supervisor,
                                        pid + 1, uid, gid, 500) ==
           YUKI_PROTOCOL_BAD_PEER);
    assert(supervisor.phase == YUKI_PHASE_START);
    close(sockets[0]);
    close(sockets[1]);

    assert(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0);
    assert(yuki_linux_transport_check_peer(sockets[0], pid, uid, gid) ==
           YUKI_PROTOCOL_BAD_PACKET);
    close(sockets[0]);
    close(sockets[1]);

    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) == 0);
    {
        int source_fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
        struct {
            struct cmsghdr header;
            int descriptor;
        } ancillary;
        struct iovec vector;
        struct msghdr message;
        uint8_t packet[YUKI_PROTOCOL_PACKET_SIZE] = {
            0x59, 0x55, 0x4b, 0x49, 0x01, 0x01, 0x00, 0x00,
            0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37,
            0x38, 0x39, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66
        };
        assert(source_fd >= 0);
        memset(&ancillary, 0, sizeof(ancillary));
        ancillary.header.cmsg_len = CMSG_LEN(sizeof(int));
        ancillary.header.cmsg_level = SOL_SOCKET;
        ancillary.header.cmsg_type = SCM_RIGHTS;
        ancillary.descriptor = source_fd;
        memset(&message, 0, sizeof(message));
        vector.iov_base = packet;
        vector.iov_len = sizeof(packet);
        message.msg_iov = &vector;
        message.msg_iovlen = 1;
        message.msg_control = &ancillary;
        message.msg_controllen = sizeof(ancillary);
        assert(sendmsg(sockets[1], &message, 0) == (ssize_t)sizeof(packet));
        assert(yuki_protocol_init(&supervisor, YUKI_ROLE_SUPERVISOR, nonce) ==
               YUKI_PROTOCOL_OK);
        assert(yuki_linux_transport_receive(sockets[0], &supervisor,
                                            pid, uid, gid, 500) ==
               YUKI_PROTOCOL_UNEXPECTED_FD);
        close(source_fd);
    }
    close(sockets[0]);
    close(sockets[1]);
    return 0;
}
