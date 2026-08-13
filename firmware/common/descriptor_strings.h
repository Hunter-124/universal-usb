#ifndef UUSB_DESCRIPTOR_STRINGS_H
#define UUSB_DESCRIPTOR_STRINGS_H

#include <stdint.h>

const uint16_t *uusb_descriptor_string(
    uint8_t index,
    uint16_t language_id,
    const char *product);

#endif
