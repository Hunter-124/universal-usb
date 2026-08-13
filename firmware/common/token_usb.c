#include "uusb_token_usb.h"

#include <string.h>

#define UUSB_CTAPHID_PROTOCOL_VERSION UINT8_C(2)
#define UUSB_CTAPHID_DEVICE_VERSION_MAJOR UINT8_C(1)
#define UUSB_CTAPHID_DEVICE_VERSION_MINOR UINT8_C(0)
#define UUSB_CTAPHID_DEVICE_VERSION_BUILD UINT8_C(0)
#define UUSB_CTAPHID_CAPABILITY_WINK UINT8_C(0x01)
#define UUSB_CTAPHID_CAPABILITY_CBOR UINT8_C(0x04)

static const uint8_t ccid_atr[] = {
    UINT8_C(0x3b), UINT8_C(0x80), UINT8_C(0x01), UINT8_C(0x81)
};

static const uint8_t ccid_default_parameters[UUSB_CCID_T1_PARAMETER_SIZE] = {
    UINT8_C(0x11), UINT8_C(0x10), UINT8_C(0x00), UINT8_C(0x4d),
    UINT8_C(0x00), UINT8_C(0x20), UINT8_C(0x00)
};

static uint16_t minimum_u16(uint16_t left, uint16_t right)
{
    return left < right ? left : right;
}

static size_t minimum_size(size_t left, size_t right)
{
    return left < right ? left : right;
}

static uint32_t read_be32(const uint8_t *data)
{
    return ((uint32_t)data[0] << 24) |
           ((uint32_t)data[1] << 16) |
           ((uint32_t)data[2] << 8) |
           (uint32_t)data[3];
}

static void write_be32(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)(value >> 24);
    data[1] = (uint8_t)(value >> 16);
    data[2] = (uint8_t)(value >> 8);
    data[3] = (uint8_t)value;
}

static uint32_t read_le32(const uint8_t *data)
{
    return (uint32_t)data[0] |
           ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) |
           ((uint32_t)data[3] << 24);
}

static void write_le32(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)value;
    data[1] = (uint8_t)(value >> 8);
    data[2] = (uint8_t)(value >> 16);
    data[3] = (uint8_t)(value >> 24);
}

static bool elapsed(uint32_t now_ms, uint32_t then_ms, uint32_t timeout_ms)
{
    return (uint32_t)(now_ms - then_ms) >= timeout_ms;
}

static void ctaphid_rx_clear(uusb_token_usb_t *state)
{
    state->ctaphid_rx_active = false;
    state->ctaphid_rx_cid = 0U;
    state->ctaphid_rx_length = 0U;
    state->ctaphid_rx_received = 0U;
    state->ctaphid_rx_command = 0U;
    state->ctaphid_rx_next_sequence = 0U;
}

static void ccid_rx_clear(uusb_token_usb_t *state)
{
    state->ccid_rx_header_received = 0U;
    state->ccid_rx_payload_received = 0U;
    state->ccid_rx_payload_length = 0U;
}

static void ctaphid_prepare_report(uusb_token_usb_t *state)
{
    uint16_t amount;

    memset(state->ctaphid_tx_report, 0, sizeof(state->ctaphid_tx_report));
    write_be32(state->ctaphid_tx_report, state->ctaphid_tx_cid);

    if (state->ctaphid_tx_offset == 0U) {
        state->ctaphid_tx_report[4] = state->ctaphid_tx_command;
        state->ctaphid_tx_report[5] =
            (uint8_t)(state->ctaphid_tx_length >> 8);
        state->ctaphid_tx_report[6] = (uint8_t)state->ctaphid_tx_length;
        amount = minimum_u16(
            state->ctaphid_tx_length, UUSB_CTAPHID_INITIAL_DATA_SIZE);
        if (amount != 0U) {
            memcpy(&state->ctaphid_tx_report[7], state->ctaphid_tx_data, amount);
        }
    } else {
        state->ctaphid_tx_report[4] = state->ctaphid_tx_next_sequence;
        ++state->ctaphid_tx_next_sequence;
        amount = minimum_u16(
            (uint16_t)(state->ctaphid_tx_length - state->ctaphid_tx_offset),
            UUSB_CTAPHID_CONTINUATION_DATA_SIZE);
        memcpy(
            &state->ctaphid_tx_report[5],
            &state->ctaphid_tx_data[state->ctaphid_tx_offset],
            amount);
    }

    state->ctaphid_tx_offset = (uint16_t)(state->ctaphid_tx_offset + amount);
    state->ctaphid_tx_report_ready = true;
}

