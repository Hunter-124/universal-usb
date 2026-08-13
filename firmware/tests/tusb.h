#ifndef UUSB_TEST_TUSB_H
#define UUSB_TEST_TUSB_H

#include <stdbool.h>
#include <stdint.h>

#define TUD_MSC_RET_ERROR (-1)
#define TUD_MSC_RET_ASYNC (-2)

bool tud_msc_set_sense(
    uint8_t lun, uint8_t sense_key, uint8_t add_sense_code,
    uint8_t add_sense_qualifier);
bool tud_msc_async_io_done(int32_t bytes_io, bool in_isr);

#endif
