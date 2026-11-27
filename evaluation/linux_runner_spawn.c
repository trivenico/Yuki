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
#include <poll.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/xattr.h>
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

static int remaining_timeout_ms(const struct timespec *deadline)
{
    struct timespec now;
    long long milliseconds;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    milliseconds = (long long)(deadline->tv_sec - now.tv_sec) * 1000LL +
                   (deadline->tv_nsec - now.tv_nsec + 999999L) / 1000000LL;
    if (milliseconds <= 0) return 0;
    return milliseconds > INT_MAX ? INT_MAX : (int)milliseconds;
}

static int pidfd_wait_readable(int pidfd, int timeout_ms)
{
    struct timespec deadline, now;
    struct pollfd descriptor;
    long remaining;
    int result;
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) return -1;
    deadline.tv_sec += timeout_ms / 1000;
    deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        ++deadline.tv_sec;
        deadline.tv_nsec -= 1000000000L;
    }
    descriptor.fd = pidfd;
    descriptor.events = POLLIN;
    descriptor.revents = 0;
    for (;;) {
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
        remaining = (deadline.tv_sec - now.tv_sec) * 1000L +
                    (deadline.tv_nsec - now.tv_nsec + 999999L) / 1000000L;
        if (remaining < 0) remaining = 0;
        result = poll(&descriptor, 1, remaining > INT_MAX ? INT_MAX : (int)remaining);
        if (result < 0 && errno == EINTR) continue;
        if (result <= 0) return result;
        return (descriptor.revents & POLLIN) != 0 ? 1 : -1;
    }
}

static int read_proc_start_time(pid_t pid, unsigned long long *start_time)
{
    char path[64], buffer[4096];
    char *cursor, *end;
    ssize_t length;
    int fd;
    unsigned int field;
    unsigned long long value;
    (void)snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    length = read(fd, buffer, sizeof(buffer) - 1u);
    close(fd);
    if (length <= 0) { errno = EIO; return -1; }
    buffer[length] = '\0';
    cursor = strrchr(buffer, ')');
    if (cursor == NULL || cursor[1] != ' ') { errno = EPROTO; return -1; }
    cursor += 2;
    for (field = 3; field < 22; ++field) {
        while (*cursor == ' ') ++cursor;
        while (*cursor != '\0' && *cursor != ' ') ++cursor;
        if (*cursor == '\0') { errno = EPROTO; return -1; }
    }
    while (*cursor == ' ') ++cursor;
    value = strtoull(cursor, &end, 10);
    if (end == cursor || (*end != ' ' && *end != '\0')) {
        errno = EPROTO;
        return -1;
    }
    *start_time = value;
    return 0;
}

static int pidfd_is_alive(int pidfd)
{
    return pidfd_wait_readable(pidfd, 0) == 0;
}

static int waitpid_bounded(pid_t pid, int *status, int timeout_ms)
{
    struct timespec deadline, now, pause_time;
    long remaining;
    long remaining_ns;
    pid_t result;
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) return -1;
    deadline.tv_sec += timeout_ms / 1000;
    deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        ++deadline.tv_sec;
        deadline.tv_nsec -= 1000000000L;
    }
    for (;;) {
        do {
            result = waitpid(pid, status, WNOHANG);
        } while (result < 0 && errno == EINTR);
        if (result == pid) return 1;
        if (result < 0) return -1;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
        remaining_ns = (deadline.tv_sec - now.tv_sec) * 1000000000L +
                       (deadline.tv_nsec - now.tv_nsec);
        if (remaining_ns <= 0) return 0;
        pause_time.tv_sec = 0;
        pause_time.tv_nsec = remaining_ns < 10000000L
                                 ? remaining_ns : 10000000L;
        if (nanosleep(&pause_time, NULL) != 0 && errno != EINTR) return -1;
    }
}

static int cleanup_partial(pid_t pid, int pidfd, int control_fd,
                           struct yuki_linux_runner_child *child)
{
    if (pid <= 0 || child == NULL) {
        if (pidfd >= 0) close(pidfd);
        if (control_fd >= 0) close(control_fd);
        errno = EINVAL;
        return -1;
    }
    child->pid = pid;
    child->pidfd = pidfd;
    child->control_fd = control_fd;
    if (child->start_time_ticks == 0) {
        (void)read_proc_start_time(pid, &child->start_time_ticks);
    }
    /*
     * Never discard a live child's recovery handles. On failure, the caller
     * receives the populated child structure and can retry abort/reap.
     */
    return yuki_linux_runner_child_abort(child, 5000);
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
    unsigned long long start_time_ticks = 0;
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
    child->pid = -1;
    child->pidfd = -1;
    child->control_fd = -1;

