#include "uusb_firmware.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "platform.h"
#include "stm32f1xx.h"
#include "tusb.h"
#include "uusb_hid.h"
#include "uusb_msc.h"

#define UUSB_HID_INSTANCE_KEYBOARD 0U
#define UUSB_HID_INSTANCE_MOUSE 1U
#define UUSB_HID_INSTANCE_CONSUMER 2U
#define UUSB_HID_INSTANCE_COUNT 3U
#define UUSB_RESET_RELEASE_DEADLINE_MS UINT32_C(100)
#define UUSB_SCSI_SYNCHRONIZE_CACHE_10 UINT8_C(0x35)
#define UUSB_SENSE_ILLEGAL_REQUEST UINT8_C(0x05)
#define UUSB_ASC_INVALID_COMMAND UINT8_C(0x20)

typedef struct {
    uusb_hid_state_t hid;
    uusb_msc_state_t msc;
    bool mounted;
    bool suspended;
    bool command_ready;
    bool release_active;
    bool reset_after_release;
    uint8_t release_required;
    uint8_t release_submitted;
    uint8_t release_accepted;
    uint8_t report_protocol[UUSB_HID_INSTANCE_COUNT];
    uint32_t reset_deadline_ms;
} uusb_hid_msc_profile_t;

static uusb_hid_msc_profile_t profile;

static bool report_is_zero(
    uint8_t instance, const uint8_t *report, uint16_t length)
{
    uint16_t expected = (instance == UUSB_HID_INSTANCE_KEYBOARD) ? 8U :
                        (instance == UUSB_HID_INSTANCE_MOUSE) ?
                            ((profile.report_protocol[instance] ==
                              HID_PROTOCOL_BOOT) ? 3U : 4U) : 2U;
    if ((report == NULL) || (length != expected)) {
        return false;
    }
    for (uint16_t index = 0U; index < length; ++index) {
        if (report[index] != 0U) {
            return false;
        }
    }
    return true;
}

static void invalidate_local_state(void *context)
{
    uusb_hid_msc_profile_t *const state = context;
    uusb_hid_state_clear(&state->hid);
    uusb_msc_initialize(&state->msc);
    state->mounted = false;
    state->suspended = false;
    state->command_ready = false;
    state->release_active = false;
    state->reset_after_release = false;
    state->release_required = 0U;
    state->release_submitted = 0U;
    state->release_accepted = 0U;
    for (size_t index = 0U; index < UUSB_HID_INSTANCE_COUNT; ++index) {
        state->report_protocol[index] = HID_PROTOCOL_REPORT;
    }
}

static void begin_release(uusb_hid_msc_profile_t *state, bool reset_after)
{
    uusb_hid_state_clear(&state->hid);
    state->command_ready = false;
    state->release_active = state->mounted && !state->suspended;
    uusb_mailbox.header.usb_flags |=
        (uint32_t)UUSB_USB_FLAG_HID_RELEASE_PENDING;
    state->reset_after_release = reset_after;
    state->release_required = UINT8_C(0x07);
    state->release_submitted = 0U;
    state->release_accepted = 0U;
    if (reset_after) {
        state->reset_deadline_ms =
            uusb_mailbox_uptime_ms() + UUSB_RESET_RELEASE_DEADLINE_MS;
    }
    if (!state->release_active && !reset_after) {
        uusb_mailbox_hid_release_complete();
    }
}

static void release_hid(void *context)
{
    begin_release(context, false);
}

static void fail_pending_block(
    void *context, uusb_control_status_t status)
{
    uusb_hid_msc_profile_t *const state = context;
    if (status == UUSB_STATUS_TIMEOUT) {
        uusb_msc_fail_pending(&state->msc, status);
    } else {
        uusb_msc_abandon(&state->msc, status);
    }
}

static void fail_pending_token(
    void *context, uusb_control_status_t status)
{
    (void)context;
    (void)status;
}

