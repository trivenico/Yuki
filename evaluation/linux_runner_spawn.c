#define _GNU_SOURCE
#include "linux_runner_spawn.h"
#include "linux_runner_transport.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <limits.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>


static int pidfd_open_checked(pid_t pid)
{
#ifdef SYS_pidfd_open
    return (int)syscall(SYS_pidfd_open, pid, 0u);
#else
    (void)pid;
    errno = ENOSYS;
    return -1;
#endif
}

static int pidfd_kill(int pidfd)
{
#ifdef SYS_pidfd_send_signal
    return (int)syscall(SYS_pidfd_send_signal, pidfd, SIGKILL, NULL, 0u);
#else
    (void)pidfd;
    errno = ENOSYS;
    return -1;
#endif
}

static void reap_child(pid_t pid)
{
    int status;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
    }
}

static void cleanup_partial(pid_t pid, int pidfd, int control_fd)
{
    if (pidfd >= 0) {
        (void)pidfd_kill(pidfd);
    } else if (pid > 0) {
        (void)kill(pid, SIGKILL);
    }
    if (pid > 0) {
        reap_child(pid);
    }
    if (pidfd >= 0) {
        (void)close(pidfd);
    }
    if (control_fd >= 0) {
        (void)close(control_fd);
    }
}

int yuki_linux_runner_spawn(const char *path,
                           const uint8_t nonce[YUKI_PROTOCOL_NONCE_SIZE],
                           int timeout_ms,
                           struct yuki_linux_runner_child *child)
{
    int sockets[2] = {-1, -1};
    int child_source = -1;
    int executable_fd = -1;
    int executable_child_fd = -1;
    int pidfd = -1;
    pid_t pid = -1;
    pid_t supervisor_pid;
    struct stat expected;
    char elf_magic[4];
    struct stat actual;
    char pid_text[32];
    char uid_text[32];
    char gid_text[32];
    char nonce_text[YUKI_PROTOCOL_NONCE_SIZE * 2u + 1u];
    static const char hex[] = "0123456789abcdef";
    char *argv[7];
    static char executable_fd_path[] = "/proc/self/fd/4";
    char *const envp[] = {(char *)"PATH=/usr/bin:/bin",
                          (char *)"LANG=C", NULL};
    posix_spawn_file_actions_t actions;
    int actions_initialized = 0;
    int spawn_error;
    size_t i;
    enum yuki_protocol_result result;

    if (path == NULL || path[0] != '/' || nonce == NULL || timeout_ms < 0 ||
        child == NULL) {
        errno = EINVAL;
        return -1;
    }
    memset(child, 0, sizeof(*child));
    child->pidfd = -1;
    child->control_fd = -1;

