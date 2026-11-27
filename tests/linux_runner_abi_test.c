#define _GNU_SOURCE
#include "linux_runner_abi.h"

#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    static const uint8_t nonce[16] = {
        'a','b','i','-','l','i','f','e','c','y','c','l','e','-','0','1'
    };
    struct yuki_linux_runner_handle *handle;
    int pid = 0;
    int error_number = 0;

    assert(argc == 2);
    assert(argv[1][0] == '/');
    handle = yuki_linux_runner_handle_create();
    assert(handle != NULL);
    assert(yuki_linux_runner_abi_version() == UINT32_C(1));
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid == -1);

    /* A failed pre-spawn attempt leaves an empty, destroyable handle. */
    assert(yuki_linux_runner_handle_spawn(handle, "/yuki/missing-helper",
                                          nonce, 100, &error_number) == -1);
    assert(error_number != 0);
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid == -1);

    /* Exercise the real opaque ABI lifecycle against the inert helper. */
    error_number = 0;
    assert(yuki_linux_runner_handle_spawn(handle, argv[1], nonce, 5000,
                                          &error_number) == 0);
    assert(error_number == 0);
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid > 0);

    /* Destroy must not discard a live child or its recovery descriptors. */
    error_number = 0;
    assert(yuki_linux_runner_handle_destroy(handle, &error_number) == -1);
    assert(error_number == EBUSY);
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid > 0);

    error_number = 0;
    assert(yuki_linux_runner_handle_abort(handle, 5000, &error_number) == 0);
    assert(error_number == 0);
    assert(yuki_linux_runner_handle_pid(handle, &pid) == 0);
    assert(pid == -1);
    assert(yuki_linux_runner_handle_destroy(handle, &error_number) == 0);
    assert(error_number == 0);
    return 0;
}
