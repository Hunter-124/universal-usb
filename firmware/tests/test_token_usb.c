#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "uusb_token_usb.h"

typedef struct {
    unsigned starts;
    unsigned cancels;
    uusb_token_usb_transport_t transport;
    uint8_t request[UUSB_TOKEN_USB_BRIDGE_MESSAGE_SIZE];
    uint16_t request_length;
    uusb_token_usb_exchange_result_t result;
    uint8_t response[UUSB_TOKEN_USB_BRIDGE_MESSAGE_SIZE];
    uint16_t response_length;
} context_t;

static bool start_exchange(
    void *opaque,
    uusb_token_usb_transport_t transport,
    const uint8_t *request,
    uint16_t length)
{
    context_t *context = opaque;
    assert(length <= sizeof(context->request));
    ++context->starts;
    context->transport = transport;
    context->request_length = length;
    memcpy(context->request, request, length);
    return true;
}

static uusb_token_usb_exchange_result_t poll_exchange(
    void *opaque,
    uusb_token_usb_transport_t transport,
    uint8_t *response,
    uint16_t capacity,
    uint16_t *length)
{
    context_t *context = opaque;
    assert(transport == context->transport);
    if (context->result == UUSB_TOKEN_USB_EXCHANGE_COMPLETE) {
        assert(context->response_length <= capacity);
        memcpy(response, context->response, context->response_length);
        *length = context->response_length;
    }
    return context->result;
}

static void cancel_exchange(
    void *opaque, uusb_token_usb_transport_t transport)
{
    context_t *context = opaque;
    assert(transport == context->transport);
    ++context->cancels;
}

static const uusb_token_usb_hooks_t hooks = {
    .start = start_exchange,
    .poll = poll_exchange,
    .cancel = cancel_exchange,
};

static void be32(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)(value >> 24);
    data[1] = (uint8_t)(value >> 16);
    data[2] = (uint8_t)(value >> 8);
    data[3] = (uint8_t)value;
}

static void le32(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)value;
    data[1] = (uint8_t)(value >> 8);
    data[2] = (uint8_t)(value >> 16);
    data[3] = (uint8_t)(value >> 24);
}

static uint32_t get_le32(const uint8_t *data)
{
    return (uint32_t)data[0] |
           ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) |
           ((uint32_t)data[3] << 24);
}

static void fido_initial(
    uint8_t report[64],
    uint32_t cid,
    uint8_t command,
    uint16_t length,
    const uint8_t *payload)
{
    uint16_t copied = length < 57U ? length : 57U;
    memset(report, 0, 64U);
    be32(report, cid);
    report[4] = command;
    report[5] = (uint8_t)(length >> 8);
    report[6] = (uint8_t)length;
    if (copied != 0U) {
        memcpy(&report[7], payload, copied);
    }
}

static void fido_continuation(
    uint8_t report[64],
    uint32_t cid,
    uint8_t sequence,
    const uint8_t *payload,
    uint16_t length)
{
    memset(report, 0, 64U);
    be32(report, cid);
    report[4] = sequence;
    memcpy(&report[5], payload, length);
}

static void take_fido_error(
    uusb_token_usb_t *state, uint32_t cid, uint8_t error)
{
    uint8_t report[64];
    assert(uusb_token_usb_ctaphid_in_peek(state, report));
    assert(report[0] == (uint8_t)(cid >> 24));
    assert(report[3] == (uint8_t)cid);
    assert(report[4] == UUSB_CTAPHID_CMD_ERROR);
    assert(report[5] == 0U && report[6] == 1U && report[7] == error);
    assert(uusb_token_usb_ctaphid_in_accepted(state));
}

static void ccid_header(
    uint8_t message[UUSB_CCID_MAX_MESSAGE_SIZE],
    uint8_t type,
    uint32_t length,
    uint8_t sequence)
{
    memset(message, 0, UUSB_CCID_HEADER_SIZE);
    message[0] = type;
    le32(&message[1], length);
    message[5] = 0U;
    message[6] = sequence;
}

static size_t take_ccid(uusb_token_usb_t *state, uint8_t *output)
{
    size_t total = 0U;
    size_t length;
    while ((length = uusb_token_usb_ccid_in_peek(
                state, &output[total], 64U)) != 0U) {
        assert(uusb_token_usb_ccid_in_accepted(state, length));
        total += length;
    }
    return total;
}

