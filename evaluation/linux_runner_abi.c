#include "linux_runner_abi.h"
#include "linux_runner_spawn.h"

#include <errno.h>
#include <stdlib.h>

uint32_t yuki_linux_runner_abi_version(void)
{
    return UINT32_C(1);
}

struct yuki_linux_runner_handle {
    struct yuki_linux_runner_child child;
};

static int set_error(int *error_out, int error_number)
{
    if (error_out != NULL) *error_out = error_number;
    errno = error_number;
    return -1;
}

struct yuki_linux_runner_handle *yuki_linux_runner_handle_create(void)
{
    struct yuki_linux_runner_handle *handle =
        (struct yuki_linux_runner_handle *)calloc(1u, sizeof(*handle));
    if (handle == NULL) return NULL;
    handle->child.pid = -1;
    handle->child.pidfd = -1;
    handle->child.control_fd = -1;
    return handle;
}

int yuki_linux_runner_handle_spawn(struct yuki_linux_runner_handle *handle,
                                  const char *path,
                                  const uint8_t nonce[16],
                                  int timeout_ms,
                                  int *error_out)
{
    if (handle == NULL) return set_error(error_out, EINVAL);
    if (handle->child.pid > 0 || handle->child.pidfd >= 0 ||
        handle->child.control_fd >= 0)
        return set_error(error_out, EBUSY);
    if (yuki_linux_runner_spawn(path, nonce, timeout_ms, &handle->child) != 0)
        return set_error(error_out, errno);
    if (error_out != NULL) *error_out = 0;
    return 0;
}

int yuki_linux_runner_handle_abort(struct yuki_linux_runner_handle *handle,
                                  int timeout_ms,
                                  int *error_out)
{
    if (handle == NULL) return set_error(error_out, EINVAL);
    if (handle->child.pid <= 0 || handle->child.control_fd < 0)
        return set_error(error_out, ECHILD);
    if (yuki_linux_runner_child_abort(&handle->child, timeout_ms) != 0)
        return set_error(error_out, errno);
    if (error_out != NULL) *error_out = 0;
    return 0;
}

int yuki_linux_runner_handle_pid(const struct yuki_linux_runner_handle *handle,
                                 int *pid_out)
{
    if (handle == NULL || pid_out == NULL) {
        errno = EINVAL;
        return -1;
    }
    *pid_out = handle->child.pid;
    return 0;
}

int yuki_linux_runner_handle_destroy(struct yuki_linux_runner_handle *handle,
                                    int *error_out)
{
    if (handle == NULL) return set_error(error_out, EINVAL);
    if (handle->child.pid > 0 || handle->child.pidfd >= 0 ||
        handle->child.control_fd >= 0)
        return set_error(error_out, EBUSY);
    free(handle);
    if (error_out != NULL) *error_out = 0;
    return 0;
}