static bool send_current_report(uint8_t instance)
{
    if (!tud_hid_n_ready(instance)) {
        return false;
    }
    switch (instance) {
    case UUSB_HID_INSTANCE_KEYBOARD:
        return tud_hid_n_report(instance, 0U, &profile.hid.keyboard,
                                sizeof(profile.hid.keyboard));
    case UUSB_HID_INSTANCE_MOUSE:
        if (profile.report_protocol[instance] == HID_PROTOCOL_BOOT) {
            uint8_t const boot_report[3] = {
                profile.hid.mouse.buttons,
                (uint8_t)profile.hid.mouse.dx,
                (uint8_t)profile.hid.mouse.dy,
            };
            return tud_hid_n_report(
                instance, 0U, boot_report, sizeof(boot_report));
        }
        return tud_hid_n_report(instance, 0U, &profile.hid.mouse,
                                sizeof(profile.hid.mouse));
    case UUSB_HID_INSTANCE_CONSUMER:
        return tud_hid_n_report(instance, 0U, &profile.hid.consumer,
                                sizeof(profile.hid.consumer));
    default:
        return false;
    }
}

static uusb_control_status_t validate_command(
    void *context, uusb_control_opcode_t opcode,
    const uint8_t *request, uint16_t request_length)
{
    uusb_hid_msc_profile_t *const state = context;
    (void)request_length;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RELEASE_ALL:
    case UUSB_OPCODE_RESET:
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_MEDIA_ATTACH:
        return (!state->msc.present && !state->msc.pending)
                   ? UUSB_STATUS_OK : UUSB_STATUS_BUSY;
    case UUSB_OPCODE_MEDIA_DETACH:
        if (!state->msc.present) {
            return UUSB_STATUS_BAD_STATE;
        }
        return (state->msc.pending || state->msc.removal_prevented)
                   ? UUSB_STATUS_BUSY : UUSB_STATUS_OK;
    case UUSB_OPCODE_HID_KEYBOARD:
        if (!state->command_ready) {
            return UUSB_STATUS_BAD_STATE;
        }
        return uusb_hid_keyboard_report_is_valid(
                   (const uusb_keyboard_payload_t *)request)
                   ? UUSB_STATUS_OK : UUSB_STATUS_OUT_OF_RANGE;
    case UUSB_OPCODE_HID_MOUSE:
    case UUSB_OPCODE_HID_CONSUMER:
        return state->command_ready ? UUSB_STATUS_OK : UUSB_STATUS_BAD_STATE;
    default:
        return UUSB_STATUS_OK;
    }
}

static uint32_t get_u32_le(const uint8_t *bytes)
{
    return ((uint32_t)bytes[0]) | ((uint32_t)bytes[1] << 8U) |
           ((uint32_t)bytes[2] << 16U) | ((uint32_t)bytes[3] << 24U);
}

static uint16_t get_u16_le(const uint8_t *bytes)
{
    return (uint16_t)(((uint16_t)bytes[0]) | ((uint16_t)bytes[1] << 8U));
}

