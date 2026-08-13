#ifndef UUSB_STM32F1_PLATFORM_H
#define UUSB_STM32F1_PLATFORM_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    UUSB_CLOCK_UNINITIALIZED = 0,
    UUSB_CLOCK_VALID,
    UUSB_CLOCK_HSE_FAILED
} uusb_clock_state_t;

bool uusb_platform_clock_configure(void);
uusb_clock_state_t uusb_platform_clock_state(void);

bool uusb_platform_usb_peripheral_disable(void *context);
bool uusb_platform_pa11_input_high_impedance(void *context);
bool uusb_platform_pa12_output_low(void *context);
bool uusb_platform_delay_ms_bounded(void *context, uint32_t milliseconds);
bool uusb_platform_pa11_pa12_restore_usb_mode(void *context);
bool uusb_platform_tinyusb_init(void *context);

#endif
