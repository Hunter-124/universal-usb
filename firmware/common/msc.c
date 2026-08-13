#include "uusb_msc.h"

#include <stddef.h>

#include "tusb.h"

#define UUSB_SENSE_NOT_READY UINT8_C(0x02)
#define UUSB_SENSE_MEDIUM_ERROR UINT8_C(0x03)
#define UUSB_SENSE_HARDWARE_ERROR UINT8_C(0x04)
#define UUSB_SENSE_ILLEGAL_REQUEST UINT8_C(0x05)
#define UUSB_SENSE_DATA_PROTECT UINT8_C(0x07)
#define UUSB_SENSE_ABORTED_COMMAND UINT8_C(0x0b)
#define UUSB_ASC_INVALID_FIELD UINT8_C(0x24)
#define UUSB_ASC_MEDIUM_NOT_PRESENT UINT8_C(0x3a)
#define UUSB_ASC_UNRECOVERED_READ UINT8_C(0x11)
#define UUSB_ASC_WRITE_PROTECTED UINT8_C(0x27)
#define UUSB_ASC_INTERNAL_FAILURE UINT8_C(0x44)
#define UUSB_ASC_DATA_PHASE_ERROR UINT8_C(0x4b)
#define UUSB_COMPILER_BARRIER() __asm__ volatile("" ::: "memory")

static void copy_to_volatile(
    volatile uint8_t *destination, const uint8_t *source, uint32_t length)
{
    for (uint32_t index = 0U; index < length; ++index) {
        destination[index] = source[index];
    }
}

static void copy_from_volatile(
    uint8_t *destination, const volatile uint8_t *source, uint32_t length)
{
    for (uint32_t index = 0U; index < length; ++index) {
        destination[index] = source[index];
    }
}

static void set_sense(uint8_t key, uint8_t code)
{
    tud_msc_set_sense(0U, key, code, 0U);
}

void uusb_msc_initialize(uusb_msc_state_t *state)
{
    if (state != NULL) {
        *state = (uusb_msc_state_t){0};
    }
    uusb_mailbox_set_media(false, false);
    uusb_mailbox_set_block_pending(false);
}

uusb_control_status_t uusb_msc_attach(
    uusb_msc_state_t *state, uint32_t block_count, bool writable)
{
    if ((state == NULL) || (block_count == 0U)) {
        return UUSB_STATUS_OUT_OF_RANGE;
    }
    if (state->present || state->pending) {
        return UUSB_STATUS_BUSY;
    }
    state->present = true;
    state->writable = writable;
    state->block_count = block_count;
    state->removal_prevented = false;
    uusb_mailbox.header.media_epoch++;
    uusb_mailbox_set_media(true, writable);
    return UUSB_STATUS_OK;
}

uusb_control_status_t uusb_msc_detach(uusb_msc_state_t *state)
{
    if ((state == NULL) || !state->present) {
        return UUSB_STATUS_BAD_STATE;
    }
    if (state->removal_prevented) {
        return UUSB_STATUS_BUSY;
    }
    if (state->pending) {
        return UUSB_STATUS_BUSY;
    }
    state->present = false;
    state->writable = false;
    state->block_count = 0U;
    uusb_mailbox.header.media_epoch++;
    uusb_mailbox_set_media(false, false);
    return UUSB_STATUS_OK;
}

static int32_t reject_io(uint8_t sense_key, uint8_t sense_code)
{
    set_sense(sense_key, sense_code);
    return TUD_MSC_RET_ERROR;
}