static uusb_control_status_t execute_command(
    void *context, uusb_control_opcode_t opcode,
    const uint8_t *request, uint16_t request_length,
    uint8_t *response, uint16_t *response_length)
{
    uusb_hid_msc_profile_t *const state = context;
    (void)request_length;
    (void)response;
    *response_length = 0U;
    switch (opcode) {
    case UUSB_OPCODE_GET_INFO:
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_RELEASE_ALL:
        begin_release(state, false);
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_HID_KEYBOARD:
        if (!uusb_hid_keyboard_set(
                &state->hid, (const uusb_keyboard_payload_t *)request) ||
            !send_current_report(UUSB_HID_INSTANCE_KEYBOARD)) {
            begin_release(state, false);
            return UUSB_STATUS_BUSY;
        }
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_HID_MOUSE:
        if (!uusb_hid_mouse_set(
                &state->hid, (const uusb_mouse_payload_t *)request) ||
            !send_current_report(UUSB_HID_INSTANCE_MOUSE)) {
            begin_release(state, false);
            return UUSB_STATUS_BUSY;
        }
        return UUSB_STATUS_OK;
    case UUSB_OPCODE_HID_CONSUMER: {
        uusb_consumer_payload_t report = {.usage = get_u16_le(request)};
        if (!uusb_hid_consumer_set(&state->hid, &report) ||
            !send_current_report(UUSB_HID_INSTANCE_CONSUMER)) {
            begin_release(state, false);
            return UUSB_STATUS_BUSY;
        }
        return UUSB_STATUS_OK;
    }
    case UUSB_OPCODE_MEDIA_ATTACH:
        return uusb_msc_attach(
            &state->msc, get_u32_le(request), request[4] != 0U);
    case UUSB_OPCODE_MEDIA_DETACH:
        return uusb_msc_detach(&state->msc);
    case UUSB_OPCODE_RESET:
        begin_release(state, true);
        return UUSB_STATUS_OK;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
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
    uusb_msc_poll(&profile.msc);
    if (profile.release_active) {
        for (uint8_t instance = 0U;
             instance < UUSB_HID_INSTANCE_COUNT; ++instance) {
            uint8_t const bit = (uint8_t)(1U << instance);
            if (((profile.release_submitted & bit) == 0U) &&
                send_current_report(instance)) {
                profile.release_submitted |= bit;
            } else if (((profile.release_submitted & bit) != 0U) &&
                       ((profile.release_accepted & bit) == 0U) &&
                       tud_hid_n_ready(instance)) {
                profile.release_submitted &= (uint8_t)~bit;
            }
        }
        if (profile.release_accepted == profile.release_required) {
            profile.release_active = false;
            uusb_mailbox_hid_release_complete();
            if (profile.reset_after_release) {
                perform_reset();
            } else {
                profile.command_ready = profile.mounted && !profile.suspended;
            }
        }
    }
    if (profile.reset_after_release &&
        ((int32_t)(uusb_mailbox_uptime_ms() - profile.reset_deadline_ms) >= 0)) {
        perform_reset();
    }
}

void uusb_profile_mount(void)
{
    profile.mounted = true;
    profile.suspended = false;
    begin_release(&profile, false);
}

void uusb_profile_umount(void)
{
    profile.mounted = false;
    profile.command_ready = false;
    profile.release_active = false;
    uusb_hid_state_clear(&profile.hid);
    uusb_msc_bot_reset(&profile.msc);
}

void uusb_profile_suspend(void)
{
    profile.suspended = true;
    profile.command_ready = false;
    profile.release_active = false;
    uusb_hid_state_clear(&profile.hid);
}

void uusb_profile_resume(void)
{
    profile.suspended = false;
    if (profile.mounted) {
        begin_release(&profile, false);
    }
}

void tud_hid_set_protocol_cb(uint8_t instance, uint8_t protocol)
{
    if (instance < UUSB_HID_INSTANCE_COUNT) {
        profile.report_protocol[instance] = protocol;
        if (instance == UUSB_HID_INSTANCE_MOUSE) {
            profile.hid.mouse.wheel = 0;
        }
    }
}

void tud_hid_report_complete_cb(
    uint8_t instance, const uint8_t *report, uint16_t length)
{
    if (profile.release_active && (instance < UUSB_HID_INSTANCE_COUNT) &&
        report_is_zero(instance, report, length)) {
        profile.release_accepted |= (uint8_t)(1U << instance);
    }
}

void tud_hid_report_failed_cb(
    uint8_t instance, hid_report_type_t report_type,
    const uint8_t *report, uint16_t transferred)
{
    (void)report_type;
    (void)report;
    (void)transferred;
    if (profile.release_active && (instance < UUSB_HID_INSTANCE_COUNT)) {
        profile.release_submitted &= (uint8_t)~(1U << instance);
    } else {
        begin_release(&profile, false);
    }
}

uint16_t tud_hid_get_report_cb(
    uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
    uint8_t *buffer, uint16_t requested_length)
{
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)requested_length;
    return 0U;
}

void tud_hid_set_report_cb(
    uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
    const uint8_t *buffer, uint16_t buffer_size)
{
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)buffer_size;
}

