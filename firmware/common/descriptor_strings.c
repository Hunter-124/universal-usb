#include "descriptor_strings.h"

#include <stddef.h>

#include "tusb.h"

#define UUSB_UID_ADDRESS UINT32_C(0x1ffff7e8)
#define UUSB_UID_BYTES 12U
#define UUSB_STRING_CAPACITY 32U

static uint16_t descriptor[UUSB_STRING_CAPACITY + 1U];

static size_t ascii_to_utf16(const char *text)
{
    size_t count = 0U;
    while ((text[count] != '\0') && (count < UUSB_STRING_CAPACITY)) {
        descriptor[count + 1U] = (uint8_t)text[count];
        ++count;
    }
    return count;
}

static size_t uid_to_utf16(void)
{
    static char const hexadecimal[] = "0123456789ABCDEF";
    volatile uint8_t const *const uid =
        (volatile uint8_t const *)(uintptr_t)UUSB_UID_ADDRESS;
    for (size_t index = 0U; index < UUSB_UID_BYTES; ++index) {
        uint8_t const byte = uid[index];
        descriptor[1U + (index * 2U)] = (uint16_t)hexadecimal[byte >> 4U];
        descriptor[2U + (index * 2U)] =
            (uint16_t)hexadecimal[byte & UINT8_C(0x0f)];
    }
    return UUSB_UID_BYTES * 2U;
}

const uint16_t *uusb_descriptor_string(
    uint8_t index,
    uint16_t language_id,
    const char *product)
{
    (void)language_id;
    size_t count;
    if (index == 0U) {
        descriptor[1] = UINT16_C(0x0409);
        count = 1U;
    } else if (index == 1U) {
        count = ascii_to_utf16("Universal USB");
    } else if (index == 2U) {
        count = ascii_to_utf16(product);
    } else if (index == 3U) {
        count = uid_to_utf16();
    } else {
        return NULL;
    }

    descriptor[0] = (uint16_t)((TUSB_DESC_STRING << 8U) | (2U + (count * 2U)));
    return descriptor;
}