static void test_fido_fragmentation_limits_and_timeout(void)
{
    uusb_token_usb_t state;
    context_t context = {0};
    uint8_t payload[512];
    uint8_t report[64];
    uint8_t output[64];
    const uint32_t cid = UINT32_C(0x01020304);
    size_t index;

    uusb_token_usb_initialize(&state, &hooks, &context);
    for (index = 0U; index < sizeof(payload); ++index) {
        payload[index] = (uint8_t)index;
    }

    fido_initial(report, cid, UUSB_CTAPHID_CMD_PING, 120U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 10U));
    fido_continuation(report, cid, 0U, &payload[57], 59U);
    assert(uusb_token_usb_ctaphid_out(&state, report, 11U));
    fido_continuation(report, cid, 1U, &payload[116], 4U);
    assert(uusb_token_usb_ctaphid_out(&state, report, 12U));
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == UUSB_CTAPHID_CMD_PING);
    assert(output[5] == 0U && output[6] == 120U);
    assert(memcmp(&output[7], payload, 57U) == 0);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == 0U && memcmp(&output[5], &payload[57], 59U) == 0);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == 1U && memcmp(&output[5], &payload[116], 4U) == 0);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));

    fido_initial(report, cid, UUSB_CTAPHID_CMD_PING, 58U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 20U));
    fido_continuation(report, cid, 1U, &payload[57], 1U);
    assert(!uusb_token_usb_ctaphid_out(&state, report, 21U));
    take_fido_error(&state, cid, UUSB_CTAPHID_ERROR_INVALID_SEQUENCE);

    fido_initial(report, cid, UUSB_CTAPHID_CMD_PING, 513U, payload);
    assert(!uusb_token_usb_ctaphid_out(&state, report, 30U));
    take_fido_error(&state, cid, UUSB_CTAPHID_ERROR_INVALID_LENGTH);

    fido_initial(report, cid, UUSB_CTAPHID_CMD_PING, 58U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 40U));
    uusb_token_usb_tick(
        &state, 40U + UUSB_CTAPHID_REASSEMBLY_TIMEOUT_MS - 1U);
    assert(!uusb_token_usb_ctaphid_in_peek(&state, output));
    uusb_token_usb_tick(
        &state, 40U + UUSB_CTAPHID_REASSEMBLY_TIMEOUT_MS);
    take_fido_error(&state, cid, UUSB_CTAPHID_ERROR_MESSAGE_TIMEOUT);

    fido_initial(report, cid, UUSB_CTAPHID_CMD_PING, 512U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 50U));
    for (index = 57U; index < 512U; index += 59U) {
        uint16_t amount = (uint16_t)(512U - index);
        if (amount > 59U) {
            amount = 59U;
        }
        fido_continuation(
            report,
            cid,
            (uint8_t)((index - 57U) / 59U),
            &payload[index],
            amount);
        assert(uusb_token_usb_ctaphid_out(&state, report, (uint32_t)index));
    }
    index = 0U;
    while (uusb_token_usb_ctaphid_in_peek(&state, output)) {
        assert(uusb_token_usb_ctaphid_in_accepted(&state));
        ++index;
    }
    assert(index == 9U);
}