static int32_t begin_io(
    uusb_msc_state_t *state,
    uusb_block_operation_t operation,
    uint32_t lba,
    uint32_t offset,
    void *buffer,
    uint32_t buffer_size)
{
    if ((state == NULL) || (buffer == NULL) || (offset != 0U) ||
        (buffer_size != UUSB_MSC_BLOCK_SIZE)) {
        return reject_io(UUSB_SENSE_ILLEGAL_REQUEST, UUSB_ASC_INVALID_FIELD);
    }
    if (!state->present) {
        return reject_io(UUSB_SENSE_NOT_READY, UUSB_ASC_MEDIUM_NOT_PRESENT);
    }
    if (state->pending) {
        return reject_io(UUSB_SENSE_ABORTED_COMMAND, UUSB_ASC_DATA_PHASE_ERROR);
    }
    if (lba >= state->block_count) {
        return reject_io(UUSB_SENSE_ILLEGAL_REQUEST, UUSB_ASC_INVALID_FIELD);
    }
    if ((operation == UUSB_BLOCK_WRITE) && !state->writable) {
        return reject_io(UUSB_SENSE_DATA_PROTECT, UUSB_ASC_WRITE_PROTECTED);
    }

    volatile uusb_block_slot_t *const slot = &uusb_mailbox.block;
    uint32_t sequence = state->sequence + 1U;
    if (sequence == 0U) {
        sequence = 1U;
    }
    state->sequence = sequence;
    state->pending = true;
    state->pending_epoch = uusb_mailbox.header.media_epoch;
    state->pending_operation = operation;
    state->pending_lba = lba;
    state->pending_started_ms = uusb_mailbox_uptime_ms();
    state->pending_buffer = (operation == UUSB_BLOCK_READ) ? buffer : NULL;

    slot->epoch = state->pending_epoch;
    slot->op = (uint8_t)operation;
    slot->reserved[0] = 0U;
    slot->reserved[1] = 0U;
    slot->reserved[2] = 0U;
    slot->lba = lba;
    slot->data_length = UUSB_MSC_BLOCK_SIZE;
    if (operation == UUSB_BLOCK_WRITE) {
        copy_to_volatile(slot->data, buffer, UUSB_MSC_BLOCK_SIZE);
    }
    slot->request_crc = uusb_mailbox_block_request_crc(
        state->pending_epoch, operation, lba, UUSB_MSC_BLOCK_SIZE,
        (operation == UUSB_BLOCK_WRITE) ? (const uint8_t *)buffer : NULL);
    UUSB_COMPILER_BARRIER();
    slot->request_seq = sequence;
    uusb_mailbox_set_block_pending(true);
    return TUD_MSC_RET_ASYNC;
}

int32_t uusb_msc_begin_read(
    uusb_msc_state_t *state, uint32_t lba, uint32_t offset,
    void *buffer, uint32_t buffer_size)
{
    return begin_io(
        state, UUSB_BLOCK_READ, lba, offset, buffer, buffer_size);
}

int32_t uusb_msc_begin_write(
    uusb_msc_state_t *state, uint32_t lba, uint32_t offset,
    const void *buffer, uint32_t buffer_size)
{
    return begin_io(state, UUSB_BLOCK_WRITE, lba, offset,
                    (void *)(uintptr_t)buffer, buffer_size);
}

static void complete_error(
    uusb_msc_state_t *state, uint8_t sense_key, uint8_t sense_code)
{
    state->pending = false;
    state->pending_buffer = NULL;
    uusb_mailbox_set_block_pending(false);
    uusb_mailbox.header.block_completed++;
    set_sense(sense_key, sense_code);
    (void)tud_msc_async_io_done(TUD_MSC_RET_ERROR, false);
}

void uusb_msc_fail_pending(
    uusb_msc_state_t *state, uusb_control_status_t reason)
{
    if (state == NULL) {
        return;
    }
    uusb_mailbox.header.last_error = (uint32_t)reason;
    if (state->pending) {
        complete_error(state, UUSB_SENSE_HARDWARE_ERROR,
                       UUSB_ASC_INTERNAL_FAILURE);
    }
    state->present = false;
    state->writable = false;
    state->block_count = 0U;
    state->removal_prevented = false;
    uusb_mailbox_set_media(false, false);
}

void uusb_msc_abandon(
    uusb_msc_state_t *state, uusb_control_status_t reason)
{
    if (state == NULL) {
        return;
    }
    state->pending = false;
    state->pending_buffer = NULL;
    state->present = false;
    state->writable = false;
    state->block_count = 0U;
    state->removal_prevented = false;
    uusb_mailbox.header.media_epoch++;
    uusb_mailbox.header.last_error = (uint32_t)reason;
    uusb_mailbox_set_media(false, false);
    uusb_mailbox_set_block_pending(false);
}

