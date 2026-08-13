#ifndef UUSB_UVC_PATTERN_H
#define UUSB_UVC_PATTERN_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "uusb_mailbox.h"

#define UUSB_UVC_WIDTH UINT16_C(128)
#define UUSB_UVC_HEIGHT UINT16_C(96)
#define UUSB_UVC_BYTES_PER_PIXEL UINT8_C(2)
#define UUSB_UVC_FRAME_BYTES UINT32_C(24576)
#define UUSB_UVC_FRAME_INTERVAL_100NS UINT32_C(1000000)
#define UUSB_UVC_PAYLOAD_BYTES UINT16_C(256)
#define UUSB_UVC_PAYLOAD_HEADER_BYTES UINT8_C(2)
#define UUSB_UVC_PAYLOAD_DATA_BYTES UINT16_C(254)

#define UUSB_UVC_PAYLOAD_FID UINT8_C(0x01)
#define UUSB_UVC_PAYLOAD_EOF UINT8_C(0x02)
#define UUSB_UVC_PAYLOAD_EOH UINT8_C(0x80)

typedef struct {
    uusb_uvc_pattern_t pattern;
    uint8_t frame_id;
    uint32_t frame_number;
    uint32_t frame_offset;
    uint32_t packet_count;
} uusb_uvc_packet_state_t;

bool uusb_uvc_pattern_is_valid(uusb_uvc_pattern_t pattern);
uint8_t uusb_uvc_pattern_byte(
    uusb_uvc_pattern_t pattern, uint32_t frame_number,
    uint32_t frame_offset);
void uusb_uvc_pattern_fill(
    uusb_uvc_pattern_t pattern, uint32_t frame_number,
    uint32_t frame_offset, uint8_t *destination, size_t length);

void uusb_uvc_packet_state_initialize(
    uusb_uvc_packet_state_t *state, uusb_uvc_pattern_t pattern);
bool uusb_uvc_packet_state_set_pattern(
    uusb_uvc_packet_state_t *state, uusb_uvc_pattern_t pattern);
size_t uusb_uvc_packet_next(
    uusb_uvc_packet_state_t *state, uint8_t *packet,
    size_t packet_capacity);

#endif
