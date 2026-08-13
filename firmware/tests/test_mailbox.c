#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "uusb_crc32c.h"
#include "uusb_mailbox.h"

static unsigned int invalidations;
static unsigned int executions;
static unsigned int releases;
static unsigned int block_failures;
static unsigned int token_failures;

static void invalidate(void *context)
{
    (void)context;
    invalidations++;
}

static uusb_control_status_t execute(
    void *context,
    uusb_control_opcode_t opcode,
    const uint8_t *request,
    uint16_t request_length,
    uint8_t *response,
    uint16_t *response_length)
{
    (void)context;
    (void)opcode;
    (void)request;
    (void)request_length;
    (void)response;
    executions++;
    *response_length = 0U;
    return UUSB_STATUS_OK;
}

static void release(void *context)
{
    (void)context;
    releases++;
}

static void fail_block(void *context, uusb_control_status_t status)
{
    (void)context;
    assert(status == UUSB_STATUS_TIMEOUT);
    block_failures++;
}

static void fail_token(void *context, uusb_control_status_t status)
{
    (void)context;
    assert(status == UUSB_STATUS_TIMEOUT);
    token_failures++;
}

static void copy_mailbox_bytes(uint8_t *destination, size_t offset, size_t length)
{
    const volatile uint8_t *source = (const volatile uint8_t *)&uusb_mailbox;
    for (size_t index = 0U; index < length; ++index) {
        destination[index] = source[offset + index];
    }
}

int main(void)
{
    static const uint8_t check[] = "123456789";
    assert(uusb_crc32c(check, sizeof(check) - 1U) == UUSB_CRC32C_CHECK);

    static const uint8_t golden_header[64] = {
        0x55, 0x53, 0x42, 0x31, 0x01, 0x00, 0x00, 0x10,
        0x03, 0x02, 0x01, 0x00, 0x04, 0x00, 0x00, 0x00,
        0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    };
    static const uint8_t golden_token_prefix[19] = {
        0x04, 0x03, 0x02, 0x01, 0x02, 0x00, 0x03, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x11, 0x22, 0x33, 0x44,
        0x61, 0x62, 0x63,
    };

    memset((void *)&uusb_mailbox, 0xa5, sizeof(uusb_mailbox));
    uusb_mailbox_hooks_t const hooks = {
        .invalidate_local_state = invalidate,
        .validate_command = NULL,
        .execute_command = execute,
        .release_hid = release,
        .fail_pending_block = fail_block,
        .fail_pending_token = fail_token,
    };
    uusb_mailbox_boot(UUSB_PROFILE_SECURITY_TOKEN,
                      UUSB_FIRMWARE_VERSION(1, 2, 3), true,
                      &hooks, NULL);
    uint8_t header[64];
    copy_mailbox_bytes(header, 0U, sizeof(header));
    assert(memcmp(header, golden_header, sizeof(header)) == 0);
    assert(invalidations == 1U);
    for (size_t offset = 0x364U; offset < 0x400U; ++offset) {
        assert(((const volatile uint8_t *)&uusb_mailbox)[offset] == 0U);
    }
    for (size_t offset = 0x614U; offset < 0x640U; ++offset) {
        assert(((const volatile uint8_t *)&uusb_mailbox)[offset] == 0U);
    }
    for (size_t offset = 0x854U; offset < 0x1000U; ++offset) {
        assert(((const volatile uint8_t *)&uusb_mailbox)[offset] == 0U);
    }

    uusb_mailbox.token_request.request_seq = UINT32_C(0x01020304);
    uusb_mailbox.token_request.transport = UUSB_TOKEN_CCID;
    uusb_mailbox.token_request.length = 3U;
    uusb_mailbox.token_request.flags = 0U;
    uusb_mailbox.token_request.request_crc = UINT32_C(0x44332211);
    uusb_mailbox.token_request.data[0] = 0x61U;
    uusb_mailbox.token_request.data[1] = 0x62U;
    uusb_mailbox.token_request.data[2] = 0x63U;
    uint8_t token_prefix[19];
    copy_mailbox_bytes(token_prefix, 0x400U, sizeof(token_prefix));
    assert(memcmp(token_prefix, golden_token_prefix, sizeof(token_prefix)) == 0);

    uusb_mailbox.control.host_seq = 9U;
    uusb_mailbox.control.host_ack = 8U;
    uusb_mailbox_boot(UUSB_PROFILE_SECURITY_TOKEN,
                      UUSB_FIRMWARE_VERSION(1, 2, 3), true,
                      &hooks, NULL);
    assert(invalidations == 2U);
    assert(uusb_mailbox.header.boot_counter == 2U);
    assert(uusb_mailbox.control.host_ack == 9U);
    assert(uusb_mailbox.control.response_status ==
           UUSB_STATUS_RESET_DURING_COMMAND);
    assert(uusb_mailbox.header.last_error ==
           UUSB_STATUS_RESET_DURING_COMMAND);
    assert(uusb_mailbox.token_request.request_ack ==
           uusb_mailbox.token_request.request_seq);
    assert(uusb_mailbox.token_response.response_ack ==
           uusb_mailbox.token_response.response_seq);
    assert(executions == 0U);

    uusb_mailbox_set_mounted(true);
    uusb_mailbox_set_media(true, true);
    uusb_mailbox_set_block_pending(true);
    for (unsigned int tick = 0U; tick < 1000U; ++tick) {
        uusb_mailbox_tick_1ms();
    }
    uusb_mailbox_poll();
    assert(releases == 1U);
    assert(block_failures == 1U);
    assert(token_failures == 1U);
    assert((uusb_mailbox.header.usb_flags & UUSB_USB_FLAG_MEDIA_PRESENT) == 0U);
    assert((uusb_mailbox.header.usb_flags & UUSB_USB_FLAG_MEDIA_WRITABLE) == 0U);
    assert((uusb_mailbox.header.usb_flags & UUSB_USB_FLAG_BLOCK_PENDING) == 0U);
    assert((uusb_mailbox.header.usb_flags & UUSB_USB_FLAG_HID_RELEASE_PENDING) != 0U);

    return 0;
}
