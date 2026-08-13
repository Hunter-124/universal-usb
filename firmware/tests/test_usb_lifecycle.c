#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "usb_lifecycle.h"

typedef struct {
    char calls[7];
    size_t count;
    uint32_t delay_ms;
} test_context_t;

static bool record(test_context_t *context, char call)
{
    assert(context->count < sizeof(context->calls));
    context->calls[context->count++] = call;
    return true;
}

static bool disable_usb(void *opaque)
{
    return record(opaque, 'D');
}

static bool pa11_high_impedance(void *opaque)
{
    return record(opaque, 'H');
}

static bool pa12_low(void *opaque)
{
    return record(opaque, 'L');
}

static bool delay_bounded(void *opaque, uint32_t milliseconds)
{
    test_context_t *context = opaque;
    context->delay_ms = milliseconds;
    return record(context, 'W');
}

static bool restore_usb(void *opaque)
{
    return record(opaque, 'R');
}

static bool init_tinyusb(void *opaque)
{
    return record(opaque, 'I');
}

int main(void)
{
    const uusb_usb_lifecycle_hooks_t hooks = {
        .usb_peripheral_disable = disable_usb,
        .pa11_input_high_impedance = pa11_high_impedance,
        .pa12_output_low = pa12_low,
        .delay_ms_bounded = delay_bounded,
        .pa11_pa12_restore_usb_mode = restore_usb,
        .tinyusb_init = init_tinyusb,
    };
    test_context_t context = {0};
    uusb_usb_lifecycle_t lifecycle;

    uusb_usb_lifecycle_reset(&lifecycle);
    assert(uusb_usb_lifecycle_begin(&lifecycle, &hooks, &context) ==
           UUSB_USB_LIFECYCLE_AWAITING_TARGET_REENUMERATION);
    assert(context.count == 6U);
    assert(memcmp(context.calls, "DHLWRI", 6U) == 0);
    assert(context.delay_ms == UUSB_USB_DISCONNECT_MS);
    assert(!uusb_usb_lifecycle_is_target_verified(&lifecycle));
    assert(!uusb_usb_lifecycle_mark_target_verified(&lifecycle, true, false));
    assert(uusb_usb_lifecycle_mark_target_verified(&lifecycle, true, true));
    assert(uusb_usb_lifecycle_is_target_verified(&lifecycle));
    return 0;
}
