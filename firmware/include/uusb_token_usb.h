#ifndef UUSB_TOKEN_USB_H
#define UUSB_TOKEN_USB_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UUSB_TOKEN_USB_BRIDGE_MESSAGE_SIZE UINT16_C(512)
#define UUSB_TOKEN_USB_BRIDGE_PAYLOAD_SIZE UINT16_C(511)
#define UUSB_TOKEN_USB_BRIDGE_TYPE_OFFSET 0U
#define UUSB_TOKEN_USB_BRIDGE_PAYLOAD_OFFSET 1U

#define UUSB_CTAPHID_REPORT_SIZE 64U
#define UUSB_CTAPHID_MAX_MESSAGE_SIZE UINT16_C(512)
#define UUSB_CTAPHID_INITIAL_DATA_SIZE 57U
#define UUSB_CTAPHID_CONTINUATION_DATA_SIZE 59U
#define UUSB_CTAPHID_REASSEMBLY_TIMEOUT_MS UINT32_C(750)
#define UUSB_TOKEN_USB_EXCHANGE_TIMEOUT_MS UINT32_C(30000)
#define UUSB_CTAPHID_BROADCAST_CID UINT32_C(0xffffffff)

#define UUSB_CTAPHID_CMD_PING UINT8_C(0x81)
#define UUSB_CTAPHID_CMD_INIT UINT8_C(0x86)
#define UUSB_CTAPHID_CMD_WINK UINT8_C(0x88)
#define UUSB_CTAPHID_CMD_CBOR UINT8_C(0x90)
#define UUSB_CTAPHID_CMD_CANCEL UINT8_C(0x91)
#define UUSB_CTAPHID_CMD_KEEPALIVE UINT8_C(0xbb)
#define UUSB_CTAPHID_CMD_ERROR UINT8_C(0xbf)

#define UUSB_CTAPHID_ERROR_INVALID_COMMAND UINT8_C(0x01)
#define UUSB_CTAPHID_ERROR_INVALID_PARAMETER UINT8_C(0x02)
#define UUSB_CTAPHID_ERROR_INVALID_LENGTH UINT8_C(0x03)
#define UUSB_CTAPHID_ERROR_INVALID_SEQUENCE UINT8_C(0x04)
#define UUSB_CTAPHID_ERROR_MESSAGE_TIMEOUT UINT8_C(0x05)
#define UUSB_CTAPHID_ERROR_CHANNEL_BUSY UINT8_C(0x06)
#define UUSB_CTAPHID_ERROR_INVALID_CHANNEL UINT8_C(0x0b)
#define UUSB_CTAPHID_ERROR_OTHER UINT8_C(0x7f)
#define UUSB_CTAPHID_KEEPALIVE_PROCESSING UINT8_C(0x01)
#define UUSB_CTAPHID_KEEPALIVE_USER_PRESENCE UINT8_C(0x02)
#define UUSB_CTAP2_ERROR_KEEPALIVE_CANCEL UINT8_C(0x2d)

#define UUSB_CCID_HEADER_SIZE 10U
#define UUSB_CCID_MAX_MESSAGE_SIZE UINT16_C(512)
#define UUSB_CCID_MAX_PAYLOAD_SIZE \
    (UUSB_CCID_MAX_MESSAGE_SIZE - UUSB_CCID_HEADER_SIZE)
#define UUSB_CCID_REASSEMBLY_TIMEOUT_MS UINT32_C(1000)
#define UUSB_CCID_SLOT 0U

#define UUSB_CCID_PC_TO_RDR_SET_PARAMETERS UINT8_C(0x61)
#define UUSB_CCID_PC_TO_RDR_ICC_POWER_ON UINT8_C(0x62)
#define UUSB_CCID_PC_TO_RDR_ICC_POWER_OFF UINT8_C(0x63)
#define UUSB_CCID_PC_TO_RDR_GET_SLOT_STATUS UINT8_C(0x65)
#define UUSB_CCID_PC_TO_RDR_GET_PARAMETERS UINT8_C(0x6c)
#define UUSB_CCID_PC_TO_RDR_RESET_PARAMETERS UINT8_C(0x6d)
#define UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK UINT8_C(0x6f)
#define UUSB_CCID_PC_TO_RDR_ABORT UINT8_C(0x72)
#define UUSB_CCID_RDR_TO_PC_DATA_BLOCK UINT8_C(0x80)
#define UUSB_CCID_RDR_TO_PC_SLOT_STATUS UINT8_C(0x81)
#define UUSB_CCID_RDR_TO_PC_PARAMETERS UINT8_C(0x82)

