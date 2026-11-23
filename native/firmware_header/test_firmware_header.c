#include "firmware_header.h"

#include <assert.h>
#include <stdint.h>
#include <string.h>

int main(void) {
    uint8_t image[80] = {0};
    yuki_fw_header_info info = {0};

    image[0] = 0x27u; image[1] = 0x05u; image[2] = 0x19u; image[3] = 0x56u;
    image[15] = 4u;
    assert(yuki_fw_inspect(image, sizeof(image), &info) == 1);
    assert(info.format == YUKI_FW_FORMAT_UBOOT);
    assert(info.header_size == 64u);
    assert(info.total_size == 68u);

    memset(image, 0, sizeof(image));
    memcpy(image, "HDR0", 4u);
    image[4] = 32u;
    assert(yuki_fw_inspect(image, sizeof(image), &info) == 1);
    assert(info.format == YUKI_FW_FORMAT_TRX);
    assert(info.total_size == 32u);

    memset(image, 0, sizeof(image));
    memcpy(image, "NOPE", 4u);
    assert(yuki_fw_inspect(image, sizeof(image), &info) == 0);
    assert(yuki_fw_inspect(NULL, sizeof(image), &info) == -1);
    assert(yuki_fw_inspect(image, sizeof(image), NULL) == -1);

    memset(image, 0, sizeof(image));
    image[0] = 0x27u; image[1] = 0x05u; image[2] = 0x19u; image[3] = 0x56u;
    image[12] = 0u; image[13] = 0u; image[14] = 0u; image[15] = 100u;
    assert(yuki_fw_inspect(image, 64u, &info) == -1);
    return 0;
}
