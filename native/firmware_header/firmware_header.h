#ifndef YUKI_FIRMWARE_HEADER_H
#define YUKI_FIRMWARE_HEADER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define YUKI_FW_FORMAT_UNKNOWN 0u
#define YUKI_FW_FORMAT_UBOOT 1u
#define YUKI_FW_FORMAT_FIT 2u
#define YUKI_FW_FORMAT_TRX 3u

typedef struct yuki_fw_header_info {
    uint32_t format;
    uint32_t header_size;
    uint64_t total_size;
    uint32_t version;
} yuki_fw_header_info;

/*
 * Inspect an immutable caller-owned buffer.
 * Returns 1 when recognized, 0 when unknown, and -1 when malformed.
 * Never reads beyond data_len and never allocates or accesses the filesystem.
 */
int yuki_fw_inspect(const uint8_t *data, size_t data_len,
                    yuki_fw_header_info *out_info);

#ifdef __cplusplus
}
#endif
#endif