#define UUSB_CCID_STATUS_ICC_ACTIVE UINT8_C(0x00)
#define UUSB_CCID_STATUS_ICC_INACTIVE UINT8_C(0x01)
#define UUSB_CCID_STATUS_COMMAND_FAILED UINT8_C(0x40)
#define UUSB_CCID_STATUS_TIME_EXTENSION UINT8_C(0x80)
#define UUSB_CCID_ERROR_NONE UINT8_C(0x00)
#define UUSB_CCID_ERROR_BAD_LENGTH UINT8_C(0x01)
#define UUSB_CCID_ERROR_BAD_SLOT UINT8_C(0x05)
#define UUSB_CCID_ERROR_BAD_PARAMETERS UINT8_C(0x07)
#define UUSB_CCID_ERROR_ICC_MUTE UINT8_C(0xfe)
#define UUSB_CCID_ERROR_COMMAND_ABORTED UINT8_C(0xff)
#define UUSB_CCID_ERROR_SLOT_BUSY UINT8_C(0xe0)
#define UUSB_CCID_PROTOCOL_T1 UINT8_C(0x01)
#define UUSB_CCID_T1_PARAMETER_SIZE 7U

#define UUSB_TOKEN_USB_OTP_REPORT_SIZE 8U

typedef enum {
    UUSB_TOKEN_USB_TRANSPORT_CTAPHID = 1,
    UUSB_TOKEN_USB_TRANSPORT_CCID = 2
} uusb_token_usb_transport_t;

typedef enum {
    UUSB_TOKEN_USB_EXCHANGE_PENDING = 0,
    UUSB_TOKEN_USB_EXCHANGE_COMPLETE = 1,
    UUSB_TOKEN_USB_EXCHANGE_FAILED = 2
} uusb_token_usb_exchange_result_t;

typedef enum {
    UUSB_TOKEN_USB_EXCHANGE_NONE = 0,
    UUSB_TOKEN_USB_EXCHANGE_CTAPHID = 1,
    UUSB_TOKEN_USB_EXCHANGE_CCID = 2
} uusb_token_usb_exchange_owner_t;

typedef enum {
    UUSB_TOKEN_USB_OTP_IDLE = 0,
    UUSB_TOKEN_USB_OTP_QUEUED_REPORT = 1,
    UUSB_TOKEN_USB_OTP_RELEASE = 2,
    UUSB_TOKEN_USB_OTP_FORCED_RELEASE = 3
} uusb_token_usb_otp_phase_t;

typedef bool (*uusb_token_usb_exchange_start_t)(
    void *context,
    uusb_token_usb_transport_t transport,
    const uint8_t *request,
    uint16_t request_length);

typedef uusb_token_usb_exchange_result_t (*uusb_token_usb_exchange_poll_t)(
    void *context,
    uusb_token_usb_transport_t transport,
    uint8_t *response,
    uint16_t response_capacity,
    uint16_t *response_length);

typedef void (*uusb_token_usb_exchange_cancel_t)(
    void *context, uusb_token_usb_transport_t transport);

typedef struct {
    uusb_token_usb_exchange_start_t start;
    uusb_token_usb_exchange_poll_t poll;
    uusb_token_usb_exchange_cancel_t cancel;
} uusb_token_usb_hooks_t;

