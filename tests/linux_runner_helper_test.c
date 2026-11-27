#define _GNU_SOURCE
#include "../evaluation/linux_runner_transport.h"

#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE] = {
    '0','1','2','3','4','5','6','7','8','9','a','b','c','d','e','f'
};
static const char nonce_hex[] = "30313233343536373839616263646566";


static void test_supervisor_death(const char *helper_path)
{
    int report_pipe[2];
    pid_t supervisor;
    pid_t helper;
    int pidfd;
    int status;
    struct pollfd pinned;
    char pid_buffer[sizeof(helper)];

    assert(pipe(report_pipe) == 0);
    supervisor = fork();
    assert(supervisor >= 0);
    if (supervisor == 0) {
        int sockets[2];
        pid_t spawned;
        char pid_text[32];
        char uid_text[32];
        char gid_text[32];

        close(report_pipe[0]);
        assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0,
                          sockets) == 0);
        spawned = fork();
        assert(spawned >= 0);
        if (spawned == 0) {
            close(sockets[0]);
            if (sockets[1] != 3) {
                if (dup2(sockets[1], 3) < 0) {
                    _exit(120);
                }
                close(sockets[1]);
            } else {
                (void)fcntl(3, F_SETFD, 0);
            }
            (void)snprintf(pid_text, sizeof(pid_text), "%ld",
                           (long)getppid());
            (void)snprintf(uid_text, sizeof(uid_text), "%lu",
                           (unsigned long)getuid());
            (void)snprintf(gid_text, sizeof(gid_text), "%lu",
                           (unsigned long)getgid());
            execl(helper_path, helper_path, "3", pid_text, uid_text,
                  gid_text, nonce_hex, (char *)NULL);
            _exit(121);
        }
        close(sockets[0]);
        close(sockets[1]);
        assert(write(report_pipe[1], &spawned, sizeof(spawned)) ==
               (ssize_t)sizeof(spawned));
        close(report_pipe[1]);
        for (;;) {
            pause();
        }
    }

    close(report_pipe[1]);
    assert(read(report_pipe[0], pid_buffer, sizeof(helper)) ==
           (ssize_t)sizeof(helper));
    memcpy(&helper, pid_buffer, sizeof(helper));
    close(report_pipe[0]);
    pidfd = (int)syscall(SYS_pidfd_open, helper, 0);
    assert(pidfd >= 0);

    /* Killing the trusted parent must terminate its blocked helper. */
    assert(kill(supervisor, SIGKILL) == 0);
    assert(waitpid(supervisor, &status, 0) == supervisor);
    assert(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL);

    pinned.fd = pidfd;
    pinned.events = POLLIN;
    pinned.revents = 0;
    assert(poll(&pinned, 1, 5000) == 1);
    assert((pinned.revents & POLLIN) != 0);
    close(pidfd);
}

int main(int argc, char **argv)
{
    int sockets[2];
    int pidfd;
    pid_t child;
    char pid_text[32];
    char uid_text[32];
    char gid_text[32];
    struct yuki_protocol_state supervisor;
    int status;
    struct pollfd pinned;
    char fd_directory_path[64];
    DIR *fd_directory;
    struct dirent *entry;
    unsigned int observed_fds = 0;

    assert(argc == 2);
    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) == 0);
    assert(yuki_linux_transport_enable_credentials(sockets[0]) ==
           YUKI_PROTOCOL_OK);
    child = fork();
    assert(child >= 0);
    if (child == 0) {
        close(sockets[0]);
        if (sockets[1] != 3) {
            if (dup2(sockets[1], 3) < 0) {
                _exit(120);
            }
            close(sockets[1]);
        } else {
            (void)fcntl(3, F_SETFD, 0);
        }
        (void)snprintf(pid_text, sizeof(pid_text), "%ld", (long)getppid());
        (void)snprintf(uid_text, sizeof(uid_text), "%lu", (unsigned long)getuid());
        (void)snprintf(gid_text, sizeof(gid_text), "%lu", (unsigned long)getgid());
        execl(argv[1], argv[1], "3", pid_text, uid_text, gid_text,
              nonce_hex, (char *)NULL);
        _exit(121);
    }

    close(sockets[1]);
    pidfd = (int)syscall(SYS_pidfd_open, child, 0);
    assert(pidfd >= 0);
    assert(yuki_protocol_init(&supervisor, YUKI_ROLE_SUPERVISOR, nonce) ==
           YUKI_PROTOCOL_OK);
    assert(yuki_linux_transport_receive(sockets[0], &supervisor, child,
                                        getuid(), getgid(), 5000) ==
           YUKI_PROTOCOL_OK);
    assert(supervisor.phase == YUKI_PHASE_BOOTSTRAP_READY);
    (void)snprintf(fd_directory_path, sizeof(fd_directory_path),
                   "/proc/%ld/fd", (long)child);
    fd_directory = opendir(fd_directory_path);
    assert(fd_directory != NULL);
    while ((entry = readdir(fd_directory)) != NULL) {
        if (entry->d_name[0] >= '0' && entry->d_name[0] <= '9') {
            assert(strcmp(entry->d_name, "3") == 0);
            ++observed_fds;
        }
    }
    assert(closedir(fd_directory) == 0);
    assert(observed_fds == 1);
    assert(yuki_linux_transport_send(sockets[0], &supervisor, YUKI_MSG_ABORT,
                                     5000) == YUKI_PROTOCOL_OK);
    assert(supervisor.phase == YUKI_PHASE_ABORTED);
    assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);

    pinned.fd = pidfd;
    pinned.events = POLLIN;
    pinned.revents = 0;
    assert(poll(&pinned, 1, 0) == 1);
    assert((pinned.revents & POLLIN) != 0);

    close(pidfd);
    close(sockets[0]);
    test_supervisor_death(argv[1]);
    return 0;
}