static bool ctaphid_queue(
    uusb_token_usb_t *state,
    uint32_t cid,
    uint8_t command,
    const uint8_t *data,
    uint16_t length)
{
    if (length > UUSB_CTAPHID_MAX_MESSAGE_SIZE ||
        state->ctaphid_tx_active || state->ctaphid_tx_report_ready) {
        return false;
    }

    if (length != 0U) {
        memcpy(state->ctaphid_tx_data, data, length);
    }
    state->ctaphid_tx_active = true;
    state->ctaphid_tx_cid = cid;
    state->ctaphid_tx_command = command;
    state->ctaphid_tx_length = length;
    state->ctaphid_tx_offset = 0U;
    state->ctaphid_tx_next_sequence = 0U;
    ctaphid_prepare_report(state);
    return true;
}

static bool ctaphid_error(
    uusb_token_usb_t *state, uint32_t cid, uint8_t error)
{
    return ctaphid_queue(state, cid, UUSB_CTAPHID_CMD_ERROR, &error, 1U);
}

static uint8_t ccid_icc_status(const uusb_token_usb_t *state)
{
    return state->ccid_powered ? UUSB_CCID_STATUS_ICC_ACTIVE
                               : UUSB_CCID_STATUS_ICC_INACTIVE;
}

static bool ccid_queue(
    uusb_token_usb_t *state,
    uint8_t message_type,
    uint8_t sequence,
    uint8_t status,
    uint8_t error,
    uint8_t parameter,
    const uint8_t *payload,
    uint16_t payload_length)
{
    if (payload_length > UUSB_CCID_MAX_PAYLOAD_SIZE ||
        state->ccid_tx_length != 0U) {
        return false;
    }

    memset(state->ccid_tx_data, 0, UUSB_CCID_HEADER_SIZE);
    state->ccid_tx_data[0] = message_type;
    write_le32(&state->ccid_tx_data[1], payload_length);
    state->ccid_tx_data[5] = UUSB_CCID_SLOT;
    state->ccid_tx_data[6] = sequence;
    state->ccid_tx_data[7] = status;
    state->ccid_tx_data[8] = error;
    state->ccid_tx_data[9] = parameter;
    if (payload_length != 0U) {
        memcpy(
            &state->ccid_tx_data[UUSB_CCID_HEADER_SIZE],
            payload,
            payload_length);
    }
    state->ccid_tx_length =
        (uint16_t)(UUSB_CCID_HEADER_SIZE + payload_length);
    state->ccid_tx_offset = 0U;
    return true;
}

static uint8_t ccid_response_type(uint8_t command)
{
    if (command == UUSB_CCID_PC_TO_RDR_ICC_POWER_ON ||
        command == UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK) {
        return UUSB_CCID_RDR_TO_PC_DATA_BLOCK;
    }
    if (command == UUSB_CCID_PC_TO_RDR_GET_PARAMETERS ||
        command == UUSB_CCID_PC_TO_RDR_SET_PARAMETERS ||
        command == UUSB_CCID_PC_TO_RDR_RESET_PARAMETERS) {
        return UUSB_CCID_RDR_TO_PC_PARAMETERS;
    }
    return UUSB_CCID_RDR_TO_PC_SLOT_STATUS;
}

static bool ccid_command_error(
    uusb_token_usb_t *state,
    uint8_t command,
    uint8_t sequence,
    uint8_t error)
{
    uint8_t response_type = ccid_response_type(command);
    uint8_t parameter = response_type == UUSB_CCID_RDR_TO_PC_PARAMETERS
                            ? UUSB_CCID_PROTOCOL_T1
                            : 0U;
    return ccid_queue(
        state,
        response_type,
        sequence,
        (uint8_t)(ccid_icc_status(state) | UUSB_CCID_STATUS_COMMAND_FAILED),
        error,
        parameter,
        NULL,
        0U);
}

static uusb_token_usb_transport_t exchange_transport(
    uusb_token_usb_exchange_owner_t owner)
{
    return owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID
               ? UUSB_TOKEN_USB_TRANSPORT_CTAPHID
               : UUSB_TOKEN_USB_TRANSPORT_CCID;
}

static void exchange_cancel(uusb_token_usb_t *state)
{
    uusb_token_usb_exchange_owner_t owner = state->exchange_owner;

    if (owner == UUSB_TOKEN_USB_EXCHANGE_NONE) {
        return;
    }
    state->exchange_owner = UUSB_TOKEN_USB_EXCHANGE_NONE;
    if (state->hooks.cancel != NULL) {
        state->hooks.cancel(state->hook_context, exchange_transport(owner));
    }
}

