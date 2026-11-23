#include "firmware_header.h"

#include <limits.h>
#include <string.h>

#define UBOOT_HEADER_SIZE 64u
#define FIT_HEADER_SIZE 40u
#define TRX_HEADER_SIZE 28u

static uint32_t read_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

int yuki_fw_inspect(const uint8_t *data, size_t data_len,
                    yuki_fw_header_info *out_info) {
    yuki_fw_header_info result = {0u, 0u, 0u, 0u};

    if (data == NULL || out_info == NULL) {
        return -1;
    }
    if (data_len < 4u) {
        return -1;
    }

    if (data[0] == 0x27u && data[1] == 0x05u &&
        data[2] == 0x19u && data[3] == 0x56u) {
        uint64_t payload_size;
        if (data_len < UBOOT_HEADER_SIZE) {
            return -1;
        }
        payload_size = (uint64_t)read_be32(data + 12u);
        if (payload_size > (uint64_t)data_len - UBOOT_HEADER_SIZE) {
            return -1;
        }
        result.format = YUKI_FW_FORMAT_UBOOT;
        result.header_size = UBOOT_HEADER_SIZE;
        result.total_size = UBOOT_HEADER_SIZE + payload_size;
        result.version = 0u;
    } else if (data[0] == 0xd0u && data[1] == 0x0du &&
               data[2] == 0xfeu && data[3] == 0xedu) {
        uint32_t total_size;
        if (data_len < FIT_HEADER_SIZE) {
            return -1;
        }
        total_size = read_be32(data + 4u);
        if (total_size < FIT_HEADER_SIZE || (uint64_t)total_size > (uint64_t)data_len) {
            return -1;
        }
        result.format = YUKI_FW_FORMAT_FIT;
        result.header_size = FIT_HEADER_SIZE;
        result.total_size = total_size;
        result.version = read_be32(data + 20u);
    } else if (data[0] == 'H' && data[1] == 'D' &&
               data[2] == 'R' && data[3] == '0') {
        uint32_t total_size;
        if (data_len < TRX_HEADER_SIZE) {
            return -1;
        }
        total_size = read_le32(data + 4u);
        if (total_size < TRX_HEADER_SIZE || (uint64_t)total_size > (uint64_t)data_len) {
            return -1;
        }
        result.format = YUKI_FW_FORMAT_TRX;
        result.header_size = TRX_HEADER_SIZE;
        result.total_size = total_size;
        result.version = read_le32(data + 12u) >> 16;
    } else {
        return 0;
    }

    *out_info = result;
    return 1;
}
