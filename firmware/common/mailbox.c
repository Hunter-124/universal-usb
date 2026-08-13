#include "uusb_mailbox.h"

#include <stddef.h>
#include <stdint.h>

#include "uusb_crc32c.h"

#define UUSB_HEARTBEAT_SAMPLE_MS UINT32_C(250)
#define UUSB_HEARTBEAT_LOSS_MS UINT32_C(1000)
#define UUSB_MOUSE_BUTTON_MASK UINT8_C(0x07)
#define UUSB_COMPILER_BARRIER() __asm__ volatile("" ::: "memory")

volatile uusb_mailbox_t uusb_mailbox
    __attribute__((section(".mailbox"), used, aligned(4)));

typedef struct {
    uusb_mailbox_hooks_t hooks;
    void *hook_context;
    uusb_profile_t profile;
    volatile uint32_t uptime_ms;
    uint32_t heartbeat_value;
    uint32_t heartbeat_sample_ms;
    uint32_t heartbeat_advanced_ms;
    bool heartbeat_lost;
} uusb_mailbox_runtime_t;

static uusb_mailbox_runtime_t runtime;

static void zero_bytes(volatile uint8_t *bytes, size_t length)
{
    for (size_t index = 0U; index < length; ++index) {
        bytes[index] = 0U;
    }
}

static void copy_from_volatile(uint8_t *destination,
                               const volatile uint8_t *source,
                               size_t length)
{
    for (size_t index = 0U; index < length; ++index) {
        destination[index] = source[index];
    }
}

static void copy_to_volatile(volatile uint8_t *destination,
                             const uint8_t *source,
                             size_t length)
{
    for (size_t index = 0U; index < length; ++index) {
        destination[index] = source[index];
    }
}

static void put_u16_le(uint8_t output[2], uint16_t value)
{
    output[0] = (uint8_t)value;
    output[1] = (uint8_t)(value >> 8);
}

static void put_u32_le(uint8_t output[4], uint32_t value)
{
    output[0] = (uint8_t)value;
    output[1] = (uint8_t)(value >> 8);
    output[2] = (uint8_t)(value >> 16);
    output[3] = (uint8_t)(value >> 24);
}

static uint16_t get_u16_le(const uint8_t input[2])
{
    return (uint16_t)((uint16_t)input[0] | ((uint16_t)input[1] << 8));
}

static uint32_t get_u32_le(const uint8_t input[4])
{
    return (uint32_t)input[0] |
           ((uint32_t)input[1] << 8) |
           ((uint32_t)input[2] << 16) |
           ((uint32_t)input[3] << 24);
}

uint32_t uusb_mailbox_control_request_crc(
    uint16_t opcode, uint16_t length, uint32_t flags, const uint8_t *payload)
{
    uint8_t metadata[8];
    put_u16_le(&metadata[0], opcode);
    put_u16_le(&metadata[2], length);
    put_u32_le(&metadata[4], flags);
    uint32_t state = uusb_crc32c_begin();
    state = uusb_crc32c_update(state, metadata, sizeof(metadata));
    state = uusb_crc32c_update(state, payload, length);
    return uusb_crc32c_finish(state);
}

uint32_t uusb_mailbox_control_response_crc(
    uusb_control_status_t status, uint16_t length, const uint8_t *payload)
{
    uint8_t metadata[8];
    put_u32_le(&metadata[0], (uint32_t)status);
    put_u16_le(&metadata[4], length);
    put_u16_le(&metadata[6], 0U);
    uint32_t state = uusb_crc32c_begin();
    state = uusb_crc32c_update(state, metadata, sizeof(metadata));
    state = uusb_crc32c_update(state, payload, length);
    return uusb_crc32c_finish(state);
}

uint32_t uusb_mailbox_block_request_crc(
    uint32_t epoch, uusb_block_operation_t operation, uint32_t lba,
    uint32_t data_length, const uint8_t *data)
{
    uint8_t metadata[16];
    put_u32_le(&metadata[0], epoch);
    metadata[4] = (uint8_t)operation;
    metadata[5] = 0U;
    metadata[6] = 0U;
    metadata[7] = 0U;
    put_u32_le(&metadata[8], lba);
    put_u32_le(&metadata[12], data_length);
    uint32_t state = uusb_crc32c_begin();
    state = uusb_crc32c_update(state, metadata, sizeof(metadata));
    if (operation == UUSB_BLOCK_WRITE) {
        state = uusb_crc32c_update(state, data, data_length);
    }
    return uusb_crc32c_finish(state);
}