static bool exchange_start(
    uusb_token_usb_t *state,
    uusb_token_usb_exchange_owner_t owner,
    const uint8_t *payload,
    uint16_t payload_length,
    uint32_t cid,
    uint8_t command,
    uint8_t sequence,
    uint32_t now_ms)
{
    uusb_token_usb_transport_t transport;
    uint16_t bridge_length;

    if (state->exchange_owner != UUSB_TOKEN_USB_EXCHANGE_NONE ||
        state->hooks.start == NULL ||
        payload_length > UUSB_TOKEN_USB_BRIDGE_PAYLOAD_SIZE) {
        return false;
    }

    state->bridge_data[UUSB_TOKEN_USB_BRIDGE_TYPE_OFFSET] = command;
    if (payload_length != 0U) {
        memcpy(
            &state->bridge_data[UUSB_TOKEN_USB_BRIDGE_PAYLOAD_OFFSET],
            payload,
            payload_length);
    }
    bridge_length = (uint16_t)(payload_length + 1U);
    state->exchange_owner = owner;
    state->exchange_started_ms = now_ms;
    state->exchange_ctaphid_cid = cid;
    state->exchange_ctaphid_command = command;
    state->exchange_ccid_sequence = sequence;
    transport = exchange_transport(owner);

    if (!state->hooks.start(
            state->hook_context,
            transport,
            state->bridge_data,
            bridge_length)) {
        state->exchange_owner = UUSB_TOKEN_USB_EXCHANGE_NONE;
        return false;
    }
    return true;
}

static uint32_t allocate_ctaphid_cid(uusb_token_usb_t *state)
{
    uint32_t cid = state->next_ctaphid_cid;

    ++state->next_ctaphid_cid;
    if (state->next_ctaphid_cid == 0U ||
        state->next_ctaphid_cid == UUSB_CTAPHID_BROADCAST_CID) {
        state->next_ctaphid_cid = 1U;
    }
    return cid;
}

static bool ctaphid_dispatch(
    uusb_token_usb_t *state,
    uint32_t cid,
    uint8_t command,
    const uint8_t *payload,
    uint16_t length,
    uint32_t now_ms)
{
    uint8_t init_response[17];
    uint32_t assigned_cid;

    switch (command) {
    case UUSB_CTAPHID_CMD_INIT:
        if (length != 8U) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);
            return false;
        }
        if (state->exchange_owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID &&
            state->exchange_ctaphid_cid == cid) {
            exchange_cancel(state);
        }
        if ((state->ctaphid_tx_active || state->ctaphid_tx_report_ready) &&
            state->ctaphid_tx_cid == cid) {
            state->ctaphid_tx_active = false;
            state->ctaphid_tx_report_ready = false;
        }
        memcpy(init_response, payload, 8U);
        assigned_cid = cid == UUSB_CTAPHID_BROADCAST_CID
                           ? allocate_ctaphid_cid(state)
                           : cid;
        write_be32(&init_response[8], assigned_cid);
        init_response[12] = UUSB_CTAPHID_PROTOCOL_VERSION;
        init_response[13] = UUSB_CTAPHID_DEVICE_VERSION_MAJOR;
        init_response[14] = UUSB_CTAPHID_DEVICE_VERSION_MINOR;
        init_response[15] = UUSB_CTAPHID_DEVICE_VERSION_BUILD;
        init_response[16] = (uint8_t)(UUSB_CTAPHID_CAPABILITY_WINK |
                                     UUSB_CTAPHID_CAPABILITY_CBOR);
        return ctaphid_queue(
            state, cid, UUSB_CTAPHID_CMD_INIT, init_response, 17U);

    case UUSB_CTAPHID_CMD_PING:
        return ctaphid_queue(
            state, cid, UUSB_CTAPHID_CMD_PING, payload, length);

    case UUSB_CTAPHID_CMD_WINK:
        if (length != 0U) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);
            return false;
        }
        if (!exchange_start(
                state,
                UUSB_TOKEN_USB_EXCHANGE_CTAPHID,
                payload,
                length,
                cid,
                command,
                0U,
                now_ms)) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_CHANNEL_BUSY);
            return false;
        }
        return true;

    case UUSB_CTAPHID_CMD_CBOR:
        if (length == 0U || length > UUSB_TOKEN_USB_BRIDGE_PAYLOAD_SIZE) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);
            return false;
        }
        if (!exchange_start(
                state,
                UUSB_TOKEN_USB_EXCHANGE_CTAPHID,
                payload,
                length,
                cid,
                command,
                0U,
                now_ms)) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_CHANNEL_BUSY);
            return false;
        }
        return true;

    case UUSB_CTAPHID_CMD_CANCEL:
        if (length != 0U) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);
            return false;
        }
        if (state->exchange_owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID &&
            state->exchange_ctaphid_cid == cid) {
            uint8_t cancelled = UUSB_CTAP2_ERROR_KEEPALIVE_CANCEL;
            uint8_t cancelled_command = state->exchange_ctaphid_command;
            exchange_cancel(state);
            if (cancelled_command == UUSB_CTAPHID_CMD_CBOR) {
                return ctaphid_queue(
                    state, cid, cancelled_command, &cancelled, 1U);
            }
            return ctaphid_queue(state, cid, cancelled_command, NULL, 0U);
        }
        return true;

    default:
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_INVALID_COMMAND);
        return false;
    }
}

static bool ctaphid_complete_rx(uusb_token_usb_t *state, uint32_t now_ms)
{
    uint32_t cid = state->ctaphid_rx_cid;
    uint8_t command = state->ctaphid_rx_command;
    uint16_t length = state->ctaphid_rx_length;

    ctaphid_rx_clear(state);
    return ctaphid_dispatch(
        state, cid, command, state->ctaphid_rx_data, length, now_ms);
}