static void test_fido_routing_output_and_cancel(void)
{
    uusb_token_usb_t state;
    context_t context = {0};
    uint8_t report[64];
    uint8_t output[64];
    uint8_t payload[2] = {UINT8_C(0x04), UINT8_C(0xa0)};
    uint8_t nonce[8] = {0U, 1U, 2U, 3U, 4U, 5U, 6U, 7U};
    const uint32_t cid = UINT32_C(0x11223344);
    size_t index;

    uusb_token_usb_initialize(&state, &hooks, &context);
    fido_initial(
        report, UUSB_CTAPHID_BROADCAST_CID, UUSB_CTAPHID_CMD_INIT, 8U, nonce);
    assert(uusb_token_usb_ctaphid_out(&state, report, 1U));
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == UUSB_CTAPHID_CMD_INIT && output[6] == 17U);
    assert(memcmp(&output[7], nonce, 8U) == 0);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));

    fido_initial(report, cid, UUSB_CTAPHID_CMD_CBOR, 2U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 100U));
    assert(context.starts == 1U);
    assert(context.transport == UUSB_TOKEN_USB_TRANSPORT_CTAPHID);
    assert(context.request_length == 3U);
    assert(context.request[0] == UUSB_CTAPHID_CMD_CBOR);
    assert(memcmp(&context.request[1], payload, 2U) == 0);
    assert(uusb_token_usb_keepalive(
        &state, UUSB_CTAPHID_KEEPALIVE_PROCESSING));
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == UUSB_CTAPHID_CMD_KEEPALIVE && output[7] == 1U);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));

    context.response[0] = UUSB_CTAPHID_CMD_CBOR;
    for (index = 1U; index < 61U; ++index) {
        context.response[index] = (uint8_t)index;
    }
    context.response_length = 61U;
    context.result = UUSB_TOKEN_USB_EXCHANGE_COMPLETE;
    uusb_token_usb_poll(&state, 101U);
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == UUSB_CTAPHID_CMD_CBOR && output[6] == 60U);
    assert(memcmp(&output[7], &context.response[1], 57U) == 0);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == 0U && memcmp(&output[5], &context.response[58], 3U) == 0);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));

    context.result = UUSB_TOKEN_USB_EXCHANGE_PENDING;
    fido_initial(report, cid, UUSB_CTAPHID_CMD_CBOR, 2U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 200U));
    fido_initial(report, cid, UUSB_CTAPHID_CMD_CANCEL, 0U, NULL);
    assert(uusb_token_usb_ctaphid_out(&state, report, 201U));
    assert(context.cancels == 1U);
    assert(uusb_token_usb_ctaphid_in_peek(&state, output));
    assert(output[4] == UUSB_CTAPHID_CMD_CBOR);
    assert(output[6] == 1U && output[7] == UUSB_CTAP2_ERROR_KEEPALIVE_CANCEL);
    assert(uusb_token_usb_ctaphid_in_accepted(&state));

    fido_initial(report, cid, UUSB_CTAPHID_CMD_CBOR, 2U, payload);
    assert(uusb_token_usb_ctaphid_out(&state, report, 300U));
    fido_initial(
        report, UINT32_C(0x55667788), UUSB_CTAPHID_CMD_CBOR, 2U, payload);
    assert(!uusb_token_usb_ctaphid_out(&state, report, 301U));
    take_fido_error(
        &state, UINT32_C(0x55667788), UUSB_CTAPHID_ERROR_CHANNEL_BUSY);
    uusb_token_usb_fail_timeout(&state);
    assert(context.cancels == 2U);
    take_fido_error(&state, cid, UUSB_CTAPHID_ERROR_MESSAGE_TIMEOUT);
}

