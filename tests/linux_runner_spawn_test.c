#define _GNU_SOURCE
#include "../evaluation/linux_runner_spawn.h"

#include <assert.h>
#include <dirent.h>
#include <poll.h>
#include <signal.h>
#include <sys/stat.h>
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
    assert(chmod(argv[1], 0555) == 0);

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

    /* A stopped helper cannot process ABORT: bounded wait must escalate via pidfd. */
    assert(yuki_linux_runner_spawn(argv[1], nonce, 5000, &child) == 0);
    assert(kill(child.pid, SIGSTOP) == 0);
    assert(yuki_linux_runner_child_abort(&child, 50) == 0);
    assert(child.pid == -1 && child.pidfd == -1 && child.control_fd == -1);
    return 0;
}