void uusb_msc_poll(uusb_msc_state_t *state)
{
    if ((state == NULL) || !state->pending) {
        return;
    }
    if ((uint32_t)(uusb_mailbox_uptime_ms() - state->pending_started_ms) >=
        UUSB_MSC_IO_TIMEOUT_MS) {
        complete_error(state, UUSB_SENSE_HARDWARE_ERROR,
                       UUSB_ASC_INTERNAL_FAILURE);
        return;
    }

    volatile uusb_block_slot_t *const slot = &uusb_mailbox.block;
    uint32_t const response_sequence = slot->response_seq;
    UUSB_COMPILER_BARRIER();
    if (response_sequence != state->sequence) {
        return;
    }
    if ((slot->request_seq != state->sequence) ||
        (slot->epoch != state->pending_epoch) ||
        (slot->op != (uint8_t)state->pending_operation) ||
        (slot->lba != state->pending_lba) ||
        (slot->data_length != UUSB_MSC_BLOCK_SIZE)) {
        complete_error(state, UUSB_SENSE_ABORTED_COMMAND,
                       UUSB_ASC_DATA_PHASE_ERROR);
        return;
    }

    uusb_control_status_t const status =
        (uusb_control_status_t)slot->response_status;
    uint8_t read_data[UUSB_MSC_BLOCK_SIZE];
    const uint8_t *response_data = NULL;
    if ((status == UUSB_STATUS_OK) &&
        (state->pending_operation == UUSB_BLOCK_READ)) {
        copy_from_volatile(read_data, slot->data, UUSB_MSC_BLOCK_SIZE);
        response_data = read_data;
    }
    UUSB_COMPILER_BARRIER();
    if (slot->response_seq != response_sequence) {
        return;
    }
    uint32_t const expected_crc = uusb_mailbox_block_response_crc(
        status, state->pending_operation, UUSB_MSC_BLOCK_SIZE, response_data);
    if (slot->response_crc != expected_crc) {
        complete_error(state, UUSB_SENSE_ABORTED_COMMAND,
                       UUSB_ASC_DATA_PHASE_ERROR);
        return;
    }
    if (status != UUSB_STATUS_OK) {
        complete_error(state,
                       (status == UUSB_STATUS_OUT_OF_RANGE)
                           ? UUSB_SENSE_ILLEGAL_REQUEST
                           : UUSB_SENSE_MEDIUM_ERROR,
                       (status == UUSB_STATUS_OUT_OF_RANGE)
                           ? UUSB_ASC_INVALID_FIELD
                           : UUSB_ASC_UNRECOVERED_READ);
        return;
    }

    if (state->pending_operation == UUSB_BLOCK_READ) {
        uint8_t *const destination = state->pending_buffer;
        for (uint32_t index = 0U; index < UUSB_MSC_BLOCK_SIZE; ++index) {
            destination[index] = read_data[index];
        }
    }
    state->pending = false;
    state->pending_buffer = NULL;
    uusb_mailbox_set_block_pending(false);
    uusb_mailbox.header.block_completed++;
    (void)tud_msc_async_io_done((int32_t)UUSB_MSC_BLOCK_SIZE, false);
}

bool uusb_msc_test_unit_ready(const uusb_msc_state_t *state)
{
    if ((state == NULL) || !state->present) {
        set_sense(UUSB_SENSE_NOT_READY, UUSB_ASC_MEDIUM_NOT_PRESENT);
        return false;
    }
    return true;
}

bool uusb_msc_is_writable(const uusb_msc_state_t *state)
{
    return (state != NULL) && state->present && state->writable;
}

bool uusb_msc_set_prevent_removal(
    uusb_msc_state_t *state, bool prevent)
{
    if ((state == NULL) || !state->present) {
        set_sense(UUSB_SENSE_NOT_READY, UUSB_ASC_MEDIUM_NOT_PRESENT);
        return false;
    }
    state->removal_prevented = prevent;
    return true;
}

bool uusb_msc_eject(uusb_msc_state_t *state)
{
    if ((state == NULL) || !state->present) {
        return true;
    }
    if (state->removal_prevented || state->pending) {
        set_sense(UUSB_SENSE_ILLEGAL_REQUEST, UUSB_ASC_INVALID_FIELD);
        return false;
    }
    return uusb_msc_detach(state) == UUSB_STATUS_OK;
}

void uusb_msc_bot_reset(uusb_msc_state_t *state)
{
    if (state == NULL) {
        return;
    }
    state->pending = false;
    state->pending_buffer = NULL;
    uusb_mailbox.header.media_epoch++;
    uusb_mailbox.header.last_error = (uint32_t)UUSB_STATUS_IO_ERROR;
    uusb_mailbox_set_block_pending(false);
    uusb_mailbox_set_media(state->present, state->writable);
}