typedef struct {
    uusb_token_usb_hooks_t hooks;
    void *hook_context;

    uusb_token_usb_exchange_owner_t exchange_owner;
    uint32_t exchange_started_ms;
    uint32_t exchange_ctaphid_cid;
    uint8_t exchange_ctaphid_command;
    uint8_t exchange_ccid_sequence;

    bool ctaphid_rx_active;
    uint32_t ctaphid_rx_cid;
    uint32_t ctaphid_rx_updated_ms;
    uint16_t ctaphid_rx_length;
    uint16_t ctaphid_rx_received;
    uint8_t ctaphid_rx_command;
    uint8_t ctaphid_rx_next_sequence;
    uint8_t ctaphid_rx_data[UUSB_CTAPHID_MAX_MESSAGE_SIZE];

    bool ctaphid_tx_active;
    bool ctaphid_tx_report_ready;
    uint32_t ctaphid_tx_cid;
    uint16_t ctaphid_tx_length;
    uint16_t ctaphid_tx_offset;
    uint8_t ctaphid_tx_command;
    uint8_t ctaphid_tx_next_sequence;
    uint8_t ctaphid_tx_data[UUSB_CTAPHID_MAX_MESSAGE_SIZE];
    uint8_t ctaphid_tx_report[UUSB_CTAPHID_REPORT_SIZE];
    uint32_t next_ctaphid_cid;

    uint8_t ccid_rx_header[UUSB_CCID_HEADER_SIZE];
    uint16_t ccid_rx_header_received;
    uint16_t ccid_rx_payload_received;
    uint32_t ccid_rx_payload_length;
    uint32_t ccid_rx_updated_ms;
    uint8_t ccid_rx_payload[UUSB_CCID_MAX_PAYLOAD_SIZE];

    uint8_t ccid_tx_data[UUSB_CCID_MAX_MESSAGE_SIZE];
    uint16_t ccid_tx_length;
    uint16_t ccid_tx_offset;
    bool ccid_powered;
    uint8_t ccid_parameters[UUSB_CCID_T1_PARAMETER_SIZE];

    uint8_t bridge_data[UUSB_TOKEN_USB_BRIDGE_MESSAGE_SIZE];

    uint8_t otp_report[UUSB_TOKEN_USB_OTP_REPORT_SIZE];
    uusb_token_usb_otp_phase_t otp_phase;
} uusb_token_usb_t;

void uusb_token_usb_initialize(
    uusb_token_usb_t *state,
    const uusb_token_usb_hooks_t *hooks,
    void *hook_context);

bool uusb_token_usb_ctaphid_out(
    uusb_token_usb_t *state,
    const uint8_t report[UUSB_CTAPHID_REPORT_SIZE],
    uint32_t now_ms);
bool uusb_token_usb_ctaphid_in_peek(
    const uusb_token_usb_t *state,
    uint8_t report[UUSB_CTAPHID_REPORT_SIZE]);
bool uusb_token_usb_ctaphid_in_accepted(uusb_token_usb_t *state);
void uusb_token_usb_ctaphid_in_failed(uusb_token_usb_t *state);

bool uusb_token_usb_ccid_out(
    uusb_token_usb_t *state,
    const uint8_t *data,
    size_t length,
    uint32_t now_ms);
size_t uusb_token_usb_ccid_in_peek(
    const uusb_token_usb_t *state, uint8_t *data, size_t capacity);
bool uusb_token_usb_ccid_in_accepted(
    uusb_token_usb_t *state, size_t length);
void uusb_token_usb_ccid_in_failed(uusb_token_usb_t *state);

void uusb_token_usb_poll(uusb_token_usb_t *state, uint32_t now_ms);
bool uusb_token_usb_keepalive(
    uusb_token_usb_t *state, uint8_t status);
bool uusb_token_usb_time_extension(
    uusb_token_usb_t *state, uint8_t multiplier);
void uusb_token_usb_tick(uusb_token_usb_t *state, uint32_t now_ms);
void uusb_token_usb_fail_timeout(uusb_token_usb_t *state);
void uusb_token_usb_fail_cancel(uusb_token_usb_t *state);
void uusb_token_usb_fail_reset(uusb_token_usb_t *state);
void uusb_token_usb_fail_unmount(uusb_token_usb_t *state);

bool uusb_token_usb_otp_report_set(
    uusb_token_usb_t *state,
    const uint8_t report[UUSB_TOKEN_USB_OTP_REPORT_SIZE]);
bool uusb_token_usb_otp_in_peek(
    const uusb_token_usb_t *state,
    uint8_t report[UUSB_TOKEN_USB_OTP_REPORT_SIZE]);
bool uusb_token_usb_otp_in_accepted(uusb_token_usb_t *state);
void uusb_token_usb_otp_in_failed(uusb_token_usb_t *state);
void uusb_token_usb_otp_fail(uusb_token_usb_t *state);

#endif
