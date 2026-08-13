#include "uusb_status.h"

#include <stddef.h>

static volatile uusb_status_t status;

void uusb_status_initialize(uusb_profile_t profile, uusb_clock_state_t clock)
{
    status.profile = profile;
    status.runtime = (clock == UUSB_CLOCK_VALID)
                         ? UUSB_STATUS_BOOTING
                         : UUSB_STATUS_CLOCK_HSE_FAILED;
    status.clock = clock;
    status.usb_lifecycle = UUSB_USB_LIFECYCLE_IDLE;
    status.usb_error = UUSB_USB_LIFECYCLE_ERROR_NONE;
    status.mounted = false;
    status.suspended = false;
    status.watchdog_enabled = false;
}

void uusb_status_set_runtime(uusb_runtime_state_t runtime)
{
    status.runtime = runtime;
}

void uusb_status_set_lifecycle(const uusb_usb_lifecycle_t *lifecycle)
{
    if (lifecycle != NULL) {
        status.usb_lifecycle = lifecycle->state;
        status.usb_error = lifecycle->error;
    }
}

void uusb_status_set_mounted(bool mounted)
{
    status.mounted = mounted;
}

void uusb_status_set_suspended(bool suspended)
{
    status.suspended = suspended;
}

void uusb_status_set_watchdog(bool enabled)
{
    status.watchdog_enabled = enabled;
}

const volatile uusb_status_t *uusb_status_get(void)
{
    return &status;
}
