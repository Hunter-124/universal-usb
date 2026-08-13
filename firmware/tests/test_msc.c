#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "uusb_msc.h"

#define TEST_ASYNC (-2)
#define TEST_ERROR (-1)

static unsigned int completion_count;
static int32_t completion_bytes;
static uint8_t sense_key;

bool tud_msc_set_sense(
    uint8_t lun, uint8_t key, uint8_t code, uint8_t qualifier)
{
    (void)lun;
    (void)code;
    (void)qualifier;
    sense_key = key;
    return true;
}

bool tud_msc_async_io_done(int32_t bytes_io, bool in_isr)
{
    assert(!in_isr);
    completion_count++;
    completion_bytes = bytes_io;
    return true;
}

int main(void)
{
    uusb_mailbox_boot(
        UUSB_PROFILE_HID_MSC, UUSB_FIRMWARE_VERSION(0, 1, 0), true,
        NULL, NULL);
    uusb_msc_state_t state;
    uusb_msc_initialize(&state);
    assert(uusb_msc_attach(&state, 4U, true) == UUSB_STATUS_OK);

    uint8_t block[UUSB_MSC_BLOCK_SIZE];
    memset(block, 0xa5, sizeof(block));
    assert(uusb_msc_begin_read(&state, 0U, 1U, block, sizeof(block)) ==
           TEST_ERROR);
    assert(!state.pending);
    assert(sense_key != 0U);

    assert(uusb_msc_begin_read(&state, 2U, 0U, block, sizeof(block)) ==
           TEST_ASYNC);
    assert(state.pending);
    uint32_t const sequence = state.sequence;
    uint32_t const epoch = state.pending_epoch;
    uint8_t expected[UUSB_MSC_BLOCK_SIZE];
    for (uint32_t index = 0U; index < sizeof(block); ++index) {
        expected[index] = (uint8_t)index;
        uusb_mailbox.block.data[index] = expected[index];
    }
    uusb_mailbox.block.response_status = UUSB_STATUS_OK;
    uusb_mailbox.block.response_crc = uusb_mailbox_block_response_crc(
        UUSB_STATUS_OK, UUSB_BLOCK_READ, UUSB_MSC_BLOCK_SIZE, expected);
    uusb_mailbox.block.response_seq = sequence;
    uusb_msc_poll(&state);
    assert(!state.pending);
    assert(completion_count == 1U);
    assert(completion_bytes == (int32_t)UUSB_MSC_BLOCK_SIZE);
    for (uint32_t index = 0U; index < sizeof(block); ++index) {
        assert(block[index] == (uint8_t)index);
    }
    uusb_msc_poll(&state);
    assert(completion_count == 1U);

    assert(uusb_msc_begin_write(&state, 1U, 0U, block, sizeof(block)) ==
           TEST_ASYNC);
    assert(uusb_mailbox.block.epoch == epoch);
    uusb_msc_bot_reset(&state);
    assert(!state.pending);
    assert(state.present);
    assert(uusb_mailbox.header.media_epoch != epoch);
    assert(completion_count == 1U);
    uusb_mailbox.block.response_seq = state.sequence;
    uusb_msc_poll(&state);
    assert(completion_count == 1U);

    uusb_msc_initialize(&state);
    assert(uusb_msc_attach(&state, 1U, false) == UUSB_STATUS_OK);
    assert(uusb_msc_begin_write(&state, 0U, 0U, block, sizeof(block)) ==
           TEST_ERROR);
    assert(!state.pending);
    return 0;
}
