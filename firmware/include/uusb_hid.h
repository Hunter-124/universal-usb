#ifndef UUSB_HID_H
#define UUSB_HID_H

#include <stdbool.h>
#include <stdint.h>

#include "uusb_mailbox.h"

#define UUSB_HID_KEYBOARD_KEY_CAPACITY 6U
#define UUSB_HID_TAP_MIN_MS UINT16_C(10)
#define UUSB_HID_TAP_MAX_MS UINT16_C(5000)
#define UUSB_HID_MOUSE_BUTTON_MASK UINT8_C(0x07)

typedef struct {
    uusb_keyboard_payload_t keyboard;
    uusb_mouse_payload_t mouse;
    uusb_consumer_payload_t consumer;
} uusb_hid_state_t;

typedef struct {
    uint8_t modifier;
    uint8_t usage;
} uusb_hid_key_t;

void uusb_hid_state_clear(uusb_hid_state_t *state);
bool uusb_hid_keyboard_report_is_valid(const uusb_keyboard_payload_t *report);
bool uusb_hid_keyboard_set(
    uusb_hid_state_t *state, const uusb_keyboard_payload_t *report);
bool uusb_hid_keyboard_down(
    uusb_hid_state_t *state, uint8_t modifier, uint8_t usage);
bool uusb_hid_keyboard_up(
    uusb_hid_state_t *state, uint8_t modifier, uint8_t usage);
bool uusb_hid_mouse_set(
    uusb_hid_state_t *state, const uusb_mouse_payload_t *report);
bool uusb_hid_consumer_set(
    uusb_hid_state_t *state, const uusb_consumer_payload_t *report);
bool uusb_hid_tap_duration_is_valid(uint16_t milliseconds);
bool uusb_hid_us_key(char character, uusb_hid_key_t *key);

#endif