static bool ccid_valid_empty_command(
    uusb_token_usb_t *state, uint8_t command, uint8_t sequence)
{
    if (state->ccid_rx_payload_length == 0U) {
        return true;
    }
    (void)ccid_command_error(
        state, command, sequence, UUSB_CCID_ERROR_BAD_LENGTH);
    return false;
}

static bool ccid_dispatch(uusb_token_usb_t *state, uint32_t now_ms)
{
    uint8_t command = state->ccid_rx_header[0];
    uint8_t slot = state->ccid_rx_header[5];
    uint8_t sequence = state->ccid_rx_header[6];

    if (slot != UUSB_CCID_SLOT) {
        (void)ccid_command_error(
            state, command, sequence, UUSB_CCID_ERROR_BAD_SLOT);
        return false;
    }

    switch (command) {
    case UUSB_CCID_PC_TO_RDR_ICC_POWER_ON:
        if (!ccid_valid_empty_command(state, command, sequence)) {
            return false;
        }
        state->ccid_powered = true;
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_DATA_BLOCK,
            sequence,
            UUSB_CCID_STATUS_ICC_ACTIVE,
            UUSB_CCID_ERROR_NONE,
            0U,
            ccid_atr,
            (uint16_t)sizeof(ccid_atr));

    case UUSB_CCID_PC_TO_RDR_ICC_POWER_OFF:
        if (!ccid_valid_empty_command(state, command, sequence)) {
            return false;
        }
        if (state->exchange_owner == UUSB_TOKEN_USB_EXCHANGE_CCID) {
            exchange_cancel(state);
        }
        state->ccid_powered = false;
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_SLOT_STATUS,
            sequence,
            UUSB_CCID_STATUS_ICC_INACTIVE,
            UUSB_CCID_ERROR_NONE,
            1U,
            NULL,
            0U);

    case UUSB_CCID_PC_TO_RDR_GET_SLOT_STATUS:
        if (!ccid_valid_empty_command(state, command, sequence)) {
            return false;
        }
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_SLOT_STATUS,
            sequence,
            ccid_icc_status(state),
            UUSB_CCID_ERROR_NONE,
            state->ccid_powered ? 0U : 1U,
            NULL,
            0U);

    case UUSB_CCID_PC_TO_RDR_GET_PARAMETERS:
        if (!ccid_valid_empty_command(state, command, sequence)) {
            return false;
        }
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_PARAMETERS,
            sequence,
            ccid_icc_status(state),
            UUSB_CCID_ERROR_NONE,
            UUSB_CCID_PROTOCOL_T1,
            state->ccid_parameters,
            UUSB_CCID_T1_PARAMETER_SIZE);

    case UUSB_CCID_PC_TO_RDR_RESET_PARAMETERS:
        if (!ccid_valid_empty_command(state, command, sequence)) {
            return false;
        }
        memcpy(
            state->ccid_parameters,
            ccid_default_parameters,
            sizeof(state->ccid_parameters));
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_PARAMETERS,
            sequence,
            ccid_icc_status(state),
            UUSB_CCID_ERROR_NONE,
            UUSB_CCID_PROTOCOL_T1,
            state->ccid_parameters,
            UUSB_CCID_T1_PARAMETER_SIZE);

    case UUSB_CCID_PC_TO_RDR_SET_PARAMETERS:
        if (state->ccid_rx_payload_length != UUSB_CCID_T1_PARAMETER_SIZE) {
            (void)ccid_command_error(
                state, command, sequence, UUSB_CCID_ERROR_BAD_LENGTH);
            return false;
        }
        if (state->ccid_rx_header[7] != UUSB_CCID_PROTOCOL_T1 ||
            state->ccid_rx_payload[5] == 0U) {
            (void)ccid_command_error(
                state, command, sequence, UUSB_CCID_ERROR_BAD_PARAMETERS);
            return false;
        }
        memcpy(
            state->ccid_parameters,
            state->ccid_rx_payload,
            sizeof(state->ccid_parameters));
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_PARAMETERS,
            sequence,
            ccid_icc_status(state),
            UUSB_CCID_ERROR_NONE,
            UUSB_CCID_PROTOCOL_T1,
            state->ccid_parameters,
            UUSB_CCID_T1_PARAMETER_SIZE);

    case UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK:
        if (!state->ccid_powered) {
            (void)ccid_command_error(
                state, command, sequence, UUSB_CCID_ERROR_ICC_MUTE);
            return false;
        }
        if (state->ccid_rx_header[8] != 0U ||
            state->ccid_rx_header[9] != 0U) {
            (void)ccid_command_error(
                state, command, sequence, UUSB_CCID_ERROR_BAD_PARAMETERS);
            return false;
        }
        if (!exchange_start(
                state,
                UUSB_TOKEN_USB_EXCHANGE_CCID,
                state->ccid_rx_payload,
                (uint16_t)state->ccid_rx_payload_length,
                0U,
                command,
                sequence,
                now_ms)) {
            (void)ccid_command_error(
                state, command, sequence, UUSB_CCID_ERROR_SLOT_BUSY);
            return false;
        }
        return true;

    case UUSB_CCID_PC_TO_RDR_ABORT:
        if (!ccid_valid_empty_command(state, command, sequence)) {
            return false;
        }
        if (state->exchange_owner == UUSB_TOKEN_USB_EXCHANGE_CCID) {
            exchange_cancel(state);
        }
        return ccid_queue(
            state,
            UUSB_CCID_RDR_TO_PC_SLOT_STATUS,
            sequence,
            (uint8_t)(ccid_icc_status(state) |
                      UUSB_CCID_STATUS_COMMAND_FAILED),
            UUSB_CCID_ERROR_COMMAND_ABORTED,
            state->ccid_powered ? 0U : 1U,
            NULL,
            0U);

    default:
        (void)ccid_command_error(
            state, command, sequence, UUSB_CCID_ERROR_BAD_PARAMETERS);
        return false;
    }
}