uint32_t uusb_mailbox_block_response_crc(
    uusb_control_status_t status, uusb_block_operation_t operation,
    uint32_t data_length, const uint8_t *data)
{
    uint8_t word[4];
    put_u32_le(word, (uint32_t)status);
    uint32_t state = uusb_crc32c_update(
        uusb_crc32c_begin(), word, sizeof(word));
    if ((status == UUSB_STATUS_OK) && (operation == UUSB_BLOCK_READ)) {
        put_u32_le(word, data_length);
        state = uusb_crc32c_update(state, word, sizeof(word));
        state = uusb_crc32c_update(state, data, data_length);
    }
    return uusb_crc32c_finish(state);
}

uint32_t uusb_mailbox_token_request_crc(
    uusb_token_transport_t transport, uint16_t length, uint32_t flags,
    const uint8_t *data)
{
    uint8_t metadata[8];
    put_u16_le(&metadata[0], (uint16_t)transport);
    put_u16_le(&metadata[2], length);
    put_u32_le(&metadata[4], flags);
    uint32_t state = uusb_crc32c_begin();
    state = uusb_crc32c_update(state, metadata, sizeof(metadata));
    state = uusb_crc32c_update(state, data, length);
    return uusb_crc32c_finish(state);
}

uint32_t uusb_mailbox_token_response_crc(
    uint32_t request_sequence, uint16_t length, const uint8_t *data)
{
    uint8_t metadata[8];
    put_u32_le(&metadata[0], request_sequence);
    put_u16_le(&metadata[4], length);
    put_u16_le(&metadata[6], 0U);
    uint32_t state = uusb_crc32c_begin();
    state = uusb_crc32c_update(state, metadata, sizeof(metadata));
    state = uusb_crc32c_update(state, data, length);
    return uusb_crc32c_finish(state);
}

static bool status_is_valid(uusb_control_status_t status)
{
    return (uint32_t)status <= (uint32_t)UUSB_STATUS_RESET_DURING_COMMAND;
}

static bool opcode_is_known(uint16_t opcode)
{
    switch ((uusb_control_opcode_t)opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RELEASE_ALL:
    case UUSB_OPCODE_HID_KEYBOARD:
    case UUSB_OPCODE_HID_MOUSE:
    case UUSB_OPCODE_HID_CONSUMER:
    case UUSB_OPCODE_MEDIA_ATTACH:
    case UUSB_OPCODE_MEDIA_DETACH:
    case UUSB_OPCODE_MIC_CONFIG:
    case UUSB_OPCODE_UVC_PATTERN:
    case UUSB_OPCODE_RESET:
        return true;
    default:
        return false;
    }
}

static bool opcode_matches_profile(uint16_t opcode)
{
    switch ((uusb_control_opcode_t)opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RELEASE_ALL:
    case UUSB_OPCODE_RESET:
        return true;
    case UUSB_OPCODE_HID_KEYBOARD:
        return (runtime.profile == UUSB_PROFILE_HID_MSC) ||
               (runtime.profile == UUSB_PROFILE_SECURITY_TOKEN);
    case UUSB_OPCODE_HID_MOUSE:
    case UUSB_OPCODE_HID_CONSUMER:
    case UUSB_OPCODE_MEDIA_ATTACH:
    case UUSB_OPCODE_MEDIA_DETACH:
        return runtime.profile == UUSB_PROFILE_HID_MSC;
    case UUSB_OPCODE_MIC_CONFIG:
        return runtime.profile == UUSB_PROFILE_MICROPHONE;
    case UUSB_OPCODE_UVC_PATTERN:
        return runtime.profile == UUSB_PROFILE_WEBCAM;
    default:
        return false;
    }
}

