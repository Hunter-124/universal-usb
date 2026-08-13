#include <stddef.h>

#include "usb_lifecycle.h"

static uusb_usb_lifecycle_state_t lifecycle_fail(
    uusb_usb_lifecycle_t *lifecycle,
    uusb_usb_lifecycle_error_t error)
{
    lifecycle->error = error;
    lifecycle->state = UUSB_USB_LIFECYCLE_FAILED;
    return lifecycle->state;
}

static uusb_usb_lifecycle_state_t lifecycle_fail_after_pin_change(
    uusb_usb_lifecycle_t *lifecycle,
    const uusb_usb_lifecycle_hooks_t *hooks,
    void *context,
    uusb_usb_lifecycle_error_t error)
{
    /* Do not leave PA12 driven low after a failed disconnect attempt. */
    if (!hooks->pa11_pa12_restore_usb_mode(context)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_PIN_RESTORE);
    }
    return lifecycle_fail(lifecycle, error);
}

void uusb_usb_lifecycle_reset(uusb_usb_lifecycle_t *lifecycle)
{
    if (lifecycle == NULL) {
        return;
    }
    lifecycle->state = UUSB_USB_LIFECYCLE_IDLE;
    lifecycle->error = UUSB_USB_LIFECYCLE_ERROR_NONE;
}

uusb_usb_lifecycle_state_t uusb_usb_lifecycle_begin(
    uusb_usb_lifecycle_t *lifecycle,
    const uusb_usb_lifecycle_hooks_t *hooks,
    void *context)
{
    if (lifecycle == NULL) {
        return UUSB_USB_LIFECYCLE_FAILED;
    }
    if ((hooks == NULL) ||
        (hooks->usb_peripheral_disable == NULL) ||
        (hooks->pa11_input_high_impedance == NULL) ||
        (hooks->pa12_output_low == NULL) ||
        (hooks->delay_ms_bounded == NULL) ||
        (hooks->pa11_pa12_restore_usb_mode == NULL) ||
        (hooks->tinyusb_init == NULL)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_INVALID_ARGUMENT);
    }
    if ((lifecycle->state != UUSB_USB_LIFECYCLE_IDLE) &&
        (lifecycle->state != UUSB_USB_LIFECYCLE_TARGET_VERIFIED) &&
        (lifecycle->state != UUSB_USB_LIFECYCLE_FAILED)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_INVALID_STATE);
    }

    lifecycle->error = UUSB_USB_LIFECYCLE_ERROR_NONE;
    lifecycle->state = UUSB_USB_LIFECYCLE_DISCONNECTING;

    if (!hooks->usb_peripheral_disable(context)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_PERIPHERAL_DISABLE);
    }
    if (!hooks->pa11_input_high_impedance(context)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_PA11_HIGH_IMPEDANCE);
    }
    if (!hooks->pa12_output_low(context)) {
        return lifecycle_fail_after_pin_change(
            lifecycle, hooks, context, UUSB_USB_LIFECYCLE_ERROR_PA12_LOW);
    }
    if (!hooks->delay_ms_bounded(context, UUSB_USB_DISCONNECT_MS)) {
        return lifecycle_fail_after_pin_change(
            lifecycle, hooks, context,
            UUSB_USB_LIFECYCLE_ERROR_DISCONNECT_DELAY);
    }
    if (!hooks->pa11_pa12_restore_usb_mode(context)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_PIN_RESTORE);
    }

    lifecycle->state = UUSB_USB_LIFECYCLE_INITIALIZING;
    if (!hooks->tinyusb_init(context)) {
        return lifecycle_fail(
            lifecycle, UUSB_USB_LIFECYCLE_ERROR_TINYUSB_INIT);
    }

    lifecycle->state =
        UUSB_USB_LIFECYCLE_AWAITING_TARGET_REENUMERATION;
    return lifecycle->state;
}

bool uusb_usb_lifecycle_mark_target_verified(
    uusb_usb_lifecycle_t *lifecycle,
    bool fresh_configured_transition,
    bool expected_profile_verified)
{
    if ((lifecycle == NULL) ||
        (lifecycle->state !=
         UUSB_USB_LIFECYCLE_AWAITING_TARGET_REENUMERATION) ||
        !fresh_configured_transition || !expected_profile_verified) {
        return false;
    }

    lifecycle->state = UUSB_USB_LIFECYCLE_TARGET_VERIFIED;
    lifecycle->error = UUSB_USB_LIFECYCLE_ERROR_NONE;
    return true;
}

bool uusb_usb_lifecycle_is_target_verified(
    const uusb_usb_lifecycle_t *lifecycle)
{
    return (lifecycle != NULL) &&
           (lifecycle->state == UUSB_USB_LIFECYCLE_TARGET_VERIFIED);
}