void uusb_token_usb_initialize(
    uusb_token_usb_t *state,
    const uusb_token_usb_hooks_t *hooks,
    void *hook_context)
{
    memset(state, 0, sizeof(*state));
    if (hooks != NULL) {
        state->hooks = *hooks;
    }
    state->hook_context = hook_context;
    state->next_ctaphid_cid = 1U;
    memcpy(
        state->ccid_parameters,
        ccid_default_parameters,
        sizeof(state->ccid_parameters));
}

bool uusb_token_usb_ctaphid_out(
    uusb_token_usb_t *state,
    const uint8_t report[UUSB_CTAPHID_REPORT_SIZE],
    uint32_t now_ms)
{
    uint32_t cid = read_be32(report);
    uint8_t frame_type = report[4];
    uint16_t amount;

    if (cid == 0U ||
        (cid == UUSB_CTAPHID_BROADCAST_CID &&
         frame_type != UUSB_CTAPHID_CMD_INIT)) {
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_INVALID_CHANNEL);
        return false;
    }

    if ((frame_type & UINT8_C(0x80)) != 0U) {
        uint16_t length =
            (uint16_t)(((uint16_t)report[5] << 8) | report[6]);

        if (state->ctaphid_rx_active) {
            if (state->ctaphid_rx_cid == cid &&
                (frame_type == UUSB_CTAPHID_CMD_INIT ||
                 frame_type == UUSB_CTAPHID_CMD_CANCEL)) {
                ctaphid_rx_clear(state);
            } else {
                (void)ctaphid_error(
                    state, cid, UUSB_CTAPHID_ERROR_CHANNEL_BUSY);
                return false;
            }
        }
        if (length > UUSB_CTAPHID_MAX_MESSAGE_SIZE) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);
            return false;
        }
        if ((frame_type == UUSB_CTAPHID_CMD_CBOR ||
             frame_type == UUSB_CTAPHID_CMD_WINK) &&
            state->exchange_owner != UUSB_TOKEN_USB_EXCHANGE_NONE) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_CHANNEL_BUSY);
            return false;
        }

        state->ctaphid_rx_active = true;
        state->ctaphid_rx_cid = cid;
        state->ctaphid_rx_command = frame_type;
        state->ctaphid_rx_length = length;
        state->ctaphid_rx_updated_ms = now_ms;
        state->ctaphid_rx_next_sequence = 0U;
        amount = minimum_u16(length, UUSB_CTAPHID_INITIAL_DATA_SIZE);
        if (amount != 0U) {
            memcpy(state->ctaphid_rx_data, &report[7], amount);
        }
        state->ctaphid_rx_received = amount;
        if (amount == length) {
            return ctaphid_complete_rx(state, now_ms);
        }
        return true;
    }

    if (!state->ctaphid_rx_active) {
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_INVALID_SEQUENCE);
        return false;
    }
    if (cid != state->ctaphid_rx_cid) {
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_INVALID_CHANNEL);
        return false;
    }
    if (frame_type != state->ctaphid_rx_next_sequence) {
        ctaphid_rx_clear(state);
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_INVALID_SEQUENCE);
        return false;
    }

    amount = minimum_u16(
        (uint16_t)(state->ctaphid_rx_length - state->ctaphid_rx_received),
        UUSB_CTAPHID_CONTINUATION_DATA_SIZE);
    memcpy(
        &state->ctaphid_rx_data[state->ctaphid_rx_received],
        &report[5],
        amount);
    state->ctaphid_rx_received =
        (uint16_t)(state->ctaphid_rx_received + amount);
    ++state->ctaphid_rx_next_sequence;
    state->ctaphid_rx_updated_ms = now_ms;
    if (state->ctaphid_rx_received == state->ctaphid_rx_length) {
        return ctaphid_complete_rx(state, now_ms);
    }
    return true;
}

