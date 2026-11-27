#ifndef YUKI_LINUX_RUNNER_TRANSPORT_H
#define YUKI_LINUX_RUNNER_TRANSPORT_H

#include "native_runner_protocol.h"

#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Linux-only SOCK_SEQPACKET transport helpers.
 * The caller owns fd. Every operation has a finite timeout in milliseconds.
 * Call enable_credentials on each receiving endpoint before any packet is
 * sent. Expected peer credentials must come from an independently pinned
 * process identity; the nonce is not peer authentication.
 */
enum yuki_protocol_result
yuki_linux_transport_enable_credentials(int fd);

enum yuki_protocol_result
yuki_linux_transport_check_peer(int fd, pid_t expected_pid,
                                uid_t expected_uid, gid_t expected_gid);

enum yuki_protocol_result
yuki_linux_transport_send(int fd, struct yuki_protocol_state *state,
                          enum yuki_message message, int timeout_ms);

enum yuki_protocol_result
yuki_linux_transport_receive(int fd, struct yuki_protocol_state *state,
                             pid_t expected_pid, uid_t expected_uid,
                             gid_t expected_gid, int timeout_ms);

#ifdef __cplusplus
}
#endif

#endif