    executable_fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (executable_fd < 0) {
        return -1;
    }
    if (fstat(executable_fd, &expected) != 0 || !S_ISREG(expected.st_mode)) {
        close(executable_fd);
        errno = ENOEXEC;
        return -1;
    }
    /*
     * Pinning an inode prevents pathname substitution, not in-place writes.
     * Accept only root/current-user owned, non-writable helper images.
     */
    if ((expected.st_uid != 0 && expected.st_uid != geteuid()) ||
        (expected.st_mode & (S_IWUSR | S_IWGRP | S_IWOTH |
                          S_ISUID | S_ISGID)) != 0) {
        close(executable_fd);
        errno = EACCES;
        return -1;
    }
    /*
     * File capabilities can grant privilege without setuid/setgid bits.
     * Reject a present capability xattr, and fail closed if the filesystem
     * cannot tell us whether one is present.
     */
    {
        unsigned char capability[256];
        ssize_t capability_size =
            fgetxattr(executable_fd, "security.capability",
                      capability, sizeof(capability));
        if (capability_size >= 0) {
            close(executable_fd);
            errno = EACCES;
            return -1;
        }
        if (errno != ENODATA) {
            int capability_error = errno;
            close(executable_fd);
            errno = capability_error;
            return -1;
        }
    }
    if (pread(executable_fd, elf_magic, sizeof(elf_magic), 0) !=
            (ssize_t)sizeof(elf_magic) ||
        memcmp(elf_magic, "\177ELF", sizeof(elf_magic)) != 0) {
        close(executable_fd);
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
        int original_error = errno;
        if (cleanup_partial(pid, -1, sockets[0], child) == 0)
            errno = original_error;
        return -1;
    }
    if (yuki_protocol_init(&child->protocol, YUKI_ROLE_SUPERVISOR, nonce) !=
        YUKI_PROTOCOL_OK) {
        int original_error = EPROTO;
        if (cleanup_partial(pid, pidfd, sockets[0], child) == 0)
            errno = original_error;
        return -1;
    }
    result = yuki_linux_transport_receive(sockets[0], &child->protocol, pid,
                                          getuid(), getgid(), timeout_ms);
    if (result != YUKI_PROTOCOL_OK ||
        child->protocol.phase != YUKI_PHASE_BOOTSTRAP_READY) {
        int original_error = EPROTO;
        if (cleanup_partial(pid, pidfd, sockets[0], child) == 0)
            errno = original_error;
        return -1;
    }

    if (!pidfd_is_alive(pidfd) ||
        read_proc_start_time(pid, &start_time_ticks) != 0) {
        int original_error = ESTALE;
        if (cleanup_partial(pid, pidfd, sockets[0], child) == 0)
            errno = original_error;
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
            int original_error = ESTALE;
            if (cleanup_partial(pid, pidfd, sockets[0], child) == 0)
                errno = original_error;
            return -1;
        }
    }

    child->pid = pid;
    child->pidfd = pidfd;
    child->control_fd = sockets[0];
    child->uid = getuid();
    child->gid = getgid();
    child->start_time_ticks = start_time_ticks;
    if (!pidfd_is_alive(pidfd)) {
        int original_error = ESTALE;
        if (yuki_linux_runner_child_abort(child, 1000) == 0)
            errno = original_error;
        return -1;
    }
    return 0;
}

int yuki_linux_runner_child_abort(struct yuki_linux_runner_child *child,
                                  int timeout_ms)
{
    int status = 0;
    int ready = 0;
    int reaped;
    unsigned long long observed_start_time;
    struct timespec deadline;
    int remaining;
    enum yuki_protocol_result protocol_result;

    if (child == NULL || child->pid <= 0 || child->control_fd < 0 ||
        timeout_ms < 0) {
        errno = EINVAL;
        return -1;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) return -1;
    deadline.tv_sec += timeout_ms / 1000;
    deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        ++deadline.tv_sec;
        deadline.tv_nsec -= 1000000000L;
    }

    if (child->pidfd >= 0 &&
        child->protocol.phase == YUKI_PHASE_BOOTSTRAP_READY) {
        remaining = remaining_timeout_ms(&deadline);
        if (remaining < 0) return -1;
        protocol_result = yuki_linux_transport_send(
            child->control_fd, &child->protocol, YUKI_MSG_ABORT, remaining);
        if (protocol_result == YUKI_PROTOCOL_OK) {
            remaining = remaining_timeout_ms(&deadline);
            if (remaining < 0) return -1;
            ready = pidfd_wait_readable(child->pidfd, remaining);
        }
    }

    if (ready != 1) {
        if (child->pidfd >= 0) {
            if (pidfd_kill(child->pidfd) != 0 && errno != ESRCH) return -1;
            remaining = remaining_timeout_ms(&deadline);
            if (remaining < 0) return -1;
            ready = pidfd_wait_readable(child->pidfd, remaining);
            if (ready != 1) {
                errno = ETIMEDOUT;
                return -1;
            }
        } else {
            /*
             * A numeric PID is not a stable process reference. Never send a
             * signal through it when pidfd_open was unavailable: another
             * reaper could have released the PID and the kernel could have
             * reused it. Wait only for natural exit and retain the handle on
             * timeout so the owner can retry or perform external recovery.
             */
            remaining = remaining_timeout_ms(&deadline);
            if (remaining < 0) return -1;
            reaped = waitpid_bounded(child->pid, &status, remaining);
            if (reaped != 1) {
                if (reaped == 0) errno = ETIMEDOUT;
                return -1;
            }
            goto child_reaped;
        }
    }

    if (child->start_time_ticks != 0) {
        if (read_proc_start_time(child->pid, &observed_start_time) != 0 ||
            observed_start_time != child->start_time_ticks) {
            errno = ESTALE;
            return -1;
        }
    }

    if (child->pidfd >= 0) {
        do {
            reaped = (int)waitpid(child->pid, &status, WNOHANG);
        } while (reaped < 0 && errno == EINTR);
        if (reaped != child->pid) {
            errno = reaped == 0 ? EAGAIN : errno;
            return -1;
        }
    } else {
        remaining = remaining_timeout_ms(&deadline);
        if (remaining < 0) return -1;
        reaped = waitpid_bounded(child->pid, &status, remaining);
        if (reaped != 1) {
            if (reaped == 0) errno = ETIMEDOUT;
            return -1;
        }
    }

child_reaped:
    close(child->control_fd);
    if (child->pidfd >= 0) close(child->pidfd);
    child->control_fd = -1;
    child->pidfd = -1;
    child->pid = -1;
    child->start_time_ticks = 0;
    return 0;
}
