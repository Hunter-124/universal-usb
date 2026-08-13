#ifndef UUSB_STATUS_H
#define UUSB_STATUS_H

#include <stdbool.h>
#include <stdint.h>

#include "platform.h"
#include "uusb_firmware.h"
#include "usb_lifecycle.h"

typedef enum {
    UUSB_STATUS_BOOTING = 0,
    UUSB_STATUS_CLOCK_HSE_FAILED,
    UUSB_STATUS_USB_START_FAILED,
    UUSB_STATUS_RUNNING
} uusb_runtime_state_t;

typedef struct {
    uusb_profile_t profile;
    uusb_runtime_state_t runtime;
    uusb_clock_state_t clock;
    uusb_usb_lifecycle_state_t usb_lifecycle;
    uusb_usb_lifecycle_error_t usb_error;
    bool mounted;
    bool suspended;
    bool watchdog_enabled;
} uusb_status_t;

void uusb_status_initialize(uusb_profile_t profile, uusb_clock_state_t clock);
void uusb_status_set_runtime(uusb_runtime_state_t runtime);
void uusb_status_set_lifecycle(const uusb_usb_lifecycle_t *lifecycle);
void uusb_status_set_mounted(bool mounted);
void uusb_status_set_suspended(bool suspended);
void uusb_status_set_watchdog(bool enabled);
const volatile uusb_status_t *uusb_status_get(void);

#endif