static bool consumer_usage_is_supported(uint16_t usage)
{
    switch (usage) {
    case UUSB_CONSUMER_VOLUME_UP:
    case UUSB_CONSUMER_VOLUME_DOWN:
    case UUSB_CONSUMER_MUTE:
    case UUSB_CONSUMER_PLAY_PAUSE:
    case UUSB_CONSUMER_NEXT:
    case UUSB_CONSUMER_PREVIOUS:
    case UUSB_CONSUMER_STOP:
        return true;
    default:
        return false;
    }
}

static uusb_control_status_t validate_payload(uint16_t opcode,
                                              const uint8_t *payload,
                                              uint16_t length)
{
    uint16_t expected = 0U;
    switch ((uusb_control_opcode_t)opcode) {
    case UUSB_OPCODE_GET_INFO:
    case UUSB_OPCODE_RELEASE_ALL:
    case UUSB_OPCODE_MEDIA_DETACH:
    case UUSB_OPCODE_RESET:
        expected = 0U;
        break;
    case UUSB_OPCODE_HID_KEYBOARD:
        expected = (uint16_t)sizeof(uusb_keyboard_payload_t);
        break;
    case UUSB_OPCODE_HID_MOUSE:
        expected = (uint16_t)sizeof(uusb_mouse_payload_t);
        break;
    case UUSB_OPCODE_HID_CONSUMER:
        expected = (uint16_t)sizeof(uusb_consumer_payload_t);
        break;
    case UUSB_OPCODE_MEDIA_ATTACH:
        expected = (uint16_t)sizeof(uusb_media_attach_payload_t);
        break;
    case UUSB_OPCODE_MIC_CONFIG:
        expected = (uint16_t)sizeof(uusb_mic_config_payload_t);
        break;
    case UUSB_OPCODE_UVC_PATTERN:
        expected = (uint16_t)sizeof(uusb_uvc_pattern_payload_t);
        break;
    default:
        return UUSB_STATUS_BAD_OPCODE;
    }
    if (length != expected) {
        return UUSB_STATUS_BAD_LENGTH;
    }

    switch ((uusb_control_opcode_t)opcode) {
    case UUSB_OPCODE_HID_KEYBOARD:
        if (payload[1] != 0U) {
            return UUSB_STATUS_BAD_ABI;
        }
        break;
    case UUSB_OPCODE_HID_MOUSE:
        if ((payload[0] & (uint8_t)~UUSB_MOUSE_BUTTON_MASK) != 0U) {
            return UUSB_STATUS_OUT_OF_RANGE;
        }
        break;
    case UUSB_OPCODE_HID_CONSUMER:
        if ((get_u16_le(payload) != 0U) &&
            !consumer_usage_is_supported(get_u16_le(payload))) {
            return UUSB_STATUS_OUT_OF_RANGE;
        }
        break;
    case UUSB_OPCODE_MEDIA_ATTACH:
        if ((payload[5] != 0U) || (payload[6] != 0U) ||
            (payload[7] != 0U)) {
            return UUSB_STATUS_BAD_ABI;
        }
        if ((get_u32_le(payload) == 0U) || (payload[4] > 1U)) {
            return UUSB_STATUS_OUT_OF_RANGE;
        }
        break;
    case UUSB_OPCODE_MIC_CONFIG: {
        uint16_t const frequency = get_u16_le(&payload[2]);
        uint16_t const amplitude = get_u16_le(&payload[4]);
        if (payload[1] != 0U) {
            return UUSB_STATUS_BAD_ABI;
        }
        if ((payload[0] > (uint8_t)UUSB_MIC_TONE) ||
            (frequency < UINT16_C(20)) ||
            (frequency > UINT16_C(20000)) ||
            (amplitude > UINT16_C(32767))) {
            return UUSB_STATUS_OUT_OF_RANGE;
        }
        break;
    }
    case UUSB_OPCODE_UVC_PATTERN:
        if (payload[0] > (uint8_t)UUSB_UVC_GRADIENT) {
            return UUSB_STATUS_OUT_OF_RANGE;
        }
        break;
    default:
        break;
    }
    return UUSB_STATUS_OK;
}

