#ifndef UUSB_MAILBOX_H
#define UUSB_MAILBOX_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UUSB_MAILBOX_ADDRESS UINT32_C(0x20000000)
#define UUSB_MAILBOX_SIZE UINT32_C(4096)
#define UUSB_MAILBOX_MAGIC UINT32_C(0x31425355)
#define UUSB_MAILBOX_ABI_VERSION UINT16_C(1)
#define UUSB_MAILBOX_CONTROL_PAYLOAD_SIZE UINT16_C(112)
#define UUSB_MAILBOX_BLOCK_DATA_SIZE UINT32_C(512)
#define UUSB_MAILBOX_TOKEN_DATA_SIZE UINT16_C(512)

#define UUSB_FIRMWARE_VERSION(major, minor, patch)                         \
    ((((uint32_t)(major) & UINT32_C(0xffff)) << 16) |                     \
     (((uint32_t)(minor) & UINT32_C(0xff)) << 8) |                        \
     ((uint32_t)(patch) & UINT32_C(0xff)))

typedef enum {
    UUSB_PROFILE_HID_MSC = 1,
    UUSB_PROFILE_MICROPHONE = 2,
    UUSB_PROFILE_WEBCAM = 3,
    UUSB_PROFILE_SECURITY_TOKEN = 4
} uusb_profile_t;

typedef enum {
    UUSB_USB_FLAG_CLOCK_VALID = UINT32_C(1) << 0,
    UUSB_USB_FLAG_MOUNTED = UINT32_C(1) << 1,
    UUSB_USB_FLAG_SUSPENDED = UINT32_C(1) << 2,
    UUSB_USB_FLAG_MEDIA_PRESENT = UINT32_C(1) << 3,
    UUSB_USB_FLAG_MEDIA_WRITABLE = UINT32_C(1) << 4,
    UUSB_USB_FLAG_HID_RELEASE_PENDING = UINT32_C(1) << 5,
    UUSB_USB_FLAG_BLOCK_PENDING = UINT32_C(1) << 6
} uusb_usb_flag_t;

typedef enum {
    UUSB_OPCODE_GET_INFO = 0x0001,
    UUSB_OPCODE_RELEASE_ALL = 0x0002,
    UUSB_OPCODE_HID_KEYBOARD = 0x0100,
    UUSB_OPCODE_HID_MOUSE = 0x0101,
    UUSB_OPCODE_HID_CONSUMER = 0x0102,
    UUSB_OPCODE_MEDIA_ATTACH = 0x0200,
    UUSB_OPCODE_MEDIA_DETACH = 0x0201,
    UUSB_OPCODE_MIC_CONFIG = 0x0300,
    UUSB_OPCODE_UVC_PATTERN = 0x0400,
    UUSB_OPCODE_RESET = 0x7f00
} uusb_control_opcode_t;

typedef enum {
    UUSB_STATUS_OK = 0,
    UUSB_STATUS_BAD_ABI = 1,
    UUSB_STATUS_BAD_LENGTH = 2,
    UUSB_STATUS_BAD_CRC = 3,
    UUSB_STATUS_BAD_OPCODE = 4,
    UUSB_STATUS_WRONG_PROFILE = 5,
    UUSB_STATUS_BAD_STATE = 6,
    UUSB_STATUS_OUT_OF_RANGE = 7,
    UUSB_STATUS_BUSY = 8,
    UUSB_STATUS_TIMEOUT = 9,
    UUSB_STATUS_IO_ERROR = 10,
    UUSB_STATUS_RESET_DURING_COMMAND = 11
} uusb_control_status_t;

typedef enum {
    UUSB_BLOCK_READ = 1,
    UUSB_BLOCK_WRITE = 2,
    UUSB_BLOCK_FLUSH = 3
} uusb_block_operation_t;

typedef enum {
    UUSB_TOKEN_FIDO_HID = 1,
    UUSB_TOKEN_CCID = 2
} uusb_token_transport_t;

typedef enum {
    UUSB_MIC_SILENCE = 0,
    UUSB_MIC_TONE = 1
} uusb_mic_mode_t;

typedef enum {
    UUSB_UVC_BARS = 0,
    UUSB_UVC_CHECKER = 1,
    UUSB_UVC_GRADIENT = 2
} uusb_uvc_pattern_t;

#define UUSB_CONSUMER_PLAY_PAUSE UINT16_C(0x00cd)
#define UUSB_CONSUMER_STOP UINT16_C(0x00b7)
#define UUSB_CONSUMER_NEXT UINT16_C(0x00b5)
#define UUSB_CONSUMER_PREVIOUS UINT16_C(0x00b6)
#define UUSB_CONSUMER_MUTE UINT16_C(0x00e2)
#define UUSB_CONSUMER_VOLUME_UP UINT16_C(0x00e9)
#define UUSB_CONSUMER_VOLUME_DOWN UINT16_C(0x00ea)

typedef struct {
    uint8_t modifiers;
    uint8_t reserved;
    uint8_t keys[6];
} uusb_keyboard_payload_t;

typedef struct {
    uint8_t buttons;
    int8_t dx;
    int8_t dy;
    int8_t wheel;
} uusb_mouse_payload_t;

