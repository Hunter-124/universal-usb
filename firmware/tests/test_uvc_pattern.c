#include <assert.h>
#include <stddef.h>
#include <stdint.h>

#include "uusb_uvc_pattern.h"

static uint32_t pixel_offset(uint16_t x, uint16_t y)
{
    return (((uint32_t)y * UUSB_UVC_WIDTH) + x) * 2U;
}

static void test_patterns(void)
{
    assert(UUSB_UVC_WIDTH == 128U);
    assert(UUSB_UVC_HEIGHT == 96U);
    assert(UUSB_UVC_FRAME_BYTES == 24576U);
    assert(UUSB_UVC_FRAME_INTERVAL_100NS == 1000000U);

    assert(uusb_uvc_pattern_is_valid(UUSB_UVC_BARS));
    assert(uusb_uvc_pattern_is_valid(UUSB_UVC_CHECKER));
    assert(uusb_uvc_pattern_is_valid(UUSB_UVC_GRADIENT));
    assert(!uusb_uvc_pattern_is_valid((uusb_uvc_pattern_t)3U));

    uint32_t const bar_boundary = pixel_offset(14U, 50U);
    assert(uusb_uvc_pattern_byte(UUSB_UVC_BARS, 0U, bar_boundary) == 180U);
    assert(uusb_uvc_pattern_byte(UUSB_UVC_BARS, 1U, bar_boundary) == 168U);

    uint32_t const checker_boundary = pixel_offset(10U, 30U);
    assert(uusb_uvc_pattern_byte(
               UUSB_UVC_CHECKER, 0U, checker_boundary) == 200U);
    assert(uusb_uvc_pattern_byte(
               UUSB_UVC_CHECKER, 1U, checker_boundary) == 48U);

    uint32_t const gradient_sample = pixel_offset(80U, 50U);
    uint8_t const gradient_frame_zero = uusb_uvc_pattern_byte(
        UUSB_UVC_GRADIENT, 0U, gradient_sample);
    uint8_t const gradient_frame_one = uusb_uvc_pattern_byte(
        UUSB_UVC_GRADIENT, 1U, gradient_sample);
    assert(gradient_frame_zero != gradient_frame_one);
    assert(gradient_frame_zero >= 16U && gradient_frame_zero <= 235U);

    /* The least-significant hex digit changes visibly from 0 to 1. */
    uint32_t const counter_stroke = pixel_offset(60U, 4U);
    assert(uusb_uvc_pattern_byte(
               UUSB_UVC_BARS, 0U, counter_stroke) == 235U);
    assert(uusb_uvc_pattern_byte(
               UUSB_UVC_BARS, 1U, counter_stroke) == 16U);

    uint8_t filled[13];
    uusb_uvc_pattern_fill(
        UUSB_UVC_CHECKER, 7U, 253U, filled, sizeof(filled));
    for (size_t index = 0U; index < sizeof(filled); ++index) {
        assert(filled[index] == uusb_uvc_pattern_byte(
                                    UUSB_UVC_CHECKER, 7U,
                                    253U + (uint32_t)index));
    }
    assert(uusb_uvc_pattern_byte(
               UUSB_UVC_GRADIENT, 0U, UUSB_UVC_FRAME_BYTES) == 0U);

    uint8_t end[5] = {0xffU, 0xffU, 0xffU, 0xffU, 0xffU};
    uusb_uvc_pattern_fill(
        UUSB_UVC_BARS, 0U, UUSB_UVC_FRAME_BYTES - 2U,
        end, sizeof(end));
    assert(end[0] != 0U || end[1] != 0U);
    assert(end[2] == 0U && end[3] == 0U && end[4] == 0U);
}

static void test_packet_framing(void)
{
    uusb_uvc_packet_state_t state;
    uusb_uvc_packet_state_initialize(&state, UUSB_UVC_BARS);
    assert(state.pattern == UUSB_UVC_BARS);
    assert(state.frame_number == 0U);
    assert(state.frame_offset == 0U);
    assert(state.frame_id == 0U);

    uint8_t packet[UUSB_UVC_PAYLOAD_BYTES];
    assert(uusb_uvc_packet_next(&state, packet, 2U) == 0U);
    assert(state.frame_offset == 0U && state.packet_count == 0U);

    size_t length = uusb_uvc_packet_next(
        &state, packet, sizeof(packet));
    assert(length == UUSB_UVC_PAYLOAD_BYTES);
    assert(packet[0] == UUSB_UVC_PAYLOAD_HEADER_BYTES);
    assert(packet[1] == UUSB_UVC_PAYLOAD_EOH);
    assert(packet[2] == uusb_uvc_pattern_byte(UUSB_UVC_BARS, 0U, 0U));
    assert(packet[255] ==
           uusb_uvc_pattern_byte(UUSB_UVC_BARS, 0U, 253U));

    uint32_t data_total = (uint32_t)length - UUSB_UVC_PAYLOAD_HEADER_BYTES;
    uint32_t packets = 1U;
    while ((packet[1] & UUSB_UVC_PAYLOAD_EOF) == 0U) {
        length = uusb_uvc_packet_next(&state, packet, sizeof(packet));
        assert(length >= UUSB_UVC_PAYLOAD_HEADER_BYTES);
        data_total += (uint32_t)length - UUSB_UVC_PAYLOAD_HEADER_BYTES;
        ++packets;
    }
    assert(data_total == UUSB_UVC_FRAME_BYTES);
    assert(packets == 97U);
    assert(length == 194U);
    assert(packet[1] == (UUSB_UVC_PAYLOAD_EOH | UUSB_UVC_PAYLOAD_EOF));
    assert(state.frame_number == 1U);
    assert(state.frame_offset == 0U);
    assert(state.frame_id == UUSB_UVC_PAYLOAD_FID);
    assert(state.packet_count == 97U);

    length = uusb_uvc_packet_next(&state, packet, sizeof(packet));
    assert(length == UUSB_UVC_PAYLOAD_BYTES);
    assert(packet[1] ==
           (UUSB_UVC_PAYLOAD_EOH | UUSB_UVC_PAYLOAD_FID));
    assert(packet[2] == uusb_uvc_pattern_byte(UUSB_UVC_BARS, 1U, 0U));

    assert(uusb_uvc_packet_state_set_pattern(
        &state, UUSB_UVC_GRADIENT));
    assert(state.pattern == UUSB_UVC_GRADIENT);
    assert(!uusb_uvc_packet_state_set_pattern(
        &state, (uusb_uvc_pattern_t)0xffU));
    assert(state.pattern == UUSB_UVC_GRADIENT);

    state.frame_offset = UUSB_UVC_FRAME_BYTES - 1U;
    state.frame_number = 9U;
    state.frame_id = 0U;
    length = uusb_uvc_packet_next(&state, packet, 3U);
    assert(length == 3U);
    assert(packet[1] == (UUSB_UVC_PAYLOAD_EOH | UUSB_UVC_PAYLOAD_EOF));
    assert(state.frame_offset == 0U);
    assert(state.frame_number == 10U);
    assert(state.frame_id == UUSB_UVC_PAYLOAD_FID);
}

int main(void)
{
    test_patterns();
    test_packet_framing();
    return 0;
}