static void publish_control_response(uint32_t sequence,
                                     uusb_control_status_t status,
                                     const uint8_t *payload,
                                     uint16_t length)
{
    volatile uusb_control_slot_t *const slot = &uusb_mailbox.control;
    slot->response_status = (uint32_t)status;
    slot->response_length = length;
    slot->reserved = 0U;
    if (length != 0U) {
        copy_to_volatile(slot->response_payload, payload, length);
    }
    slot->response_crc =
        uusb_mailbox_control_response_crc(status, length, payload);
    uusb_mailbox.header.last_error = (uint32_t)status;
    uusb_mailbox.header.control_completed++;
    UUSB_COMPILER_BARRIER();
    slot->host_ack = sequence;
}

static void process_control(void)
{
    volatile uusb_control_slot_t *const slot = &uusb_mailbox.control;
    uint32_t const sequence = slot->host_seq;
    UUSB_COMPILER_BARRIER();
    if (sequence == slot->host_ack) {
        return;
    }

    uint16_t const opcode = slot->opcode;
    uint16_t const length = slot->request_length;
    uint32_t const flags = slot->request_flags;
    uint32_t const expected_crc = slot->request_crc;
    uint8_t request[UUSB_MAILBOX_CONTROL_PAYLOAD_SIZE];
    uint8_t response[UUSB_MAILBOX_CONTROL_PAYLOAD_SIZE];
    uint16_t response_length = 0U;
    uusb_control_status_t status = UUSB_STATUS_OK;

    if (length > UUSB_MAILBOX_CONTROL_PAYLOAD_SIZE) {
        status = UUSB_STATUS_BAD_LENGTH;
    } else {
        copy_from_volatile(request, slot->request_payload, length);
        UUSB_COMPILER_BARRIER();
        if (slot->host_seq != sequence) {
            return;
        }
        if (flags != 0U) {
            status = UUSB_STATUS_BAD_ABI;
        } else if (uusb_mailbox_control_request_crc(
                       opcode, length, flags, request) !=
                   expected_crc) {
            status = UUSB_STATUS_BAD_CRC;
        } else if (!opcode_is_known(opcode)) {
            status = UUSB_STATUS_BAD_OPCODE;
        } else if (!opcode_matches_profile(opcode)) {
            status = UUSB_STATUS_WRONG_PROFILE;
        } else {
            status = validate_payload(opcode, request, length);
        }
    }

    if ((status == UUSB_STATUS_OK) &&
        (runtime.hooks.validate_command != NULL)) {
        status = runtime.hooks.validate_command(runtime.hook_context,
                                                 (uusb_control_opcode_t)opcode,
                                                 request, length);
        if (!status_is_valid(status)) {
            status = UUSB_STATUS_IO_ERROR;
        }
    }

    if (status == UUSB_STATUS_OK) {
        if (runtime.hooks.execute_command == NULL) {
            status = (opcode == (uint16_t)UUSB_OPCODE_GET_INFO)
                         ? UUSB_STATUS_OK
                         : UUSB_STATUS_BAD_STATE;
        } else {
            status = runtime.hooks.execute_command(
                runtime.hook_context, (uusb_control_opcode_t)opcode,
                request, length, response, &response_length);
            if (!status_is_valid(status) ||
                (response_length > UUSB_MAILBOX_CONTROL_PAYLOAD_SIZE)) {
                status = UUSB_STATUS_IO_ERROR;
                response_length = 0U;
            } else if (status != UUSB_STATUS_OK) {
                response_length = 0U;
            }
        }
    }
    if ((status != UUSB_STATUS_OK) &&
        (runtime.hooks.release_hid != NULL)) {
        uusb_mailbox.header.usb_flags |=
            (uint32_t)UUSB_USB_FLAG_HID_RELEASE_PENDING;
        runtime.hooks.release_hid(runtime.hook_context);
    }
    publish_control_response(sequence, status, response, response_length);
}