typedef struct {
    uint16_t usage;
} uusb_consumer_payload_t;

typedef struct {
    uint32_t block_count;
    uint8_t writable;
    uint8_t reserved[3];
} uusb_media_attach_payload_t;

typedef struct {
    uint8_t mode;
    uint8_t reserved;
    uint16_t frequency_hz;
    uint16_t amplitude_q15;
} uusb_mic_config_payload_t;

typedef struct {
    uint8_t pattern;
} uusb_uvc_pattern_payload_t;

typedef struct {
    uint32_t magic;
    uint16_t abi_version;
    uint16_t layout_size;
    uint32_t fw_version;
    uint32_t profile;
    uint32_t boot_counter;
    uint32_t uptime_ms;
    uint32_t usb_flags;
    uint32_t host_heartbeat;
    uint32_t last_error;
    uint32_t control_completed;
    uint32_t media_epoch;
    uint32_t block_completed;
    uint32_t reserved[4];
} uusb_mailbox_header_t;

typedef struct {
    uint32_t host_seq;
    uint16_t opcode;
    uint16_t request_length;
    uint32_t request_flags;
    uint32_t request_crc;
    uint8_t request_payload[112];
    uint32_t host_ack;
    uint32_t response_status;
    uint16_t response_length;
    uint16_t reserved;
    uint32_t response_crc;
    uint8_t response_payload[112];
} uusb_control_slot_t;

typedef struct {
    uint32_t request_seq;
    uint32_t epoch;
    uint8_t op;
    uint8_t reserved[3];
    uint32_t lba;
    uint32_t data_length;
    uint32_t request_crc;
    uint8_t data[512];
    uint32_t response_seq;
    uint32_t response_status;
    uint32_t response_crc;
} uusb_block_slot_t;

typedef struct {
    uint32_t request_seq;
    uint16_t transport;
    uint16_t length;
    uint32_t flags;
    uint32_t request_crc;
    uint8_t data[512];
    uint32_t request_ack;
} uusb_token_request_slot_t;

typedef struct {
    uint32_t response_seq;
    uint32_t request_seq;
    uint16_t length;
    uint16_t reserved;
    uint32_t response_crc;
    uint8_t data[512];
    uint32_t response_ack;
} uusb_token_response_slot_t;

typedef struct {
    uusb_mailbox_header_t header;
    uusb_control_slot_t control;
    uusb_block_slot_t block;
    uint8_t reserved_0364_03ff[0x09c];
    uusb_token_request_slot_t token_request;
    uint8_t reserved_0614_063f[0x02c];
    uusb_token_response_slot_t token_response;
    uint8_t reserved_0854_0fff[0x7ac];
} uusb_mailbox_t;

typedef struct {
    void (*invalidate_local_state)(void *context);
    uusb_control_status_t (*validate_command)(
        void *context,
        uusb_control_opcode_t opcode,
        const uint8_t *request,
        uint16_t request_length);
    uusb_control_status_t (*execute_command)(
        void *context,
        uusb_control_opcode_t opcode,
        const uint8_t *request,
        uint16_t request_length,
        uint8_t *response,
        uint16_t *response_length);
    void (*release_hid)(void *context);
    void (*fail_pending_block)(void *context, uusb_control_status_t status);
    void (*fail_pending_token)(void *context, uusb_control_status_t status);
} uusb_mailbox_hooks_t;

extern volatile uusb_mailbox_t uusb_mailbox;
uint32_t uusb_mailbox_control_request_crc(
    uint16_t opcode, uint16_t length, uint32_t flags, const uint8_t *payload);
uint32_t uusb_mailbox_control_response_crc(
    uusb_control_status_t status, uint16_t length, const uint8_t *payload);
uint32_t uusb_mailbox_block_request_crc(
    uint32_t epoch,
    uusb_block_operation_t operation,
    uint32_t lba,
    uint32_t data_length,
    const uint8_t *data);
uint32_t uusb_mailbox_block_response_crc(
    uusb_control_status_t status,
    uusb_block_operation_t operation,
    uint32_t data_length,
    const uint8_t *data);
uint32_t uusb_mailbox_token_request_crc(
    uusb_token_transport_t transport,
    uint16_t length,
    uint32_t flags,
    const uint8_t *data);
uint32_t uusb_mailbox_token_response_crc(
    uint32_t request_sequence,
    uint16_t length,
    const uint8_t *data);


void uusb_mailbox_boot(
    uusb_profile_t profile,
    uint32_t fw_version,
    bool clock_valid,
    const uusb_mailbox_hooks_t *hooks,
    void *hook_context);
void uusb_mailbox_poll(void);
void uusb_mailbox_tick_1ms(void);
uint32_t uusb_mailbox_uptime_ms(void);
void uusb_mailbox_set_mounted(bool mounted);
void uusb_mailbox_set_suspended(bool suspended);
void uusb_mailbox_set_media(bool present, bool writable);
void uusb_mailbox_set_block_pending(bool pending);
void uusb_mailbox_hid_release_complete(void);
void uusb_mailbox_usb_unmounted(void);