bool uusb_token_usb_ctaphid_in_peek(
    const uusb_token_usb_t *state,
    uint8_t report[UUSB_CTAPHID_REPORT_SIZE])
{
    if (!state->ctaphid_tx_report_ready) {
        return false;
    }
    memcpy(report, state->ctaphid_tx_report, UUSB_CTAPHID_REPORT_SIZE);
    return true;
}

bool uusb_token_usb_ctaphid_in_accepted(uusb_token_usb_t *state)
{
    if (!state->ctaphid_tx_report_ready) {
        return false;
    }
    state->ctaphid_tx_report_ready = false;
    if (state->ctaphid_tx_offset < state->ctaphid_tx_length) {
        ctaphid_prepare_report(state);
    } else {
        state->ctaphid_tx_active = false;
    }
    return true;
}

void uusb_token_usb_ctaphid_in_failed(uusb_token_usb_t *state)
{
    (void)state;
}

bool uusb_token_usb_ccid_out(
    uusb_token_usb_t *state,
    const uint8_t *data,
    size_t length,
    uint32_t now_ms)
{
    while (length != 0U) {
        if (state->ccid_rx_header_received < UUSB_CCID_HEADER_SIZE) {
            size_t needed =
                UUSB_CCID_HEADER_SIZE - state->ccid_rx_header_received;
            size_t amount = minimum_size(needed, length);
            memcpy(
                &state->ccid_rx_header[state->ccid_rx_header_received],
                data,
                amount);
            state->ccid_rx_header_received =
                (uint16_t)(state->ccid_rx_header_received + amount);
            state->ccid_rx_updated_ms = now_ms;
            data += amount;
            length -= amount;
            if (state->ccid_rx_header_received < UUSB_CCID_HEADER_SIZE) {
                continue;
            }

            state->ccid_rx_payload_length =
                read_le32(&state->ccid_rx_header[1]);
            if (state->ccid_rx_payload_length >
                UUSB_CCID_MAX_PAYLOAD_SIZE) {
                (void)ccid_command_error(
                    state,
                    state->ccid_rx_header[0],
                    state->ccid_rx_header[6],
                    UUSB_CCID_ERROR_BAD_LENGTH);
                ccid_rx_clear(state);
                return false;
            }
            if (state->ccid_rx_payload_length == 0U) {
                bool accepted = ccid_dispatch(state, now_ms);
                ccid_rx_clear(state);
                if (!accepted) {
                    return false;
                }
                continue;
            }
        }

        {
            size_t needed = state->ccid_rx_payload_length -
                            state->ccid_rx_payload_received;
            size_t amount = minimum_size(needed, length);
            memcpy(
                &state->ccid_rx_payload[state->ccid_rx_payload_received],
                data,
                amount);
            state->ccid_rx_payload_received =
                (uint16_t)(state->ccid_rx_payload_received + amount);
            state->ccid_rx_updated_ms = now_ms;
            data += amount;
            length -= amount;
            if (state->ccid_rx_payload_received ==
                state->ccid_rx_payload_length) {
                bool accepted = ccid_dispatch(state, now_ms);
                ccid_rx_clear(state);
                if (!accepted) {
                    return false;
                }
            }
        }
    }
    return true;
}

size_t uusb_token_usb_ccid_in_peek(
    const uusb_token_usb_t *state, uint8_t *data, size_t capacity)
{
    size_t remaining;
    size_t amount;

    if (state->ccid_tx_length == 0U || capacity == 0U) {
        return 0U;
    }
    remaining = state->ccid_tx_length - state->ccid_tx_offset;
    amount = minimum_size(remaining, capacity);
    memcpy(data, &state->ccid_tx_data[state->ccid_tx_offset], amount);
    return amount;
}

bool uusb_token_usb_ccid_in_accepted(
    uusb_token_usb_t *state, size_t length)
{
    size_t remaining;

    if (state->ccid_tx_length == 0U || length == 0U) {
        return false;
    }
    remaining = state->ccid_tx_length - state->ccid_tx_offset;
    if (length > remaining) {
        return false;
    }
    state->ccid_tx_offset = (uint16_t)(state->ccid_tx_offset + length);
    if (state->ccid_tx_offset == state->ccid_tx_length) {
        state->ccid_tx_offset = 0U;
        state->ccid_tx_length = 0U;
    }
    return true;
}

void uusb_token_usb_ccid_in_failed(uusb_token_usb_t *state)
{
    (void)state;
}

