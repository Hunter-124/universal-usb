#include "uusb_firmware.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "device/usbd_pvt.h"
#include "platform.h"
#include "stm32f1xx.h"
#include "tusb.h"
#include "uusb_token_usb.h"

#include "uusb_hid.h"
#define UUSB_FIDO_INSTANCE 0U
#define UUSB_OTP_INSTANCE 1U
#define UUSB_CCID_INTERFACE 1U
#define UUSB_CCID_OUT UINT8_C(0x02)
#define UUSB_CCID_IN UINT8_C(0x82)
#define UUSB_CCID_INTERRUPT UINT8_C(0x83)
#define UUSB_CCID_PACKET_SIZE 64U
#define UUSB_TOKEN_EXCHANGE_TIMEOUT_MS UINT32_C(1000)
#define UUSB_KEEPALIVE_INTERVAL_MS UINT32_C(100)
#define UUSB_RESET_RELEASE_TIMEOUT_MS UINT32_C(100)
#define UUSB_COMPILER_BARRIER() __asm__ volatile("" ::: "memory")

typedef struct {
    uusb_token_usb_t usb;
    bool mounted;
    bool suspended;
    bool reset_pending;
    uint32_t reset_deadline_ms;
    bool bridge_pending;
    bool bridge_failed;
    uint32_t bridge_sequence;
    uint32_t bridge_started_ms;
    uusb_token_usb_transport_t bridge_transport;
    uint32_t next_keepalive_ms;
    bool fido_in_flight;
    uint32_t bridge_previous_ack;
    bool otp_in_flight;
    bool ccid_out_armed;
    bool ccid_in_flight;
    bool ccid_interrupt_in_flight;
    uint8_t ccid_out[UUSB_CCID_PACKET_SIZE];
    uint8_t ccid_in[UUSB_CCID_PACKET_SIZE];
    uint8_t ccid_interrupt[8];
} uusb_security_profile_t;

static uusb_security_profile_t profile;

static void copy_to_volatile(
    volatile uint8_t *destination, const uint8_t *source, uint16_t length)
{
    for (uint16_t index = 0U; index < length; ++index) {
        destination[index] = source[index];
    }
}

static void copy_from_volatile(
    uint8_t *destination, const volatile uint8_t *source, uint16_t length)
{
    for (uint16_t index = 0U; index < length; ++index) {
        destination[index] = source[index];
    }
}

static void acknowledge_response(void)
{
    UUSB_COMPILER_BARRIER();
    uusb_mailbox.token_response.response_ack =
        uusb_mailbox.token_response.response_seq;
}

static void bridge_cancel(void *context, uusb_token_usb_transport_t transport)
{
    uusb_security_profile_t *state = context;
    (void)transport;
    if (state->bridge_pending) {
        state->bridge_pending = false;
        state->bridge_failed = true;
        acknowledge_response();
    }
}

static bool bridge_start(
    void *context, uusb_token_usb_transport_t transport,
    const uint8_t *request, uint16_t request_length)
{
    uusb_security_profile_t *state = context;
    volatile uusb_token_request_slot_t *slot = &uusb_mailbox.token_request;
    if (state->bridge_pending || request_length > UUSB_MAILBOX_TOKEN_DATA_SIZE ||
        slot->request_seq != slot->request_ack || !state->mounted ||
        state->suspended) {
        return false;
    }

    uint32_t sequence = slot->request_seq + 1U;
    if (sequence == 0U) {
        sequence = 1U;
    }
    slot->transport = transport == UUSB_TOKEN_USB_TRANSPORT_CTAPHID
                          ? UUSB_TOKEN_FIDO_HID
                          : UUSB_TOKEN_CCID;
    slot->length = request_length;
    slot->flags = 0U;
    copy_to_volatile(slot->data, request, request_length);
    slot->request_crc = uusb_mailbox_token_request_crc(
        (uusb_token_transport_t)slot->transport, request_length, 0U, request);
    state->bridge_pending = true;
    state->bridge_failed = false;
    state->bridge_sequence = sequence;
    state->bridge_previous_ack = slot->request_ack;
    state->bridge_started_ms = uusb_mailbox_uptime_ms();
    state->bridge_transport = transport;
    UUSB_COMPILER_BARRIER();
    slot->request_seq = sequence;
    return true;
}

