#include <assert.h>
#include <stdint.h>

#include "uusb_hid.h"

int main(void)
{
    uusb_hid_state_t state;
    uusb_hid_state_clear(&state);
    for (uint8_t usage = 4U; usage < 10U; ++usage) {
        assert(uusb_hid_keyboard_down(&state, 0U, usage));
    }
    assert(!uusb_hid_keyboard_down(&state, 0U, 10U));
    assert(uusb_hid_keyboard_up(&state, 0U, 6U));
    assert(uusb_hid_keyboard_down(&state, 0U, 10U));

    uusb_keyboard_payload_t duplicate = {
        .modifiers = 0U, .reserved = 0U, .keys = {4U, 4U, 0U, 0U, 0U, 0U}
    };
    assert(!uusb_hid_keyboard_report_is_valid(&duplicate));
    duplicate.keys[1] = 5U;
    assert(uusb_hid_keyboard_report_is_valid(&duplicate));

    uusb_hid_key_t key;
    assert(uusb_hid_us_key('a', &key));
    assert(key.modifier == 0U && key.usage == 4U);
    assert(uusb_hid_us_key('A', &key));
    assert(key.modifier == 2U && key.usage == 4U);
    assert(uusb_hid_us_key('!', &key));
    assert(key.modifier == 2U && key.usage == 30U);
    assert(uusb_hid_us_key('\n', &key) && key.usage == 40U);
    assert(!uusb_hid_us_key('\x01', &key));

    assert(!uusb_hid_tap_duration_is_valid(9U));
    assert(uusb_hid_tap_duration_is_valid(10U));
    assert(uusb_hid_tap_duration_is_valid(5000U));
    assert(!uusb_hid_tap_duration_is_valid(5001U));

    uusb_mouse_payload_t mouse = {.buttons = 7U, .dx = -1, .dy = 2, .wheel = 3};
    assert(uusb_hid_mouse_set(&state, &mouse));
    mouse.buttons = 8U;
    assert(!uusb_hid_mouse_set(&state, &mouse));

    uusb_consumer_payload_t consumer = {.usage = UUSB_CONSUMER_MUTE};
    assert(uusb_hid_consumer_set(&state, &consumer));
    consumer.usage = 0U;
    assert(uusb_hid_consumer_set(&state, &consumer));
    consumer.usage = UINT16_C(0xffff);
    assert(!uusb_hid_consumer_set(&state, &consumer));
    return 0;
}