static void heartbeat_loss(void)
{
    uint32_t const flags = uusb_mailbox.header.usb_flags;
    if ((flags & UUSB_USB_FLAG_MOUNTED) != 0U) {
        uusb_mailbox.header.usb_flags =
            flags | (uint32_t)UUSB_USB_FLAG_HID_RELEASE_PENDING;
        if (runtime.hooks.release_hid != NULL) {
            runtime.hooks.release_hid(runtime.hook_context);
        }
    }
    if (runtime.hooks.fail_pending_block != NULL) {
        runtime.hooks.fail_pending_block(runtime.hook_context,
                                         UUSB_STATUS_TIMEOUT);
    }
    if (runtime.hooks.fail_pending_token != NULL) {
        runtime.hooks.fail_pending_token(runtime.hook_context,
                                         UUSB_STATUS_TIMEOUT);
    }
    uusb_mailbox.header.media_epoch++;
    uusb_mailbox.header.usb_flags &=
        ~((uint32_t)UUSB_USB_FLAG_MEDIA_PRESENT |
          (uint32_t)UUSB_USB_FLAG_MEDIA_WRITABLE |
          (uint32_t)UUSB_USB_FLAG_BLOCK_PENDING);
    uusb_mailbox.header.last_error = (uint32_t)UUSB_STATUS_TIMEOUT;
}

static void observe_heartbeat(void)
{
    uint32_t const now = runtime.uptime_ms;
    if ((uint32_t)(now - runtime.heartbeat_sample_ms) <
        UUSB_HEARTBEAT_SAMPLE_MS) {
        return;
    }
    runtime.heartbeat_sample_ms = now;
    uint32_t const heartbeat = uusb_mailbox.header.host_heartbeat;
    if (heartbeat != runtime.heartbeat_value) {
        runtime.heartbeat_value = heartbeat;
        runtime.heartbeat_advanced_ms = now;
        runtime.heartbeat_lost = false;
    } else if (!runtime.heartbeat_lost &&
               ((uint32_t)(now - runtime.heartbeat_advanced_ms) >=
                UUSB_HEARTBEAT_LOSS_MS)) {
        runtime.heartbeat_lost = true;
        heartbeat_loss();
    }
}

void uusb_mailbox_boot(uusb_profile_t profile, uint32_t fw_version,
                       bool clock_valid, const uusb_mailbox_hooks_t *hooks,
                       void *hook_context)
{
    runtime = (uusb_mailbox_runtime_t){0};
    if (hooks != NULL) {
        runtime.hooks = *hooks;
    }
    runtime.hook_context = hook_context;
    runtime.profile = profile;
    if (runtime.hooks.invalidate_local_state != NULL) {
        runtime.hooks.invalidate_local_state(runtime.hook_context);
    }

    bool const retained =
        (uusb_mailbox.header.magic == UUSB_MAILBOX_MAGIC) &&
        (uusb_mailbox.header.abi_version == UUSB_MAILBOX_ABI_VERSION) &&
        (uusb_mailbox.header.layout_size == UUSB_MAILBOX_SIZE);
    if (!retained) {
        zero_bytes((volatile uint8_t *)&uusb_mailbox, sizeof(uusb_mailbox));
        uusb_mailbox.header.magic = UUSB_MAILBOX_MAGIC;
        uusb_mailbox.header.abi_version = UUSB_MAILBOX_ABI_VERSION;
        uusb_mailbox.header.layout_size = (uint16_t)UUSB_MAILBOX_SIZE;
        uusb_mailbox.header.boot_counter = 1U;
    } else {
        uusb_mailbox.header.boot_counter++;
    }

    uusb_mailbox.header.fw_version = fw_version;
    uusb_mailbox.header.profile = (uint32_t)profile;
    uusb_mailbox.header.uptime_ms = 0U;
    uusb_mailbox.header.usb_flags = clock_valid
                                        ? (uint32_t)UUSB_USB_FLAG_CLOCK_VALID
                                        : 0U;
    uusb_mailbox.header.media_epoch++;
    runtime.heartbeat_value = uusb_mailbox.header.host_heartbeat;

    bool const control_was_pending = retained &&
        (uusb_mailbox.control.host_seq != uusb_mailbox.control.host_ack);
    bool const token_was_pending = retained &&
        ((uusb_mailbox.token_request.request_seq !=
          uusb_mailbox.token_request.request_ack) ||
         (uusb_mailbox.token_response.request_seq !=
          uusb_mailbox.token_request.request_seq) ||
         (uusb_mailbox.token_response.response_seq !=
          uusb_mailbox.token_response.response_ack));

    if (control_was_pending) {
        uint32_t const sequence = uusb_mailbox.control.host_seq;
        publish_control_response(sequence, UUSB_STATUS_RESET_DURING_COMMAND,
                                 NULL, 0U);
    }
    if (token_was_pending) {
        /* Discard both retained publications.  The trigger/ACK words are
         * paired last so neither peer can mistake pre-reset bytes for a new
         * exchange.  A new request uses a different sequence. */
        UUSB_COMPILER_BARRIER();
        uusb_mailbox.token_request.request_ack =
            uusb_mailbox.token_request.request_seq;
        uusb_mailbox.token_response.response_ack =
            uusb_mailbox.token_response.response_seq;
    }
    if (control_was_pending || token_was_pending) {
        uusb_mailbox.header.last_error =
            (uint32_t)UUSB_STATUS_RESET_DURING_COMMAND;
    } else {
        uusb_mailbox.header.last_error = (uint32_t)UUSB_STATUS_OK;
    }
}