void uusb_token_usb_poll(uusb_token_usb_t *state, uint32_t now_ms)
{
    uusb_token_usb_exchange_owner_t owner = state->exchange_owner;
    uusb_token_usb_exchange_result_t result;
    uusb_token_usb_transport_t transport;
    uint16_t response_length = 0U;
    uint32_t cid;
    uint8_t command;
    uint8_t sequence;

    (void)now_ms;
    if (owner == UUSB_TOKEN_USB_EXCHANGE_NONE || state->hooks.poll == NULL) {
        return;
    }
    if ((owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID &&
         (state->ctaphid_tx_active || state->ctaphid_tx_report_ready)) ||
        (owner == UUSB_TOKEN_USB_EXCHANGE_CCID &&
         state->ccid_tx_length != 0U)) {
        return;
    }

    transport = exchange_transport(owner);
    result = state->hooks.poll(
        state->hook_context,
        transport,
        state->bridge_data,
        UUSB_TOKEN_USB_BRIDGE_MESSAGE_SIZE,
        &response_length);
    if (result == UUSB_TOKEN_USB_EXCHANGE_PENDING) {
        return;
    }

    cid = state->exchange_ctaphid_cid;
    command = state->exchange_ctaphid_command;
    sequence = state->exchange_ccid_sequence;
    state->exchange_owner = UUSB_TOKEN_USB_EXCHANGE_NONE;

    if (result == UUSB_TOKEN_USB_EXCHANGE_FAILED) {
        if (owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID) {
            (void)ctaphid_error(state, cid, UUSB_CTAPHID_ERROR_OTHER);
        } else {
            (void)ccid_command_error(
                state,
                UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK,
                sequence,
                UUSB_CCID_ERROR_ICC_MUTE);
        }
        return;
    }

    if (response_length == 0U ||
        response_length > UUSB_TOKEN_USB_BRIDGE_MESSAGE_SIZE) {
        if (owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);
        } else {
            (void)ccid_command_error(
                state,
                UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK,
                sequence,
                UUSB_CCID_ERROR_BAD_LENGTH);
        }
        return;
    }

    if (owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID) {
        if (state->bridge_data[0] != command) {
            (void)ctaphid_error(
                state, cid, UUSB_CTAPHID_ERROR_INVALID_COMMAND);
            return;
        }
        (void)ctaphid_queue(
            state,
            cid,
            command,
            &state->bridge_data[1],
            (uint16_t)(response_length - 1U));
        return;
    }

    if (state->bridge_data[0] != UUSB_CCID_RDR_TO_PC_DATA_BLOCK ||
        response_length - 1U > UUSB_CCID_MAX_PAYLOAD_SIZE) {
        (void)ccid_command_error(
            state,
            UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK,
            sequence,
            UUSB_CCID_ERROR_BAD_LENGTH);
        return;
    }
    (void)ccid_queue(
        state,
        UUSB_CCID_RDR_TO_PC_DATA_BLOCK,
        sequence,
        ccid_icc_status(state),
        UUSB_CCID_ERROR_NONE,
        0U,
        &state->bridge_data[1],
        (uint16_t)(response_length - 1U));
}

bool uusb_token_usb_keepalive(
    uusb_token_usb_t *state, uint8_t status)
{
    if (state->exchange_owner != UUSB_TOKEN_USB_EXCHANGE_CTAPHID ||
        (status != UUSB_CTAPHID_KEEPALIVE_PROCESSING &&
         status != UUSB_CTAPHID_KEEPALIVE_USER_PRESENCE)) {
        return false;
    }
    return ctaphid_queue(
        state,
        state->exchange_ctaphid_cid,
        UUSB_CTAPHID_CMD_KEEPALIVE,
        &status,
        1U);
}

bool uusb_token_usb_time_extension(
    uusb_token_usb_t *state, uint8_t multiplier)
{
    if (state->exchange_owner != UUSB_TOKEN_USB_EXCHANGE_CCID ||
        multiplier == 0U) {
        return false;
    }
    return ccid_queue(
        state,
        UUSB_CCID_RDR_TO_PC_DATA_BLOCK,
        state->exchange_ccid_sequence,
        (uint8_t)(ccid_icc_status(state) |
                  UUSB_CCID_STATUS_TIME_EXTENSION),
        multiplier,
        0U,
        NULL,
        0U);
}

void uusb_token_usb_fail_timeout(uusb_token_usb_t *state)
{
    uusb_token_usb_exchange_owner_t owner = state->exchange_owner;
    uint32_t cid = state->exchange_ctaphid_cid;
    uint8_t sequence = state->exchange_ccid_sequence;

    if (owner == UUSB_TOKEN_USB_EXCHANGE_NONE) {
        return;
    }
    exchange_cancel(state);
    if (owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID) {
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_MESSAGE_TIMEOUT);
    } else {
        (void)ccid_command_error(
            state,
            UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK,
            sequence,
            UUSB_CCID_ERROR_ICC_MUTE);
    }
}