static void test_ccid_framing_state_abort_and_time_extension(void)
{
    uusb_token_usb_t state;
    context_t context = {0};
    uint8_t message[UUSB_CCID_MAX_MESSAGE_SIZE];
    uint8_t output[UUSB_CCID_MAX_MESSAGE_SIZE];
    uint8_t parameters[7] = {0x12U, 0x10U, 1U, 0x4dU, 0U, 64U, 0U};
    uint8_t apdu[5] = {0U, 0xa4U, 0U, 0U, 0U};
    size_t length;

    uusb_token_usb_initialize(&state, &hooks, &context);
    ccid_header(message, UUSB_CCID_PC_TO_RDR_ICC_POWER_ON, 0U, 1U);
    assert(uusb_token_usb_ccid_out(&state, message, 4U, 10U));
    assert(uusb_token_usb_ccid_out(&state, &message[4], 6U, 11U));
    length = take_ccid(&state, output);
    assert(length == 14U && output[0] == UUSB_CCID_RDR_TO_PC_DATA_BLOCK);
    assert(get_le32(&output[1]) == 4U && output[7] == UUSB_CCID_STATUS_ICC_ACTIVE);

    ccid_header(message, UUSB_CCID_PC_TO_RDR_SET_PARAMETERS, 7U, 2U);
    message[7] = UUSB_CCID_PROTOCOL_T1;
    memcpy(&message[10], parameters, 7U);
    assert(uusb_token_usb_ccid_out(&state, message, 12U, 20U));
    assert(uusb_token_usb_ccid_out(&state, &message[12], 5U, 21U));
    length = take_ccid(&state, output);
    assert(length == 17U && output[0] == UUSB_CCID_RDR_TO_PC_PARAMETERS);
    assert(memcmp(&output[10], parameters, 7U) == 0);

    ccid_header(message, UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK, 5U, 3U);
    memcpy(&message[10], apdu, 5U);
    assert(uusb_token_usb_ccid_out(&state, message, 7U, 30U));
    assert(uusb_token_usb_ccid_out(&state, &message[7], 8U, 31U));
    assert(context.starts == 1U);
    assert(context.transport == UUSB_TOKEN_USB_TRANSPORT_CCID);
    assert(context.request_length == 6U);
    assert(context.request[0] == UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK);
    assert(memcmp(&context.request[1], apdu, 5U) == 0);

    assert(uusb_token_usb_time_extension(&state, 3U));
    length = take_ccid(&state, output);
    assert(length == 10U && output[0] == UUSB_CCID_RDR_TO_PC_DATA_BLOCK);
    assert(output[7] == (uint8_t)(UUSB_CCID_STATUS_ICC_ACTIVE |
                                  UUSB_CCID_STATUS_TIME_EXTENSION));
    assert(output[8] == 3U);

    context.response[0] = UUSB_CCID_RDR_TO_PC_DATA_BLOCK;
    context.response[1] = 0x90U;
    context.response[2] = 0U;
    context.response_length = 3U;
    context.result = UUSB_TOKEN_USB_EXCHANGE_COMPLETE;
    uusb_token_usb_poll(&state, 32U);
    length = take_ccid(&state, output);
    assert(length == 12U && get_le32(&output[1]) == 2U);
    assert(output[6] == 3U && output[10] == 0x90U && output[11] == 0U);

    context.result = UUSB_TOKEN_USB_EXCHANGE_PENDING;
    ccid_header(message, UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK, 5U, 4U);
    memcpy(&message[10], apdu, 5U);
    assert(uusb_token_usb_ccid_out(&state, message, 15U, 40U));
    ccid_header(message, UUSB_CCID_PC_TO_RDR_ABORT, 0U, 5U);
    assert(uusb_token_usb_ccid_out(&state, message, 10U, 41U));
    assert(context.cancels == 1U);
    length = take_ccid(&state, output);
    assert(length == 10U && output[0] == UUSB_CCID_RDR_TO_PC_SLOT_STATUS);
    assert((output[7] & UUSB_CCID_STATUS_COMMAND_FAILED) != 0U);
    assert(output[8] == UUSB_CCID_ERROR_COMMAND_ABORTED);

    ccid_header(
        message,
        UUSB_CCID_PC_TO_RDR_TRANSFER_BLOCK,
        UUSB_CCID_MAX_PAYLOAD_SIZE + 1U,
        6U);
    assert(!uusb_token_usb_ccid_out(&state, message, 10U, 50U));
    length = take_ccid(&state, output);
    assert(length == 10U && output[8] == UUSB_CCID_ERROR_BAD_LENGTH);

    ccid_header(message, UUSB_CCID_PC_TO_RDR_ICC_POWER_OFF, 0U, 7U);
    assert(uusb_token_usb_ccid_out(&state, message, 10U, 60U));
    length = take_ccid(&state, output);
    assert(length == 10U && output[7] == UUSB_CCID_STATUS_ICC_INACTIVE);
}

static void test_otp_acceptance_and_release_failure(void)
{
    uusb_token_usb_t state;
    uint8_t report[8] = {2U, 0U, 30U, 0U, 0U, 0U, 0U, 0U};
    uint8_t output[8];
    uint8_t repeated[8];
    const uint8_t zero[8] = {0};

    uusb_token_usb_initialize(&state, NULL, NULL);
    assert(uusb_token_usb_otp_report_set(&state, report));
    assert(uusb_token_usb_otp_in_peek(&state, output));
    assert(uusb_token_usb_otp_in_peek(&state, repeated));
    assert(memcmp(output, report, 8U) == 0 && memcmp(repeated, report, 8U) == 0);
    assert(!uusb_token_usb_otp_report_set(&state, report));
    uusb_token_usb_otp_in_failed(&state);
    assert(uusb_token_usb_otp_in_peek(&state, output));
    assert(memcmp(output, zero, 8U) == 0);
    assert(uusb_token_usb_otp_in_accepted(&state));
    assert(!uusb_token_usb_otp_in_peek(&state, output));

    assert(uusb_token_usb_otp_report_set(&state, report));
    assert(uusb_token_usb_otp_in_accepted(&state));
    assert(uusb_token_usb_otp_in_peek(&state, output));
    assert(memcmp(output, zero, 8U) == 0);
    assert(uusb_token_usb_otp_in_accepted(&state));
    assert(!uusb_token_usb_otp_in_peek(&state, output));

    assert(uusb_token_usb_otp_report_set(&state, report));
    uusb_token_usb_fail_unmount(&state);
    assert(uusb_token_usb_otp_in_peek(&state, output));
    assert(memcmp(output, zero, 8U) == 0);
}

int main(void)
{
    test_fido_fragmentation_limits_and_timeout();
    test_fido_routing_output_and_cancel();
    test_ccid_framing_state_abort_and_time_extension();
    test_otp_acceptance_and_release_failure();
    return 0;
}
