#define _GNU_SOURCE
#include "linux_runner_abi.h"

#include <assert.h>
#include <errno.h>
#include <stdint.h>

int main(void)
{
    static const uint8_t nonce[16] = {0};
    struct yuki_linux_runner_handle *handle = yuki_linux_runner_handle_create();
    int pid = 0;
    int error_number = 0;

    assert(handle != 0);
    assert(yuki_linux_runner_abi_version() == UINT32_C(1));
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid == -1);

    /* A failed pre-spawn attempt must leave the opaque handle destroyable. */
    assert(yuki_linux_runner_handle_spawn(handle, "/yuki/missing-helper",
                                          nonce, 100, &error_number) == -1);
    assert(error_number != 0);
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid == -1);
    assert(yuki_linux_runner_handle_destroy(handle, &error_number) == 0);
    assert(error_number == 0);
    return 0;
}
