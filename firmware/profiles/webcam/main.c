#include "uusb_firmware.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "platform.h"
#include "stm32f1xx.h"
#include "tusb.h"
#include "uusb_uvc_pattern.h"

#define UUSB_UVC_FRAME_PERIOD_MS UINT32_C(100)
#define UUSB_UVC_INFO_LENGTH UINT16_C(16)

typedef struct {
    uusb_uvc_pattern_t selected_pattern;
    uusb_uvc_pattern_t render_pattern;
    bool mounted;
    bool suspended;
    bool committed;
    bool transfer_busy;
    bool schedule_started;
    bool reset_pending;
    uint32_t next_frame_ms;
    uint32_t render_frame;
    uint32_t frames_completed;
    uint32_t payloads_generated;
} uusb_webcam_profile_t;

static uusb_webcam_profile_t profile;

static void put_u16_le(uint8_t *destination, uint16_t value)
{
    destination[0] = (uint8_t)value;
    destination[1] = (uint8_t)(value >> 8U);
}

static void put_u32_le(uint8_t *destination, uint32_t value)
{
    destination[0] = (uint8_t)value;
    destination[1] = (uint8_t)(value >> 8U);
    destination[2] = (uint8_t)(value >> 16U);
    destination[3] = (uint8_t)(value >> 24U);
}

static void reset_stream_state(uusb_webcam_profile_t *state)
{
    state->render_pattern = state->selected_pattern;
    state->committed = false;
    state->transfer_busy = false;
    state->schedule_started = false;
    state->next_frame_ms = 0U;
    state->render_frame = 0U;
    state->frames_completed = 0U;
    state->payloads_generated = 0U;
}

static void invalidate_local_state(void *context)
{
    uusb_webcam_profile_t *const state = context;
    state->selected_pattern = UUSB_UVC_BARS;
    state->mounted = false;
    state->suspended = false;
    state->reset_pending = false;
    reset_stream_state(state);
}

static uusb_control_status_t validate_command(
    void *context, uusb_control_opcode_t opcode,
    const uint8_t *request, uint16_t request_length)
{
    (void)context;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RESET:
        return (request_length == 0U) ?
                   UUSB_STATUS_OK : UUSB_STATUS_BAD_LENGTH;
    case UUSB_OPCODE_UVC_PATTERN:
        if ((request == NULL) ||
            (request_length != sizeof(uusb_uvc_pattern_payload_t))) {
            return UUSB_STATUS_BAD_LENGTH;
        }
        return uusb_uvc_pattern_is_valid((uusb_uvc_pattern_t)request[0]) ?
                   UUSB_STATUS_OK : UUSB_STATUS_OUT_OF_RANGE;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
}

static uusb_control_status_t execute_command(
    void *context, uusb_control_opcode_t opcode,
    const uint8_t *request, uint16_t request_length,
    uint8_t *response, uint16_t *response_length)
{
    uusb_webcam_profile_t *const state = context;
    (void)request_length;
    *response_length = 0U;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
        response[0] = (uint8_t)state->selected_pattern;
        response[1] = state->mounted ? 1U : 0U;
        response[2] = (state->mounted && !state->suspended &&
                       tud_video_n_streaming(0U, 0U)) ? 1U : 0U;
        response[3] = state->suspended ? 1U : 0U;
        put_u16_le(response + 4U, UUSB_UVC_WIDTH);
        put_u16_le(response + 6U, UUSB_UVC_HEIGHT);
        put_u32_le(response + 8U, state->frames_completed);
        put_u32_le(response + 12U, state->payloads_generated);
        *response_length = UUSB_UVC_INFO_LENGTH;
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_UVC_PATTERN:
        state->selected_pattern = (uusb_uvc_pattern_t)request[0];
        return UUSB_STATUS_OK;
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

static void fail_pending_block(
    void *context, uusb_control_status_t status)
{
    (void)context;
    (void)status;
}

static void fail_pending_token(
    void *context, uusb_control_status_t status)
{
    (void)context;
    (void)status;
}

static const uusb_mailbox_hooks_t hooks = {
    .invalidate_local_state = invalidate_local_state,
    .validate_command = validate_command,
    .execute_command = execute_command,
    .release_hid = release_hid,
    .fail_pending_block = fail_pending_block,
    .fail_pending_token = fail_pending_token,
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

    bool const class_streaming = tud_video_n_streaming(0U, 0U);
    if (!class_streaming) {
        profile.transfer_busy = false;
    }
    bool const streaming = profile.mounted && !profile.suspended &&
                           profile.committed && class_streaming;
    if (!streaming) {
        profile.schedule_started = false;
        return;
    }

    uint32_t const now = uusb_mailbox_uptime_ms();
    if (!profile.schedule_started) {
        profile.next_frame_ms = now;
        profile.schedule_started = true;
    }
    if (profile.transfer_busy ||
        ((int32_t)(now - profile.next_frame_ms) < 0)) {
        return;
    }

    profile.render_pattern = profile.selected_pattern;
    profile.render_frame = profile.frames_completed;
    if (tud_video_n_frame_xfer(
            0U, 0U, NULL, (size_t)UUSB_UVC_FRAME_BYTES)) {
        profile.transfer_busy = true;
        profile.next_frame_ms += UUSB_UVC_FRAME_PERIOD_MS;
        if ((int32_t)(now - profile.next_frame_ms) >= 0) {
            profile.next_frame_ms = now + UUSB_UVC_FRAME_PERIOD_MS;
        }
    }
}

void uusb_profile_mount(void)
{
    profile.mounted = true;
    profile.suspended = false;
    reset_stream_state(&profile);
}

void uusb_profile_umount(void)
{
    profile.mounted = false;
    profile.suspended = false;
    reset_stream_state(&profile);
}

void uusb_profile_suspend(void)
{
    profile.suspended = true;
    profile.schedule_started = false;
}

void uusb_profile_resume(void)
{
    profile.suspended = false;
    profile.schedule_started = false;
}

void tud_video_prepare_payload_cb(
    uint_fast8_t control_index, uint_fast8_t streaming_index,
    tud_video_payload_request_t *request)
{
    if ((control_index != 0U) || (streaming_index != 0U) ||
        (request == NULL) || (request->buf == NULL)) {
        return;
    }
    uusb_uvc_pattern_fill(
        profile.render_pattern, profile.render_frame,
        (uint32_t)request->offset, request->buf, request->length);
    ++profile.payloads_generated;
}

void tud_video_frame_xfer_complete_cb(
    uint_fast8_t control_index, uint_fast8_t streaming_index)
{
    if ((control_index == 0U) && (streaming_index == 0U)) {
        profile.transfer_busy = false;
        ++profile.frames_completed;
    }
}

int tud_video_commit_cb(
    uint_fast8_t control_index, uint_fast8_t streaming_index,
    const video_probe_and_commit_control_t *parameters)
{
    if ((control_index != 0U) || (streaming_index != 0U) ||
        (parameters == NULL) ||
        (parameters->bFormatIndex != 1U) ||
        (parameters->bFrameIndex != 1U) ||
        (parameters->dwFrameInterval != UUSB_UVC_FRAME_INTERVAL_100NS)) {
        return VIDEO_ERROR_INVALID_VALUE_WITHIN_RANGE;
    }
    profile.committed = true;
    profile.schedule_started = false;
    return VIDEO_ERROR_NONE;
}

int main(void)
{
    return uusb_firmware_run_with_hooks(
        UUSB_PROFILE_WEBCAM, &hooks, &profile);
}
