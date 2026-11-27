#define _GNU_SOURCE
#include "../evaluation/linux_runner_spawn.h"

#include <assert.h>
#include <dirent.h>
#include <poll.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    static const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE] = {
        's','p','a','w','n','-','t','e','s','t','-','n','o','n','c','e'
    };
    struct yuki_linux_runner_child child;
    char fd_path[64];
    DIR *directory;
    struct dirent *entry;
    unsigned int descriptors = 0;
    struct pollfd pinned;

    assert(argc == 2);
    assert(argv[1][0] == '/');

    /* Writable helper images must be rejected before any child is spawned. */
    assert(chmod(argv[1], 0755) == 0);
    assert(yuki_linux_runner_spawn(argv[1], nonce, 100, &child) == -1);
    assert(child.pid == -1 && child.pidfd == -1 && child.control_fd == -1);

    /* Privilege-changing setuid/setgid helper modes must also be rejected. */
    assert(chmod(argv[1], 04755) == 0);
    assert(yuki_linux_runner_spawn(argv[1], nonce, 100, &child) == -1);
    assert(child.pid == -1 && child.pidfd == -1 && child.control_fd == -1);
    assert(chmod(argv[1], 0555) == 0);

    /*
     * When CI permits setting Linux file capabilities, verify that the
     * launcher rejects them even though the ordinary mode bits are safe.
     * Environments without CAP_SETFCAP still exercise the normal path.
     */
    {
        const unsigned char capability[20] = {
            0x01, 0x00, 0x00, 0x02, /* VFS_CAP_REVISION_2 | effective */
            0x01, 0x00, 0x00, 0x00, /* permitted: CAP_CHOWN */
            0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, /* inheritable */
            0x00, 0x00, 0x00, 0x00
        };
        if (setxattr(argv[1], "security.capability", capability,
                     sizeof(capability), 0) == 0) {
            assert(yuki_linux_runner_spawn(argv[1], nonce, 100, &child) == -1);
            assert(errno == EACCES);
            assert(child.pid == -1 && child.pidfd == -1 &&
                   child.control_fd == -1);
            assert(removexattr(argv[1], "security.capability") == 0);
        } else if (errno == EOPNOTSUPP || errno == ENOTSUP) {
            /*
             * The filesystem cannot support the security xattr query. The
             * launcher must fail closed rather than assume no capability.
             */
            assert(yuki_linux_runner_spawn(argv[1], nonce, 100, &child) == -1);
            assert(errno == ENOTSUP || errno == EOPNOTSUPP);
            assert(child.pid == -1 && child.pidfd == -1 &&
                   child.control_fd == -1);
            return 0;
        } else {
            /* Lack of CAP_SETFCAP is fine; the absent xattr remains queryable. */
            assert(errno == EPERM || errno == EACCES || errno == EINVAL);
        }
    }

    /* A pre-spawn failure leaves an unambiguous empty child handle. */
    assert(yuki_linux_runner_spawn("/yuki/nonexistent-helper", nonce, 100, &child) == -1);
    assert(child.pid == -1 && child.pidfd == -1 && child.control_fd == -1);
    assert(yuki_linux_runner_spawn(argv[1], nonce, 5000, &child) == 0);
    assert(child.pid > 0 && child.pidfd >= 0 && child.control_fd >= 0);
    assert(child.protocol.phase == YUKI_PHASE_BOOTSTRAP_READY);
    assert(child.start_time_ticks > 0);

    (void)snprintf(fd_path, sizeof(fd_path), "/proc/%ld/fd",
                   (long)child.pid);
    directory = opendir(fd_path);
    assert(directory != NULL);
    while ((entry = readdir(directory)) != NULL) {
        if (entry->d_name[0] >= '0' && entry->d_name[0] <= '9') {
            assert(strcmp(entry->d_name, "3") == 0);
            ++descriptors;
        }
    }
    assert(closedir(directory) == 0);
    assert(descriptors == 1);

    pinned.fd = child.pidfd;
    pinned.events = POLLIN;
    pinned.revents = 0;
    assert(poll(&pinned, 1, 0) == 0);
    assert(yuki_linux_runner_child_abort(&child, 5000) == 0);
    assert(child.pidfd == -1 && child.control_fd == -1);

    /*
     * Without a pidfd, abort must not signal a numeric PID. It may wait for
     * natural exit, but must retain the recovery handle when the child lives.
     */
    assert(yuki_linux_runner_spawn(argv[1], nonce, 5000, &child) == 0);
    {
        int pinned_pidfd = child.pidfd;
        pid_t pinned_pid = child.pid;
        child.pidfd = -1;
        assert(yuki_linux_runner_child_abort(&child, 50) == -1);
        assert(errno == ETIMEDOUT);
        assert(child.pid == pinned_pid && child.pidfd == -1 &&
               child.control_fd >= 0);
        assert(kill(pinned_pid, 0) == 0);
        child.pidfd = pinned_pidfd;
        assert(yuki_linux_runner_child_abort(&child, 5000) == 0);
    }

    /* A stopped helper cannot process ABORT: bounded wait must escalate via pidfd. */
    assert(yuki_linux_runner_spawn(argv[1], nonce, 5000, &child) == 0);
    assert(kill(child.pid, SIGSTOP) == 0);
    assert(yuki_linux_runner_child_abort(&child, 50) == 0);
    assert(child.pid == -1 && child.pidfd == -1 && child.control_fd == -1);
    return 0;
}
