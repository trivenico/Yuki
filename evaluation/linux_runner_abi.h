#ifndef YUKI_LINUX_RUNNER_ABI_H
#define YUKI_LINUX_RUNNER_ABI_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Opaque lifecycle-only ABI. This does not enable isolation or candidate exec. */
struct yuki_linux_runner_handle;

/* Increment whenever exported signatures or ownership semantics change. */
uint32_t yuki_linux_runner_abi_version(void);

struct yuki_linux_runner_handle *yuki_linux_runner_handle_create(void);
int yuki_linux_runner_handle_spawn(struct yuki_linux_runner_handle *handle,
                                  const char *absolute_helper_path,
                                  const uint8_t nonce[16],
                                  int timeout_ms,
                                  int *error_out);
int yuki_linux_runner_handle_abort(struct yuki_linux_runner_handle *handle,
                                  int timeout_ms,
                                  int *error_out);
int yuki_linux_runner_handle_pid(const struct yuki_linux_runner_handle *handle,
                                 int *pid_out);
int yuki_linux_runner_handle_destroy(struct yuki_linux_runner_handle *handle,
                                    int *error_out);

#ifdef __cplusplus
}
#endif

#endif