static uusb_token_usb_exchange_result_t bridge_poll(
    void *context, uusb_token_usb_transport_t transport,
    uint8_t *response, uint16_t response_capacity, uint16_t *response_length)
{
    uusb_security_profile_t *state = context;
    volatile uusb_token_request_slot_t *request = &uusb_mailbox.token_request;
    volatile uusb_token_response_slot_t *slot = &uusb_mailbox.token_response;
    *response_length = 0U;
    if (state->bridge_failed) {
        state->bridge_failed = false;
        return UUSB_TOKEN_USB_EXCHANGE_FAILED;
    }
    if (!state->bridge_pending || transport != state->bridge_transport) {
        return UUSB_TOKEN_USB_EXCHANGE_FAILED;
    }
    if ((uint32_t)(uusb_mailbox_uptime_ms() - state->bridge_started_ms) >=
        UUSB_TOKEN_EXCHANGE_TIMEOUT_MS) {
        state->bridge_pending = false;
        return UUSB_TOKEN_USB_EXCHANGE_FAILED;
    }
    if (request->request_ack != state->bridge_sequence) {
        if (request->request_ack != state->bridge_previous_ack) {
            state->bridge_pending = false;
            return UUSB_TOKEN_USB_EXCHANGE_FAILED;
        }
        return UUSB_TOKEN_USB_EXCHANGE_PENDING;
    }

    uint32_t const response_sequence = slot->response_seq;
    UUSB_COMPILER_BARRIER();
    if (response_sequence == slot->response_ack) {
        return UUSB_TOKEN_USB_EXCHANGE_PENDING;
    }
    uint32_t const request_sequence = slot->request_seq;
    uint16_t const length = slot->length;
    uint16_t const reserved = slot->reserved;
    uint32_t const expected_crc = slot->response_crc;
    if (length <= UUSB_MAILBOX_TOKEN_DATA_SIZE && length <= response_capacity) {
        copy_from_volatile(response, slot->data, length);
    }
    UUSB_COMPILER_BARRIER();
    bool const stable = slot->response_seq == response_sequence;
    acknowledge_response();
    state->bridge_pending = false;
    if (!stable || request_sequence != state->bridge_sequence || reserved != 0U ||
        length > UUSB_MAILBOX_TOKEN_DATA_SIZE || length > response_capacity ||
        expected_crc != uusb_mailbox_token_response_crc(
                            request_sequence, length, response)) {
        return UUSB_TOKEN_USB_EXCHANGE_FAILED;
    }
    *response_length = length;
    return UUSB_TOKEN_USB_EXCHANGE_COMPLETE;
}

static const uusb_token_usb_hooks_t token_hooks = {
    .start = bridge_start,
    .poll = bridge_poll,
    .cancel = bridge_cancel,
};

static void invalidate_local_state(void *context)
{
    uusb_security_profile_t *state = context;
    uusb_token_usb_initialize(&state->usb, &token_hooks, state);
    state->mounted = false;
    state->suspended = false;
    state->reset_pending = false;
    state->bridge_pending = false;
    state->bridge_failed = false;
    state->fido_in_flight = false;
    state->otp_in_flight = false;
    state->ccid_out_armed = false;
    state->ccid_in_flight = false;
    state->ccid_interrupt_in_flight = false;
}

static uusb_control_status_t validate_command(
    void *context, uusb_control_opcode_t opcode,
    const uint8_t *request, uint16_t request_length)
{
    (void)context;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RELEASE_ALL:
    case UUSB_OPCODE_RESET:
        return request_length == 0U ? UUSB_STATUS_OK : UUSB_STATUS_BAD_LENGTH;
    case UUSB_OPCODE_HID_KEYBOARD:
        return request_length == sizeof(uusb_keyboard_payload_t) &&
                       uusb_hid_keyboard_report_is_valid(
                           (const uusb_keyboard_payload_t *)request)
                   ? UUSB_STATUS_OK : UUSB_STATUS_BAD_LENGTH;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
}

static uusb_control_status_t execute_command(
    void *context, uusb_control_opcode_t opcode,
    const uint8_t *request, uint16_t request_length,
    uint8_t *response, uint16_t *response_length)
{
    uusb_security_profile_t *state = context;
    (void)request_length;
    *response_length = 0U;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
        response[0] = state->mounted ? 1U : 0U;
        response[1] = state->suspended ? 1U : 0U;
        response[2] = state->bridge_pending ? 1U : 0U;
        response[3] = state->usb.ccid_powered ? 1U : 0U;
        *response_length = 4U;
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_HID_KEYBOARD:
        return uusb_token_usb_otp_report_set(&state->usb, request)
                   ? UUSB_STATUS_OK : UUSB_STATUS_BUSY;
    case UUSB_OPCODE_RELEASE_ALL:
        uusb_token_usb_fail_cancel(&state->usb);
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_RESET:
        uusb_token_usb_fail_cancel(&state->usb);
        state->reset_pending = true;
        state->reset_deadline_ms =
            uusb_mailbox_uptime_ms() + UUSB_RESET_RELEASE_TIMEOUT_MS;
        return UUSB_STATUS_OK;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
}

