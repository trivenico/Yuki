#include "firmware_header.h"

#include <stddef.h>
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    yuki_fw_header_info info = {0u, 0u, 0u, 0u};
    int result = yuki_fw_inspect(data, size, &info);
    if (result == 1) {
        if (info.header_size == 0u || info.total_size < info.header_size ||
            info.total_size > (uint64_t)size) {
            __builtin_trap();
        }
        if (info.format < YUKI_FW_FORMAT_UBOOT ||
            info.format > YUKI_FW_FORMAT_TRX) {
            __builtin_trap();
        }
    } else if (result != 0 && result != -1) {
        __builtin_trap();
    }
    return 0;
}
