#ifndef UUSB_CRC32C_H
#define UUSB_CRC32C_H

#include <stddef.h>
#include <stdint.h>

#define UUSB_CRC32C_POLYNOMIAL UINT32_C(0x1edc6f41)
#define UUSB_CRC32C_REFLECTED_POLYNOMIAL UINT32_C(0x82f63b78)
#define UUSB_CRC32C_INITIAL UINT32_C(0xffffffff)
#define UUSB_CRC32C_XOROUT UINT32_C(0xffffffff)
#define UUSB_CRC32C_CHECK UINT32_C(0xe3069283)

uint32_t uusb_crc32c_begin(void);
uint32_t uusb_crc32c_update(uint32_t state, const void *data, size_t length);
uint32_t uusb_crc32c_finish(uint32_t state);
uint32_t uusb_crc32c(const void *data, size_t length);

#endif
