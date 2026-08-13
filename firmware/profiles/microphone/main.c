#include "uusb_firmware.h"

#include <stdbool.h>
#include <stdint.h>

#include "platform.h"
#include "stm32f1xx.h"
#include "tusb.h"
#include "uusb_tone.h"

#define UUSB_AUDIO_STREAMING_INTERFACE 1U
#define UUSB_AUDIO_IN_ENDPOINT UINT8_C(0x81)
#define UUSB_MIC_INFO_LENGTH 16U
_Static_assert(UUSB_TONE_PACKET_BYTES == 96U,
               "audio packet must match the endpoint");
_Static_assert(UUSB_MIC_INFO_LENGTH <= UUSB_MAILBOX_CONTROL_PAYLOAD_SIZE,
               "microphone GET_INFO exceeds the mailbox response");

typedef struct {
    uusb_tone_state_t tone;
    bool mounted;
    bool suspended;
    volatile bool streaming;
    volatile bool refill_pending;
    volatile bool ignore_initial_zlp;
    bool reset_pending;
    bool packet_prepared;
    uint16_t packet_bytes_queued;
    uint8_t packet[UUSB_TONE_PACKET_BYTES];
} uusb_microphone_profile_t;

static uusb_microphone_profile_t profile;

static uint16_t get_u16_le(const uint8_t *bytes)
{
    return (uint16_t)((uint16_t)bytes[0] |
                      (uint16_t)((uint16_t)bytes[1] << 8U));
}

static void put_u16_le(uint8_t *bytes, uint16_t value)
{
    bytes[0] = (uint8_t)value;
    bytes[1] = (uint8_t)(value >> 8U);
}

static void put_u32_le(uint8_t *bytes, uint32_t value)
{
    bytes[0] = (uint8_t)value;
    bytes[1] = (uint8_t)(value >> 8U);
    bytes[2] = (uint8_t)(value >> 16U);
    bytes[3] = (uint8_t)(value >> 24U);
}

static void invalidate_local_state(void *context)
{
    uusb_microphone_profile_t *const state = context;
    uusb_tone_initialize(&state->tone);
    state->mounted = false;
    state->suspended = false;
    state->streaming = false;
    state->refill_pending = false;
    state->ignore_initial_zlp = false;
    state->reset_pending = false;
    state->packet_prepared = false;
    state->packet_bytes_queued = 0U;
}

static uusb_control_status_t validate_command(
    void *context,
    uusb_control_opcode_t opcode,
    const uint8_t *request,
    uint16_t request_length)
{
    (void)context;
    (void)request_length;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RESET:
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_MIC_CONFIG:
        return uusb_tone_config_is_valid(
                   (uusb_mic_mode_t)request[0],
                   get_u16_le(&request[2]),
                   get_u16_le(&request[4]))
                   ? UUSB_STATUS_OK
                   : UUSB_STATUS_OUT_OF_RANGE;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
}

static uusb_control_status_t execute_command(
    void *context,
    uusb_control_opcode_t opcode,
    const uint8_t *request,
    uint16_t request_length,
    uint8_t *response,
    uint16_t *response_length)
{
    uusb_microphone_profile_t *const state = context;
    (void)request_length;
    *response_length = 0U;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
        response[0] = (uint8_t)state->tone.mode;
        response[1] = state->mounted ? 1U : 0U;
        response[2] = state->streaming ? 1U : 0U;
        response[3] = state->suspended ? 1U : 0U;
        put_u16_le(&response[4], state->tone.frequency_hz);
        put_u16_le(&response[6], state->tone.amplitude_q15);
        put_u32_le(&response[8], state->tone.packet_count);
        put_u32_le(&response[12], state->tone.underrun_count);
        *response_length = UUSB_MIC_INFO_LENGTH;
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_MIC_CONFIG:
        return uusb_tone_configure(
                   &state->tone,
                   (uusb_mic_mode_t)request[0],
                   get_u16_le(&request[2]),
                   get_u16_le(&request[4]))
                   ? UUSB_STATUS_OK
                   : UUSB_STATUS_OUT_OF_RANGE;
    case UUSB_OPCODE_RESET:
        state->reset_pending = true;
        return UUSB_STATUS_OK;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
}

