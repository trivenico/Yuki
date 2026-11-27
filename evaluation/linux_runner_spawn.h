#ifndef YUKI_LINUX_RUNNER_SPAWN_H
#define YUKI_LINUX_RUNNER_SPAWN_H

#include "native_runner_protocol.h"

#include <sys/types.h>
#include <stdint.h>

struct yuki_linux_runner_child {
    pid_t pid;
    int pidfd;
    int control_fd;
    uid_t uid;
    gid_t gid;
    struct yuki_protocol_state protocol;
};

/* Spawn a pinned absolute-path ELF with control fd 3 and temporary executable
 * fd 4; the helper must close fd 4 before readiness. Returns after credential-
 * authenticated readiness and kernel executable inode verification.
 */
int yuki_linux_runner_spawn(const char *absolute_helper_path,
                           const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE],
                           int timeout_ms,
                           struct yuki_linux_runner_child *child);

/* Send ABORT, reap the exact child, and close owned descriptors. */
int yuki_linux_runner_child_abort(struct yuki_linux_runner_child *child);

#endif