void tud_msc_inquiry_cb(
    uint8_t lun, uint8_t vendor_id[8], uint8_t product_id[16],
    uint8_t product_revision[4])
{
    static const uint8_t vendor[8] = {
        'U', 'U', 'S', 'B', ' ', ' ', ' ', ' '
    };
    static const uint8_t product[16] = {
        'S', 'W', 'D', ' ', 'D', 'i', 's', 'k',
        ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '
    };
    static const uint8_t revision[4] = {'0', '0', '0', '1'};
    (void)lun;
    for (size_t index = 0U; index < sizeof(vendor); ++index) {
        vendor_id[index] = vendor[index];
    }
    for (size_t index = 0U; index < sizeof(product); ++index) {
        product_id[index] = product[index];
    }
    for (size_t index = 0U; index < sizeof(revision); ++index) {
        product_revision[index] = revision[index];
    }
}

bool tud_msc_test_unit_ready_cb(uint8_t lun)
{
    return (lun == 0U) && uusb_msc_test_unit_ready(&profile.msc);
}

void tud_msc_capacity_cb(
    uint8_t lun, uint32_t *block_count, uint16_t *block_size)
{
    if ((lun == 0U) && profile.msc.present) {
        *block_count = profile.msc.block_count;
        *block_size = (uint16_t)UUSB_MSC_BLOCK_SIZE;
    } else {
        *block_count = 0U;
        *block_size = 0U;
    }
}

bool tud_msc_is_writable_cb(uint8_t lun)
{
    return (lun == 0U) && uusb_msc_is_writable(&profile.msc);
}

int32_t tud_msc_read10_cb(
    uint8_t lun, uint32_t lba, uint32_t offset,
    void *buffer, uint32_t buffer_size)
{
    if (lun != 0U) {
        return TUD_MSC_RET_ERROR;
    }
    return uusb_msc_begin_read(
        &profile.msc, lba, offset, buffer, buffer_size);
}

int32_t tud_msc_write10_cb(
    uint8_t lun, uint32_t lba, uint32_t offset,
    uint8_t *buffer, uint32_t buffer_size)
{
    if (lun != 0U) {
        return TUD_MSC_RET_ERROR;
    }
    return uusb_msc_begin_write(
        &profile.msc, lba, offset, buffer, buffer_size);
}

bool tud_msc_start_stop_cb(
    uint8_t lun, uint8_t power_condition, bool start, bool load_eject)
{
    (void)power_condition;
    if (lun != 0U) {
        return false;
    }
    if (load_eject) {
        return start ? profile.msc.present : uusb_msc_eject(&profile.msc);
    }
    return true;
}

bool tud_msc_prevent_allow_medium_removal_cb(
    uint8_t lun, uint8_t prohibit_removal, uint8_t control)
{
    if ((lun != 0U) || (control != 0U) || (prohibit_removal > 1U)) {
        return false;
    }
    return uusb_msc_set_prevent_removal(
        &profile.msc, prohibit_removal != 0U);
}

int32_t tud_msc_scsi_cb(
    uint8_t lun, const uint8_t scsi_command[16],
    void *buffer, uint16_t buffer_size)
{
    (void)buffer;
    (void)buffer_size;
    if ((lun == 0U) &&
        (scsi_command[0] == UUSB_SCSI_SYNCHRONIZE_CACHE_10)) {
        return profile.msc.present ? 0 : TUD_MSC_RET_ERROR;
    }
    tud_msc_set_sense(
        lun, UUSB_SENSE_ILLEGAL_REQUEST, UUSB_ASC_INVALID_COMMAND, 0U);
    return TUD_MSC_RET_ERROR;
}

void tud_msc_bot_reset_cb(void)
{
    uusb_msc_bot_reset(&profile.msc);
}

uint8_t tud_msc_get_maxlun_cb(void)
{
    return 0U;
}

int main(void)
{
    return uusb_firmware_run_with_hooks(
        UUSB_PROFILE_HID_MSC, &hooks, &profile);
}