#define UUSB_ASSERT_SIZE(type, expected) \
    _Static_assert(sizeof(type) == (expected), #type " size changed")
#define UUSB_ASSERT_OFFSET(type, field, expected) \
    _Static_assert(offsetof(type, field) == (expected), #type "." #field " offset changed")

UUSB_ASSERT_SIZE(uusb_keyboard_payload_t, 8);
UUSB_ASSERT_OFFSET(uusb_keyboard_payload_t, modifiers, 0);
UUSB_ASSERT_OFFSET(uusb_keyboard_payload_t, reserved, 1);
UUSB_ASSERT_OFFSET(uusb_keyboard_payload_t, keys, 2);
UUSB_ASSERT_SIZE(uusb_mouse_payload_t, 4);
UUSB_ASSERT_OFFSET(uusb_mouse_payload_t, buttons, 0);
UUSB_ASSERT_OFFSET(uusb_mouse_payload_t, dx, 1);
UUSB_ASSERT_OFFSET(uusb_mouse_payload_t, dy, 2);
UUSB_ASSERT_OFFSET(uusb_mouse_payload_t, wheel, 3);
UUSB_ASSERT_SIZE(uusb_consumer_payload_t, 2);
UUSB_ASSERT_OFFSET(uusb_consumer_payload_t, usage, 0);
UUSB_ASSERT_SIZE(uusb_media_attach_payload_t, 8);
UUSB_ASSERT_OFFSET(uusb_media_attach_payload_t, block_count, 0);
UUSB_ASSERT_OFFSET(uusb_media_attach_payload_t, writable, 4);
UUSB_ASSERT_OFFSET(uusb_media_attach_payload_t, reserved, 5);
UUSB_ASSERT_SIZE(uusb_mic_config_payload_t, 6);
UUSB_ASSERT_OFFSET(uusb_mic_config_payload_t, mode, 0);
UUSB_ASSERT_OFFSET(uusb_mic_config_payload_t, reserved, 1);
UUSB_ASSERT_OFFSET(uusb_mic_config_payload_t, frequency_hz, 2);
UUSB_ASSERT_OFFSET(uusb_mic_config_payload_t, amplitude_q15, 4);
UUSB_ASSERT_SIZE(uusb_uvc_pattern_payload_t, 1);
UUSB_ASSERT_OFFSET(uusb_uvc_pattern_payload_t, pattern, 0);

UUSB_ASSERT_SIZE(uusb_mailbox_header_t, 0x40);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, magic, 0x00);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, abi_version, 0x04);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, layout_size, 0x06);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, fw_version, 0x08);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, profile, 0x0c);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, boot_counter, 0x10);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, uptime_ms, 0x14);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, usb_flags, 0x18);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, host_heartbeat, 0x1c);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, last_error, 0x20);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, control_completed, 0x24);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, media_epoch, 0x28);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, block_completed, 0x2c);
UUSB_ASSERT_OFFSET(uusb_mailbox_header_t, reserved, 0x30);

UUSB_ASSERT_SIZE(uusb_control_slot_t, 0x100);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, host_seq, 0x00);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, opcode, 0x04);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, request_length, 0x06);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, request_flags, 0x08);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, request_crc, 0x0c);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, request_payload, 0x10);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, host_ack, 0x80);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, response_status, 0x84);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, response_length, 0x88);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, reserved, 0x8a);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, response_crc, 0x8c);
UUSB_ASSERT_OFFSET(uusb_control_slot_t, response_payload, 0x90);

UUSB_ASSERT_SIZE(uusb_block_slot_t, 0x224);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, request_seq, 0x000);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, epoch, 0x004);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, op, 0x008);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, reserved, 0x009);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, lba, 0x00c);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, data_length, 0x010);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, request_crc, 0x014);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, data, 0x018);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, response_seq, 0x218);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, response_status, 0x21c);
UUSB_ASSERT_OFFSET(uusb_block_slot_t, response_crc, 0x220);

UUSB_ASSERT_SIZE(uusb_token_request_slot_t, 0x214);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, request_seq, 0x000);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, transport, 0x004);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, length, 0x006);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, flags, 0x008);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, request_crc, 0x00c);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, data, 0x010);
UUSB_ASSERT_OFFSET(uusb_token_request_slot_t, request_ack, 0x210);

UUSB_ASSERT_SIZE(uusb_token_response_slot_t, 0x214);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, response_seq, 0x000);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, request_seq, 0x004);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, length, 0x008);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, reserved, 0x00a);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, response_crc, 0x00c);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, data, 0x010);
UUSB_ASSERT_OFFSET(uusb_token_response_slot_t, response_ack, 0x210);

UUSB_ASSERT_SIZE(uusb_mailbox_t, 0x1000);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, header, 0x000);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, control, 0x040);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, block, 0x140);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, reserved_0364_03ff, 0x364);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, token_request, 0x400);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, reserved_0614_063f, 0x614);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, token_response, 0x640);
UUSB_ASSERT_OFFSET(uusb_mailbox_t, reserved_0854_0fff, 0x854);

#undef UUSB_ASSERT_OFFSET
#undef UUSB_ASSERT_SIZE

#endif
