#ifndef UUSB_MSC_H
#define UUSB_MSC_H

#include <stdbool.h>
#include <stdint.h>

#include "uusb_mailbox.h"

#define UUSB_MSC_BLOCK_SIZE UINT32_C(512)
#define UUSB_MSC_IO_TIMEOUT_MS UINT32_C(1000)

typedef struct {
    bool present;
    bool writable;
    bool removal_prevented;
    bool pending;
    uint32_t block_count;
    uint32_t sequence;
    uint32_t pending_epoch;
    uint32_t pending_lba;
    uint32_t pending_started_ms;
    uusb_block_operation_t pending_operation;
    uint8_t *pending_buffer;
} uusb_msc_state_t;

void uusb_msc_initialize(uusb_msc_state_t *state);
uusb_control_status_t uusb_msc_attach(
    uusb_msc_state_t *state, uint32_t block_count, bool writable);
uusb_control_status_t uusb_msc_detach(uusb_msc_state_t *state);
void uusb_msc_abandon(
    uusb_msc_state_t *state, uusb_control_status_t reason);
void uusb_msc_fail_pending(
    uusb_msc_state_t *state, uusb_control_status_t reason);
int32_t uusb_msc_begin_read(
    uusb_msc_state_t *state,
    uint32_t lba,
    uint32_t offset,
    void *buffer,
    uint32_t buffer_size);
int32_t uusb_msc_begin_write(
    uusb_msc_state_t *state,
    uint32_t lba,
    uint32_t offset,
    const void *buffer,
    uint32_t buffer_size);
void uusb_msc_poll(uusb_msc_state_t *state);
bool uusb_msc_test_unit_ready(const uusb_msc_state_t *state);
bool uusb_msc_is_writable(const uusb_msc_state_t *state);
bool uusb_msc_set_prevent_removal(
    uusb_msc_state_t *state, bool prevent);
bool uusb_msc_eject(uusb_msc_state_t *state);
void uusb_msc_bot_reset(uusb_msc_state_t *state);

#endif