static void release_hid(void *context)
{
    uusb_security_profile_t *state = context;
    uusb_token_usb_otp_fail(&state->usb);
}

static void fail_pending_token(void *context, uusb_control_status_t status)
{
    uusb_security_profile_t *state = context;
    (void)status;
    state->bridge_pending = false;
    state->bridge_failed = true;
    uusb_token_usb_fail_timeout(&state->usb);
    uusb_token_usb_otp_fail(&state->usb);
}

static const uusb_mailbox_hooks_t mailbox_hooks = {
    .invalidate_local_state = invalidate_local_state,
    .validate_command = validate_command,
    .execute_command = execute_command,
    .release_hid = release_hid,
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

static void service_hid_in(void)
{
    uint8_t report[UUSB_CTAPHID_REPORT_SIZE];
    if (!profile.fido_in_flight && tud_hid_n_ready(UUSB_FIDO_INSTANCE) &&
        uusb_token_usb_ctaphid_in_peek(&profile.usb, report) &&
        tud_hid_n_report(UUSB_FIDO_INSTANCE, 0U, report, sizeof(report))) {
        profile.fido_in_flight = true;
    }
    if (!profile.otp_in_flight && tud_hid_n_ready(UUSB_OTP_INSTANCE) &&
        uusb_token_usb_otp_in_peek(&profile.usb, report) &&
        tud_hid_n_report(UUSB_OTP_INSTANCE, 0U, report,
                         UUSB_TOKEN_USB_OTP_REPORT_SIZE)) {
        profile.otp_in_flight = true;
    }
}

static void arm_ccid_out(void)
{
    if (profile.mounted && !profile.suspended && !profile.ccid_out_armed &&
        usbd_edpt_xfer(0U, UUSB_CCID_OUT, profile.ccid_out,
                       sizeof(profile.ccid_out), false)) {
        profile.ccid_out_armed = true;
    }
}

static void service_ccid_in(void)
{
    if (!profile.ccid_in_flight) {
        size_t length = uusb_token_usb_ccid_in_peek(
            &profile.usb, profile.ccid_in, sizeof(profile.ccid_in));
        if (length != 0U && usbd_edpt_xfer(
                0U, UUSB_CCID_IN, profile.ccid_in, (uint16_t)length, false)) {
            profile.ccid_in_flight = true;
        }
    }
}

void uusb_profile_task(void)
{
    uint32_t const now = uusb_mailbox_uptime_ms();
    uusb_token_usb_tick(&profile.usb, now);
    uusb_token_usb_poll(&profile.usb, now);
    if (profile.usb.exchange_owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID &&
        (int32_t)(now - profile.next_keepalive_ms) >= 0) {
        if (uusb_token_usb_keepalive(
                &profile.usb, UUSB_CTAPHID_KEEPALIVE_PROCESSING)) {
            profile.next_keepalive_ms = now + UUSB_KEEPALIVE_INTERVAL_MS;
        }
    } else if (profile.usb.exchange_owner == UUSB_TOKEN_USB_EXCHANGE_CCID &&
               (int32_t)(now - profile.next_keepalive_ms) >= 0) {
        if (uusb_token_usb_time_extension(&profile.usb, 1U)) {
            profile.next_keepalive_ms = now + UUSB_KEEPALIVE_INTERVAL_MS;
        }
    }
    if (profile.mounted && !profile.suspended) {
        service_hid_in();
        arm_ccid_out();
        service_ccid_in();
    }
    if (profile.reset_pending &&
        (profile.usb.otp_phase == UUSB_TOKEN_USB_OTP_IDLE ||
         (int32_t)(now - profile.reset_deadline_ms) >= 0)) {
        perform_reset();
    }
}

void uusb_profile_mount(void)
{
    profile.mounted = true;
    profile.suspended = false;
    profile.next_keepalive_ms = uusb_mailbox_uptime_ms();
}

void uusb_profile_umount(void)
{
    profile.mounted = false;
    profile.suspended = false;
    profile.ccid_out_armed = false;
    profile.ccid_in_flight = false;
    uusb_token_usb_fail_unmount(&profile.usb);
}

void uusb_profile_suspend(void)
{
    profile.suspended = true;
    fail_pending_token(&profile, UUSB_STATUS_IO_ERROR);
}

void uusb_profile_resume(void)
{
    profile.suspended = false;
}

uint16_t tud_hid_get_report_cb(
    uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
    uint8_t *buffer, uint16_t requested_length)
{
    (void)instance; (void)report_id; (void)report_type;
    (void)buffer; (void)requested_length;
    return 0U;
}

void tud_hid_set_report_cb(
    uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
    const uint8_t *buffer, uint16_t buffer_size)
{
    (void)report_id;
    if (instance == UUSB_FIDO_INSTANCE && report_type == HID_REPORT_TYPE_OUTPUT &&
        buffer_size == UUSB_CTAPHID_REPORT_SIZE) {
        (void)uusb_token_usb_ctaphid_out(
            &profile.usb, buffer, uusb_mailbox_uptime_ms());
    }
}

void tud_hid_report_complete_cb(
    uint8_t instance, const uint8_t *report, uint16_t length)
{
    (void)report;
    if (instance == UUSB_FIDO_INSTANCE &&
        length == UUSB_CTAPHID_REPORT_SIZE && profile.fido_in_flight) {
        profile.fido_in_flight = false;
        (void)uusb_token_usb_ctaphid_in_accepted(&profile.usb);
    } else if (instance == UUSB_OTP_INSTANCE &&
               length == UUSB_TOKEN_USB_OTP_REPORT_SIZE &&
               profile.otp_in_flight) {
        profile.otp_in_flight = false;
        (void)uusb_token_usb_otp_in_accepted(&profile.usb);
        if (profile.usb.otp_phase == UUSB_TOKEN_USB_OTP_IDLE) {
            uusb_mailbox_hid_release_complete();
        }
    }
}

static void ccid_init(void) {}
static bool ccid_deinit(void) { return true; }
static void ccid_reset(uint8_t rhport)
{
    (void)rhport;
    profile.ccid_out_armed = false;
    profile.ccid_in_flight = false;
    profile.ccid_interrupt_in_flight = false;
}

static uint16_t ccid_open(
    uint8_t rhport, const tusb_desc_interface_t *interface, uint16_t max_length)
{
    if (interface->bInterfaceClass != TUSB_CLASS_SMART_CARD ||
        interface->bInterfaceNumber != UUSB_CCID_INTERFACE) {
        return 0U;
    }
    const uint8_t *cursor = (const uint8_t *)interface;
    uint16_t consumed = interface->bLength;
    cursor += interface->bLength;
    while (consumed < max_length && cursor[0] != 0U) {
        if (cursor[1] == TUSB_DESC_INTERFACE) {
            break;
        }
        if (cursor[1] == TUSB_DESC_ENDPOINT) {
            if (!usbd_edpt_open(rhport, (const tusb_desc_endpoint_t *)cursor)) {
                return 0U;
            }
        }
        consumed = (uint16_t)(consumed + cursor[0]);
        cursor += cursor[0];
        if (consumed >= 84U) {
            break;
        }
    }
    return consumed;
}

static bool ccid_control(
    uint8_t rhport, uint8_t stage, const tusb_control_request_t *request)
{
    if (stage == CONTROL_STAGE_SETUP && request->bRequest == 1U) {
        uusb_token_usb_fail_cancel(&profile.usb);
        return tud_control_status(rhport, request);
    }
    return false;
}

static bool ccid_transfer(
    uint8_t rhport, uint8_t endpoint, xfer_result_t result,
    uint32_t transferred)
{
    (void)rhport;
    if (endpoint == UUSB_CCID_OUT) {
        profile.ccid_out_armed = false;
        if (result == XFER_RESULT_SUCCESS && transferred != 0U &&
            transferred <= sizeof(profile.ccid_out)) {
            (void)uusb_token_usb_ccid_out(
                &profile.usb, profile.ccid_out, transferred,
                uusb_mailbox_uptime_ms());
        } else if (result != XFER_RESULT_SUCCESS) {
            uusb_token_usb_ccid_in_failed(&profile.usb);
        }
    } else if (endpoint == UUSB_CCID_IN) {
        profile.ccid_in_flight = false;
        if (result == XFER_RESULT_SUCCESS) {
            (void)uusb_token_usb_ccid_in_accepted(&profile.usb, transferred);
        } else {
            uusb_token_usb_ccid_in_failed(&profile.usb);
        }
    } else if (endpoint == UUSB_CCID_INTERRUPT) {
        profile.ccid_interrupt_in_flight = false;
    }
    return true;
}

static const usbd_class_driver_t ccid_driver = {
    .name = "CCID",
    .init = ccid_init,
    .deinit = ccid_deinit,
    .reset = ccid_reset,
    .open = ccid_open,
    .control_xfer_cb = ccid_control,
    .xfer_cb = ccid_transfer,
    .xfer_isr = NULL,
    .sof = NULL,
};

const usbd_class_driver_t *usbd_app_driver_get_cb(uint8_t *driver_count)
{
    *driver_count = 1U;
    return &ccid_driver;
}

int main(void)
{
    return uusb_firmware_run_with_hooks(
        UUSB_PROFILE_SECURITY_TOKEN, &mailbox_hooks, &profile);
}
