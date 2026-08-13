#include "uusb_crc32c.h"

#include <stdint.h>

uint32_t uusb_crc32c_begin(void)
{
    return UUSB_CRC32C_INITIAL;
}

uint32_t uusb_crc32c_update(uint32_t state, const void *data, size_t length)
{
    const uint8_t *bytes = data;
    if ((bytes == NULL) && (length != 0U)) {
        return state;
    }

    for (size_t index = 0U; index < length; ++index) {
        state ^= bytes[index];
        for (unsigned int bit = 0U; bit < 8U; ++bit) {
            uint32_t const mask = UINT32_C(0) - (state & UINT32_C(1));
            state = (state >> 1) ^ (UUSB_CRC32C_REFLECTED_POLYNOMIAL & mask);
        }
    }
    return state;
}

uint32_t uusb_crc32c_finish(uint32_t state)
{
    return state ^ UUSB_CRC32C_XOROUT;
}

uint32_t uusb_crc32c(const void *data, size_t length)
{
    return uusb_crc32c_finish(uusb_crc32c_update(uusb_crc32c_begin(), data, length));
}
