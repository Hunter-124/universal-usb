#include "uusb_hid.h"

#include <stddef.h>

#define UUSB_HID_MODIFIER_LEFT_SHIFT UINT8_C(0x02)

static bool key_is_present(const uusb_keyboard_payload_t *report, uint8_t usage)
{
    for (size_t index = 0U; index < UUSB_HID_KEYBOARD_KEY_CAPACITY; ++index) {
        if (report->keys[index] == usage) {
            return true;
        }
    }
    return false;
}

void uusb_hid_state_clear(uusb_hid_state_t *state)
{
    if (state != NULL) {
        *state = (uusb_hid_state_t){0};
    }
}

bool uusb_hid_keyboard_report_is_valid(const uusb_keyboard_payload_t *report)
{
    if ((report == NULL) || (report->reserved != 0U)) {
        return false;
    }
    for (size_t index = 0U; index < UUSB_HID_KEYBOARD_KEY_CAPACITY; ++index) {
        uint8_t const usage = report->keys[index];
        if (usage == 0U) {
            continue;
        }
        for (size_t other = index + 1U;
             other < UUSB_HID_KEYBOARD_KEY_CAPACITY; ++other) {
            if (report->keys[other] == usage) {
                return false;
            }
        }
    }
    return true;
}

bool uusb_hid_keyboard_set(
    uusb_hid_state_t *state, const uusb_keyboard_payload_t *report)
{
    if ((state == NULL) || !uusb_hid_keyboard_report_is_valid(report)) {
        return false;
    }
    state->keyboard = *report;
    return true;
}

bool uusb_hid_keyboard_down(
    uusb_hid_state_t *state, uint8_t modifier, uint8_t usage)
{
    if ((state == NULL) || (usage == 0U)) {
        return false;
    }
    state->keyboard.modifiers |= modifier;
    if (key_is_present(&state->keyboard, usage)) {
        return true;
    }
    for (size_t index = 0U; index < UUSB_HID_KEYBOARD_KEY_CAPACITY; ++index) {
        if (state->keyboard.keys[index] == 0U) {
            state->keyboard.keys[index] = usage;
            return true;
        }
    }
    return false;
}

bool uusb_hid_keyboard_up(
    uusb_hid_state_t *state, uint8_t modifier, uint8_t usage)
{
    if (state == NULL) {
        return false;
    }
    state->keyboard.modifiers &= (uint8_t)~modifier;
    for (size_t index = 0U; index < UUSB_HID_KEYBOARD_KEY_CAPACITY; ++index) {
        if (state->keyboard.keys[index] == usage) {
            for (size_t next = index + 1U;
                 next < UUSB_HID_KEYBOARD_KEY_CAPACITY; ++next) {
                state->keyboard.keys[next - 1U] = state->keyboard.keys[next];
            }
            state->keyboard.keys[UUSB_HID_KEYBOARD_KEY_CAPACITY - 1U] = 0U;
            return true;
        }
    }
    return usage == 0U;
}

bool uusb_hid_mouse_set(
    uusb_hid_state_t *state, const uusb_mouse_payload_t *report)
{
    if ((state == NULL) || (report == NULL) ||
        ((report->buttons & (uint8_t)~UUSB_HID_MOUSE_BUTTON_MASK) != 0U)) {
        return false;
    }
    state->mouse = *report;
    return true;
}

bool uusb_hid_consumer_set(
    uusb_hid_state_t *state, const uusb_consumer_payload_t *report)
{
    if ((state == NULL) || (report == NULL)) {
        return false;
    }
    switch (report->usage) {
    case 0U:
    case UUSB_CONSUMER_VOLUME_UP:
    case UUSB_CONSUMER_VOLUME_DOWN:
    case UUSB_CONSUMER_MUTE:
    case UUSB_CONSUMER_PLAY_PAUSE:
    case UUSB_CONSUMER_NEXT:
    case UUSB_CONSUMER_PREVIOUS:
    case UUSB_CONSUMER_STOP:
        state->consumer = *report;
        return true;
    default:
        return false;
    }
}

bool uusb_hid_tap_duration_is_valid(uint16_t milliseconds)
{
    return (milliseconds >= UUSB_HID_TAP_MIN_MS) &&
           (milliseconds <= UUSB_HID_TAP_MAX_MS);
}

static bool letter_key(char character, uusb_hid_key_t *key)
{
    if ((character >= 'a') && (character <= 'z')) {
        key->modifier = 0U;
        key->usage = (uint8_t)(4 + (character - 'a'));
        return true;
    }
    if ((character >= 'A') && (character <= 'Z')) {
        key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT;
        key->usage = (uint8_t)(4 + (character - 'A'));
        return true;
    }
    return false;
}

static bool number_key(char character, uusb_hid_key_t *key)
{
    static const char shifted[] = "!@#$%^&*()";
    if ((character >= '1') && (character <= '9')) {
        key->modifier = 0U;
        key->usage = (uint8_t)(30 + (character - '1'));
        return true;
    }
    if (character == '0') {
        key->modifier = 0U;
        key->usage = 39U;
        return true;
    }
    for (size_t index = 0U; index < sizeof(shifted) - 1U; ++index) {
        if (character == shifted[index]) {
            key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT;
            key->usage = (uint8_t)(30U + index);
            return true;
        }
    }
    return false;
}

bool uusb_hid_us_key(char character, uusb_hid_key_t *key)
{
    if ((key == NULL) || letter_key(character, key) ||
        number_key(character, key)) {
        return key != NULL;
    }
    key->modifier = 0U;
    switch (character) {
    case '\n': key->usage = 40U; break;
    case '\x1b': key->usage = 41U; break;
    case '\b': key->usage = 42U; break;
    case '\t': key->usage = 43U; break;
    case ' ': key->usage = 44U; break;
    case '-': key->usage = 45U; break;
    case '=': key->usage = 46U; break;
    case '[': key->usage = 47U; break;
    case ']': key->usage = 48U; break;
    case '\\': key->usage = 49U; break;
    case ';': key->usage = 51U; break;
    case '\'': key->usage = 52U; break;
    case '`': key->usage = 53U; break;
    case ',': key->usage = 54U; break;
    case '.': key->usage = 55U; break;
    case '/': key->usage = 56U; break;
    case '_': key->usage = 45U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '+': key->usage = 46U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '{': key->usage = 47U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '}': key->usage = 48U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '|': key->usage = 49U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case ':': key->usage = 51U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '"': key->usage = 52U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '~': key->usage = 53U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '<': key->usage = 54U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '>': key->usage = 55U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    case '?': key->usage = 56U; key->modifier = UUSB_HID_MODIFIER_LEFT_SHIFT; break;
    default: return false;
    }
    return true;
}