static void release_hid(void *context)
{
    (void)context;
    uusb_mailbox_hid_release_complete();
}

static const uusb_mailbox_hooks_t hooks = {
    .invalidate_local_state = invalidate_local_state,
    .validate_command = validate_command,
    .execute_command = execute_command,
    .release_hid = release_hid,
};

static void perform_reset(void)
{
    (void)uusb_platform_usb_peripheral_disable(NULL);
    (void)uusb_platform_pa11_input_high_impedance(NULL);
    (void)uusb_platform_pa12_output_low(NULL);
    (void)uusb_platform_delay_ms_bounded(NULL, 20U);
    NVIC_SystemReset();
}

void uusb_profile_task(void)
{
    if (profile.reset_pending) {
        perform_reset();
    }
    if (!profile.streaming || profile.suspended ||
        !profile.refill_pending) {
        return;
    }

    if (!profile.packet_prepared) {
        uusb_tone_fill_packet(&profile.tone, profile.packet);
        profile.packet_prepared = true;
    }
    uint16_t const remaining =
        (uint16_t)(UUSB_TONE_PACKET_BYTES - profile.packet_bytes_queued);
    uint16_t const written = tud_audio_write(
        &profile.packet[profile.packet_bytes_queued], remaining);
    profile.packet_bytes_queued =
        (uint16_t)(profile.packet_bytes_queued + written);
    if (profile.packet_bytes_queued == UUSB_TONE_PACKET_BYTES) {
        profile.packet_prepared = false;
        profile.packet_bytes_queued = 0U;
        profile.refill_pending = false;
    }
}

void uusb_profile_mount(void)
{
    profile.mounted = true;
    profile.suspended = false;
    profile.streaming = false;
    profile.refill_pending = false;
    profile.ignore_initial_zlp = false;
    profile.packet_prepared = false;
    profile.packet_bytes_queued = 0U;
}

void uusb_profile_umount(void)
{
    profile.mounted = false;
    profile.suspended = false;
    profile.streaming = false;
    profile.refill_pending = false;
    profile.ignore_initial_zlp = false;
    profile.packet_prepared = false;
    profile.packet_bytes_queued = 0U;
}

void uusb_profile_suspend(void)
{
    profile.suspended = true;
}

void uusb_profile_resume(void)
{
    profile.suspended = false;
}

bool tud_audio_set_itf_close_ep_cb(
    uint8_t rhport, tusb_control_request_t const *request)
{
    (void)rhport;
    (void)request;
    profile.streaming = false;
    profile.refill_pending = false;
    profile.ignore_initial_zlp = false;
    profile.packet_prepared = false;
    profile.packet_bytes_queued = 0U;
    return true;
}

bool tud_audio_set_itf_cb(
    uint8_t rhport, tusb_control_request_t const *request)
{
    (void)rhport;
    uint8_t const interface_number = tu_u16_low(request->wIndex);
    uint8_t const alternate_setting = tu_u16_low(request->wValue);
    if (interface_number != UUSB_AUDIO_STREAMING_INTERFACE) {
        return true;
    }

    profile.streaming = alternate_setting == 1U;
    profile.refill_pending = profile.streaming;
    profile.ignore_initial_zlp = profile.streaming;
    profile.packet_prepared = false;
    profile.packet_bytes_queued = 0U;
    return true;
}

bool tud_audio_tx_done_isr(
    uint8_t rhport,
    uint16_t bytes_sent,
    uint8_t function_id,
    uint8_t endpoint_in,
    uint8_t alternate_setting)
{
    (void)rhport;
    if ((function_id != 0U) ||
        (endpoint_in != UUSB_AUDIO_IN_ENDPOINT) ||
        (alternate_setting != 1U) || !profile.streaming) {
        return true;
    }

    if (profile.ignore_initial_zlp) {
        profile.ignore_initial_zlp = false;
    } else if (bytes_sent == UUSB_TONE_PACKET_BYTES) {
        uusb_tone_note_packet_sent(&profile.tone);
    } else {
        uusb_tone_note_underrun(&profile.tone);
    }
    profile.refill_pending = true;
    return true;
}

int main(void)
{
    return uusb_firmware_run_with_hooks(
        UUSB_PROFILE_MICROPHONE, &hooks, &profile);
}
