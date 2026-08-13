#ifndef UUSB_STM32F1_USB_LIFECYCLE_H
#define UUSB_STM32F1_USB_LIFECYCLE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UUSB_USB_DISCONNECT_MS UINT32_C(20)

typedef enum {
    UUSB_USB_LIFECYCLE_IDLE = 0,
    UUSB_USB_LIFECYCLE_DISCONNECTING,
    UUSB_USB_LIFECYCLE_INITIALIZING,
    UUSB_USB_LIFECYCLE_AWAITING_TARGET_REENUMERATION,
    UUSB_USB_LIFECYCLE_TARGET_VERIFIED,
    UUSB_USB_LIFECYCLE_FAILED
} uusb_usb_lifecycle_state_t;

typedef enum {
    UUSB_USB_LIFECYCLE_ERROR_NONE = 0,
    UUSB_USB_LIFECYCLE_ERROR_INVALID_ARGUMENT,
    UUSB_USB_LIFECYCLE_ERROR_INVALID_STATE,
    UUSB_USB_LIFECYCLE_ERROR_PERIPHERAL_DISABLE,
    UUSB_USB_LIFECYCLE_ERROR_PA11_HIGH_IMPEDANCE,
    UUSB_USB_LIFECYCLE_ERROR_PA12_LOW,
    UUSB_USB_LIFECYCLE_ERROR_DISCONNECT_DELAY,
    UUSB_USB_LIFECYCLE_ERROR_PIN_RESTORE,
    UUSB_USB_LIFECYCLE_ERROR_TINYUSB_INIT
} uusb_usb_lifecycle_error_t;

/*
 * Board operations are synchronous and must be bounded. In particular,
 * delay_ms_bounded must return after the requested interval or a board-defined
 * finite deadline; it must not poll an unbounded hardware-ready condition.
 */
typedef struct {
    bool (*usb_peripheral_disable)(void *context);
    bool (*pa11_input_high_impedance)(void *context);
    bool (*pa12_output_low)(void *context);
    bool (*delay_ms_bounded)(void *context, uint32_t milliseconds);
    bool (*pa11_pa12_restore_usb_mode)(void *context);
    bool (*tinyusb_init)(void *context);
} uusb_usb_lifecycle_hooks_t;

typedef struct {
    uusb_usb_lifecycle_state_t state;
    uusb_usb_lifecycle_error_t error;
} uusb_usb_lifecycle_t;

void uusb_usb_lifecycle_reset(uusb_usb_lifecycle_t *lifecycle);

/*
 * Perform the controlled disconnect and initialize TinyUSB. A completed call
 * stops in AWAITING_TARGET_REENUMERATION, not TARGET_VERIFIED. The operation is
 * allocation-free and contains no polling loop.
 */
uusb_usb_lifecycle_state_t uusb_usb_lifecycle_begin(
    uusb_usb_lifecycle_t *lifecycle,
    const uusb_usb_lifecycle_hooks_t *hooks,
    void *context);

/*
 * Record later controller/host evidence. Both a fresh configured transition and
 * the expected profile identity are required before the verified state is set.
 */
bool uusb_usb_lifecycle_mark_target_verified(
    uusb_usb_lifecycle_t *lifecycle,
    bool fresh_configured_transition,
    bool expected_profile_verified);

bool uusb_usb_lifecycle_is_target_verified(
    const uusb_usb_lifecycle_t *lifecycle);

#ifdef __cplusplus
}
#endif

#endif