void uusb_mailbox_poll(void)
{
    process_control();
    observe_heartbeat();
}

void uusb_mailbox_tick_1ms(void)
{
    runtime.uptime_ms++;
    uusb_mailbox.header.uptime_ms = runtime.uptime_ms;
}

uint32_t uusb_mailbox_uptime_ms(void)
{
    return runtime.uptime_ms;
}

void uusb_mailbox_set_mounted(bool mounted)
{
    if (mounted) {
        uusb_mailbox.header.usb_flags |= (uint32_t)UUSB_USB_FLAG_MOUNTED;
    } else {
        uusb_mailbox.header.usb_flags &= ~(uint32_t)UUSB_USB_FLAG_MOUNTED;
    }
}

void uusb_mailbox_set_suspended(bool suspended)
{
    if (suspended) {
        uusb_mailbox.header.usb_flags |= (uint32_t)UUSB_USB_FLAG_SUSPENDED;
    } else {
        uusb_mailbox.header.usb_flags &= ~(uint32_t)UUSB_USB_FLAG_SUSPENDED;
    }
}

void uusb_mailbox_set_media(bool present, bool writable)
{
    uint32_t flags = uusb_mailbox.header.usb_flags;
    flags &= ~((uint32_t)UUSB_USB_FLAG_MEDIA_PRESENT |
               (uint32_t)UUSB_USB_FLAG_MEDIA_WRITABLE);
    if (present) {
        flags |= (uint32_t)UUSB_USB_FLAG_MEDIA_PRESENT;
        if (writable) {
            flags |= (uint32_t)UUSB_USB_FLAG_MEDIA_WRITABLE;
        }
    }
    uusb_mailbox.header.usb_flags = flags;
}

void uusb_mailbox_set_block_pending(bool pending)
{
    if (pending) {
        uusb_mailbox.header.usb_flags |= (uint32_t)UUSB_USB_FLAG_BLOCK_PENDING;
    } else {
        uusb_mailbox.header.usb_flags &=
            ~(uint32_t)UUSB_USB_FLAG_BLOCK_PENDING;
    }
}

void uusb_mailbox_hid_release_complete(void)
{
    uusb_mailbox.header.usb_flags &=
        ~(uint32_t)UUSB_USB_FLAG_HID_RELEASE_PENDING;
}

void uusb_mailbox_usb_unmounted(void)
{
    if (runtime.hooks.invalidate_local_state != NULL) {
        runtime.hooks.invalidate_local_state(runtime.hook_context);
    }
    if (runtime.hooks.fail_pending_block != NULL) {
        runtime.hooks.fail_pending_block(runtime.hook_context,
                                         UUSB_STATUS_IO_ERROR);
    }
    if (runtime.hooks.fail_pending_token != NULL) {
        runtime.hooks.fail_pending_token(runtime.hook_context,
                                         UUSB_STATUS_IO_ERROR);
    }
    uusb_mailbox.header.media_epoch++;
    uusb_mailbox.header.usb_flags &=
        ~((uint32_t)UUSB_USB_FLAG_MOUNTED |
          (uint32_t)UUSB_USB_FLAG_SUSPENDED |
          (uint32_t)UUSB_USB_FLAG_MEDIA_PRESENT |
          (uint32_t)UUSB_USB_FLAG_MEDIA_WRITABLE |
          (uint32_t)UUSB_USB_FLAG_HID_RELEASE_PENDING |
          (uint32_t)UUSB_USB_FLAG_BLOCK_PENDING);
}