    executable_fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (executable_fd < 0 || fstat(executable_fd, &expected) != 0 ||
        !S_ISREG(expected.st_mode) || pread(executable_fd, elf_magic,
                                             sizeof(elf_magic), 0) !=
                                             (ssize_t)sizeof(elf_magic) ||
        memcmp(elf_magic, "\177ELF", sizeof(elf_magic)) != 0) {
        if (executable_fd >= 0) {
            close(executable_fd);
        }
        errno = ENOEXEC;
        return -1;
    }
    if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) != 0) {
        close(executable_fd);
        return -1;
    }
    result = yuki_linux_transport_enable_credentials(sockets[0]);
    if (result != YUKI_PROTOCOL_OK) {
        errno = EPROTO;
        close(executable_fd);
        close(sockets[0]);
        close(sockets[1]);
        return -1;
    }

    /*
     * posix_spawn avoids running non-async-signal-safe code in a post-fork
     * child. Pin the ELF by an open descriptor and execute that exact inode
     * through procfs fd 4; fd 3 is the control channel. Close fd 5 and above.
     */
    child_source = fcntl(sockets[1], F_DUPFD_CLOEXEC, 5);
    executable_child_fd = fcntl(executable_fd, F_DUPFD_CLOEXEC, 5);
    if (child_source < 0 || executable_child_fd < 0) {
        if (child_source >= 0) close(child_source);
        if (executable_child_fd >= 0) close(executable_child_fd);
        close(executable_fd);
        close(sockets[0]);
        close(sockets[1]);
        return -1;
    }
    supervisor_pid = getpid();
    (void)snprintf(pid_text, sizeof(pid_text), "%ld",
                   (long)supervisor_pid);
    (void)snprintf(uid_text, sizeof(uid_text), "%lu", (unsigned long)getuid());
    (void)snprintf(gid_text, sizeof(gid_text), "%lu", (unsigned long)getgid());
    for (i = 0; i < YUKI_PROTOCOL_NONCE_SIZE; ++i) {
        nonce_text[i * 2u] = hex[nonce[i] >> 4];
        nonce_text[i * 2u + 1u] = hex[nonce[i] & 0x0fu];
    }
    nonce_text[sizeof(nonce_text) - 1u] = '\0';
    argv[0] = path;
    argv[1] = (char *)"3";
    argv[2] = pid_text;
    argv[3] = uid_text;
    argv[4] = gid_text;
    argv[5] = nonce_text;
    argv[6] = NULL;

    spawn_error = posix_spawn_file_actions_init(&actions);
    if (spawn_error == 0) {
        actions_initialized = 1;
        spawn_error = posix_spawn_file_actions_adddup2(&actions,
                                                       child_source, 3);
    }
    if (spawn_error == 0) {
        spawn_error = posix_spawn_file_actions_adddup2(&actions,
                                                       executable_child_fd, 4);
    }
    if (spawn_error == 0) {
        spawn_error = posix_spawn_file_actions_addclosefrom_np(&actions, 5);
    }
    if (spawn_error == 0) {
        spawn_error = posix_spawn(&pid, executable_fd_path, &actions, NULL,
                                  argv, envp);
    }
    if (actions_initialized != 0) {
        posix_spawn_file_actions_destroy(&actions);
    }
    close(child_source);
    close(executable_child_fd);
    close(executable_fd);
    close(sockets[1]);
    sockets[1] = -1;
    if (spawn_error != 0) {
        close(sockets[0]);
        errno = spawn_error;
        return -1;
    }

    pidfd = pidfd_open_checked(pid);
    if (pidfd < 0) {
        cleanup_partial(pid, -1, sockets[0]);
        return -1;
    }
    if (yuki_protocol_init(&child->protocol, YUKI_ROLE_SUPERVISOR, nonce) !=
        YUKI_PROTOCOL_OK) {
        cleanup_partial(pid, pidfd, sockets[0]);
        return -1;
    }
    result = yuki_linux_transport_receive(sockets[0], &child->protocol, pid,
                                          getuid(), getgid(), timeout_ms);
    if (result != YUKI_PROTOCOL_OK ||
        child->protocol.phase != YUKI_PHASE_BOOTSTRAP_READY) {
        cleanup_partial(pid, pidfd, sockets[0]);
        errno = EPROTO;
        return -1;
    }

    /* Compare the kernel's executable inode, not a mutable path string. */
    {
        char proc_exe[64];
        int length = snprintf(proc_exe, sizeof(proc_exe), "/proc/%ld/exe",
                              (long)pid);
        if (length <= 0 || (size_t)length >= sizeof(proc_exe) ||
            stat(proc_exe, &actual) != 0 ||
            actual.st_dev != expected.st_dev || actual.st_ino != expected.st_ino) {
            cleanup_partial(pid, pidfd, sockets[0]);
            errno = ESTALE;
            return -1;
        }
    }

    child->pid = pid;
    child->pidfd = pidfd;
    child->control_fd = sockets[0];
    child->uid = getuid();
    child->gid = getgid();
    return 0;
}

int yuki_linux_runner_child_abort(struct yuki_linux_runner_child *child)
{
    int result = 0;
    int status;
    enum yuki_protocol_result protocol_result;

    if (child == NULL || child->pid <= 0 || child->pidfd < 0 ||
        child->control_fd < 0) {
        errno = EINVAL;
        return -1;
    }
    protocol_result = yuki_linux_transport_send(
        child->control_fd, &child->protocol, YUKI_MSG_ABORT, 5000);
    if (protocol_result != YUKI_PROTOCOL_OK) {
        result = -1;
        (void)pidfd_kill(child->pidfd);
    }
    while (waitpid(child->pid, &status, 0) < 0) {
        if (errno != EINTR) {
            result = -1;
            break;
        }
    }
    if (result == 0 && (!WIFEXITED(status) || WEXITSTATUS(status) != 0)) {
        result = -1;
    }
    close(child->control_fd);
    close(child->pidfd);
    child->control_fd = -1;
    child->pidfd = -1;
    child->pid = -1;
    return result;
}