void uusb_token_usb_fail_cancel(uusb_token_usb_t *state)
{
    uusb_token_usb_exchange_owner_t owner = state->exchange_owner;
    uint32_t cid = state->exchange_ctaphid_cid;
    uint8_t command = state->exchange_ctaphid_command;
    uint8_t sequence = state->exchange_ccid_sequence;

    if (owner != UUSB_TOKEN_USB_EXCHANGE_NONE) {
        exchange_cancel(state);
        if (owner == UUSB_TOKEN_USB_EXCHANGE_CTAPHID) {
            if (command == UUSB_CTAPHID_CMD_CBOR) {
                uint8_t cancelled = UUSB_CTAP2_ERROR_KEEPALIVE_CANCEL;
                (void)ctaphid_queue(
                    state, cid, command, &cancelled, 1U);
            } else {
                (void)ctaphid_queue(state, cid, command, NULL, 0U);
            }
        } else {
            (void)ccid_command_error(
                state,
                UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK,
                sequence,
                UUSB_CCID_ERROR_COMMAND_ABORTED);
        }
    }
    uusb_token_usb_otp_fail(state);
}

void uusb_token_usb_tick(uusb_token_usb_t *state, uint32_t now_ms)
{
    if (state->ctaphid_rx_active &&
        elapsed(
            now_ms,
            state->ctaphid_rx_updated_ms,
            UUSB_CTAPHID_REASSEMBLY_TIMEOUT_MS)) {
        uint32_t cid = state->ctaphid_rx_cid;
        ctaphid_rx_clear(state);
        (void)ctaphid_error(
            state, cid, UUSB_CTAPHID_ERROR_MESSAGE_TIMEOUT);
    }

    if (state->ccid_rx_header_received != 0U &&
        elapsed(
            now_ms,
            state->ccid_rx_updated_ms,
            UUSB_CCID_REASSEMBLY_TIMEOUT_MS)) {
        if (state->ccid_rx_header_received >= 7U) {
            (void)ccid_command_error(
                state,
                state->ccid_rx_header[0],
                state->ccid_rx_header[6],
                UUSB_CCID_ERROR_ICC_MUTE);
        }
        ccid_rx_clear(state);
    }

    if (state->exchange_owner != UUSB_TOKEN_USB_EXCHANGE_NONE &&
        elapsed(
            now_ms,
            state->exchange_started_ms,
            UUSB_TOKEN_USB_EXCHANGE_TIMEOUT_MS)) {
        uusb_token_usb_fail_timeout(state);
    }
}

static void fail_disconnected(uusb_token_usb_t *state)
{
    exchange_cancel(state);
    ctaphid_rx_clear(state);
    state->ctaphid_tx_active = false;
    state->ctaphid_tx_report_ready = false;
    ccid_rx_clear(state);
    state->ccid_tx_length = 0U;
    state->ccid_tx_offset = 0U;
    state->ccid_powered = false;
    memcpy(
        state->ccid_parameters,
        ccid_default_parameters,
        sizeof(state->ccid_parameters));
    uusb_token_usb_otp_fail(state);
}

void uusb_token_usb_fail_reset(uusb_token_usb_t *state)
{
    fail_disconnected(state);
}

void uusb_token_usb_fail_unmount(uusb_token_usb_t *state)
{
    fail_disconnected(state);
}

bool uusb_token_usb_otp_report_set(
    uusb_token_usb_t *state,
    const uint8_t report[UUSB_TOKEN_USB_OTP_REPORT_SIZE])
{
    if (state->otp_phase != UUSB_TOKEN_USB_OTP_IDLE) {
        return false;
    }
    memcpy(state->otp_report, report, UUSB_TOKEN_USB_OTP_REPORT_SIZE);
    state->otp_phase = UUSB_TOKEN_USB_OTP_QUEUED_REPORT;
    return true;
}

bool uusb_token_usb_otp_in_peek(
    const uusb_token_usb_t *state,
    uint8_t report[UUSB_TOKEN_USB_OTP_REPORT_SIZE])
{
    if (state->otp_phase == UUSB_TOKEN_USB_OTP_IDLE) {
        return false;
    }
    if (state->otp_phase == UUSB_TOKEN_USB_OTP_QUEUED_REPORT) {
        memcpy(report, state->otp_report, UUSB_TOKEN_USB_OTP_REPORT_SIZE);
    } else {
        memset(report, 0, UUSB_TOKEN_USB_OTP_REPORT_SIZE);
    }
    return true;
}

bool uusb_token_usb_otp_in_accepted(uusb_token_usb_t *state)
{
    if (state->otp_phase == UUSB_TOKEN_USB_OTP_IDLE) {
        return false;
    }
    if (state->otp_phase == UUSB_TOKEN_USB_OTP_QUEUED_REPORT) {
        state->otp_phase = UUSB_TOKEN_USB_OTP_RELEASE;
    } else {
        state->otp_phase = UUSB_TOKEN_USB_OTP_IDLE;
        memset(state->otp_report, 0, sizeof(state->otp_report));
    }
    return true;
}

void uusb_token_usb_otp_fail(uusb_token_usb_t *state)
{
    memset(state->otp_report, 0, sizeof(state->otp_report));
    state->otp_phase = UUSB_TOKEN_USB_OTP_FORCED_RELEASE;
}

void uusb_token_usb_otp_in_failed(uusb_token_usb_t *state)
{
    uusb_token_usb_otp_fail(state);
}
