#include "uusb_uvc_pattern.h"

#define UUSB_UVC_COUNTER_X 4U
#define UUSB_UVC_COUNTER_Y 4U
#define UUSB_UVC_COUNTER_DIGITS 8U
#define UUSB_UVC_COUNTER_SCALE 2U
#define UUSB_UVC_COUNTER_CELL_WIDTH 8U
#define UUSB_UVC_COUNTER_WIDTH \
    (UUSB_UVC_COUNTER_DIGITS * UUSB_UVC_COUNTER_CELL_WIDTH)
#define UUSB_UVC_COUNTER_HEIGHT 10U

typedef struct {
    uint8_t y0;
    uint8_t u;
    uint8_t y1;
    uint8_t v;
} uusb_yuyv_pair_t;

typedef struct {
    uint8_t y;
    uint8_t u;
    uint8_t v;
} uusb_yuv_color_t;

static const uusb_yuv_color_t ebu_bars[8] = {
    {180U, 128U, 128U}, {168U, 44U, 136U},
    {145U, 147U, 44U},  {133U, 63U, 52U},
    {63U, 193U, 204U},  {51U, 109U, 212U},
    {28U, 212U, 120U},  {16U, 128U, 128U},
};

/* Three-bit rows for hexadecimal digits 0 through F. */
static const uint8_t counter_glyphs[16][5] = {
    {7U, 5U, 5U, 5U, 7U}, {2U, 6U, 2U, 2U, 7U},
    {7U, 1U, 7U, 4U, 7U}, {7U, 1U, 7U, 1U, 7U},
    {5U, 5U, 7U, 1U, 1U}, {7U, 4U, 7U, 1U, 7U},
    {7U, 4U, 7U, 5U, 7U}, {7U, 1U, 1U, 1U, 1U},
    {7U, 5U, 7U, 5U, 7U}, {7U, 5U, 7U, 1U, 7U},
    {7U, 5U, 7U, 5U, 5U}, {6U, 5U, 6U, 5U, 6U},
    {7U, 4U, 4U, 4U, 7U}, {6U, 5U, 5U, 5U, 6U},
    {7U, 4U, 7U, 4U, 7U}, {7U, 4U, 7U, 4U, 4U},
};

bool uusb_uvc_pattern_is_valid(uusb_uvc_pattern_t pattern)
{
    return (pattern == UUSB_UVC_BARS) ||
           (pattern == UUSB_UVC_CHECKER) ||
           (pattern == UUSB_UVC_GRADIENT);
}

static bool counter_luma(
    uint16_t x, uint16_t y, uint32_t frame_number, uint8_t *luma)
{
    if ((x < UUSB_UVC_COUNTER_X) ||
        (x >= UUSB_UVC_COUNTER_X + UUSB_UVC_COUNTER_WIDTH) ||
        (y < UUSB_UVC_COUNTER_Y) ||
        (y >= UUSB_UVC_COUNTER_Y + UUSB_UVC_COUNTER_HEIGHT)) {
        return false;
    }

    uint16_t const local_x = (uint16_t)(x - UUSB_UVC_COUNTER_X);
    uint16_t const local_y = (uint16_t)(y - UUSB_UVC_COUNTER_Y);
    uint8_t const digit = (uint8_t)(local_x / UUSB_UVC_COUNTER_CELL_WIDTH);
    uint8_t const glyph_x =
        (uint8_t)((local_x % UUSB_UVC_COUNTER_CELL_WIDTH) /
                  UUSB_UVC_COUNTER_SCALE);
    uint8_t const glyph_y = (uint8_t)(local_y / UUSB_UVC_COUNTER_SCALE);

    *luma = 16U;
    if (glyph_x < 3U) {
        uint8_t const shift = (uint8_t)((7U - digit) * 4U);
        uint8_t const value = (uint8_t)((frame_number >> shift) & 0x0fU);
        uint8_t const mask = (uint8_t)(1U << (2U - glyph_x));
        if ((counter_glyphs[value][glyph_y] & mask) != 0U) {
            *luma = 235U;
        }
    }
    return true;
}

static bool counter_region(uint16_t x, uint16_t y)
{
    return (x >= UUSB_UVC_COUNTER_X) &&
           (x < UUSB_UVC_COUNTER_X + UUSB_UVC_COUNTER_WIDTH) &&
           (y >= UUSB_UVC_COUNTER_Y) &&
           (y < UUSB_UVC_COUNTER_Y + UUSB_UVC_COUNTER_HEIGHT);
}

static uusb_yuv_color_t pattern_color(
    uusb_uvc_pattern_t pattern, uint32_t frame_number,
    uint16_t x, uint16_t y)
{
    uusb_yuv_color_t color;
    switch (pattern) {
    case UUSB_UVC_BARS: {
        uint16_t const moving_x =
            (uint16_t)((x + ((frame_number * 2U) % UUSB_UVC_WIDTH)) %
                       UUSB_UVC_WIDTH);
        color = ebu_bars[moving_x / (UUSB_UVC_WIDTH / 8U)];
        break;
    }
    case UUSB_UVC_CHECKER: {
        uint16_t const moving_x =
            (uint16_t)((x + ((frame_number * 2U) % 24U)) / 12U);
        uint16_t const moving_y =
            (uint16_t)((y + (frame_number % 24U)) / 12U);
        if (((moving_x + moving_y) & 1U) == 0U) {
            color.y = 200U;
            color.u = 72U;
            color.v = 140U;
        } else {
            color.y = 48U;
            color.u = 184U;
            color.v = 116U;
        }
        break;
    }
    case UUSB_UVC_GRADIENT: {
        uint16_t const moving_x =
            (uint16_t)((x + ((frame_number * 3U) % UUSB_UVC_WIDTH)) %
                       UUSB_UVC_WIDTH);
        color.y = (uint8_t)(16U + ((uint32_t)moving_x * 219U) /
                                  (UUSB_UVC_WIDTH - 1U));
        color.u = (uint8_t)(64U + ((uint32_t)y * 128U) /
                                  (UUSB_UVC_HEIGHT - 1U));
        color.v = (uint8_t)(192U - ((uint32_t)y * 128U) /
                                   (UUSB_UVC_HEIGHT - 1U));
        break;
    }
    default:
        color.y = 16U;
        color.u = 128U;
        color.v = 128U;
        break;
    }
    return color;
}

static uusb_yuyv_pair_t pattern_pair(
    uusb_uvc_pattern_t pattern, uint32_t frame_number,
    uint16_t x, uint16_t y)
{
    uusb_yuv_color_t const first =
        pattern_color(pattern, frame_number, x, y);
    uusb_yuv_color_t const second =
        pattern_color(pattern, frame_number, (uint16_t)(x + 1U), y);
    uusb_yuyv_pair_t pair = {
        .y0 = first.y,
        .u = (uint8_t)(((uint16_t)first.u + second.u) / 2U),
        .y1 = second.y,
        .v = (uint8_t)(((uint16_t)first.v + second.v) / 2U),
    };

    uint8_t counter_y;
    if (counter_luma(x, y, frame_number, &counter_y)) {
        pair.y0 = counter_y;
    }
    if (counter_luma((uint16_t)(x + 1U), y, frame_number, &counter_y)) {
        pair.y1 = counter_y;
    }
    if (counter_region(x, y) || counter_region((uint16_t)(x + 1U), y)) {
        pair.u = 128U;
        pair.v = 128U;
    }
    return pair;
}

static uint8_t pair_byte(const uusb_yuyv_pair_t *pair, uint8_t index)
{
    switch (index) {
    case 0U: return pair->y0;
    case 1U: return pair->u;
    case 2U: return pair->y1;
    default: return pair->v;
    }
}

uint8_t uusb_uvc_pattern_byte(
    uusb_uvc_pattern_t pattern, uint32_t frame_number,
    uint32_t frame_offset)
{
    if (frame_offset >= UUSB_UVC_FRAME_BYTES) {
        return 0U;
    }
    uint32_t const pair_index = frame_offset / 4U;
    uint16_t const pairs_per_line = UUSB_UVC_WIDTH / 2U;
    uint16_t const x = (uint16_t)((pair_index % pairs_per_line) * 2U);
    uint16_t const y = (uint16_t)(pair_index / pairs_per_line);
    uusb_yuyv_pair_t const pair = pattern_pair(pattern, frame_number, x, y);
    return pair_byte(&pair, (uint8_t)(frame_offset & 3U));
}

void uusb_uvc_pattern_fill(
    uusb_uvc_pattern_t pattern, uint32_t frame_number,
    uint32_t frame_offset, uint8_t *destination, size_t length)
{
    if (destination == NULL) {
        return;
    }
    size_t output = 0U;
    while ((output < length) && (((frame_offset + output) & 3U) != 0U)) {
        destination[output] = uusb_uvc_pattern_byte(
            pattern, frame_number, frame_offset + (uint32_t)output);
        ++output;
    }
    while ((length - output) >= 4U &&
           (frame_offset + output) < UUSB_UVC_FRAME_BYTES) {
        uint32_t const offset = frame_offset + (uint32_t)output;
        uint32_t const pair_index = offset / 4U;
        uint16_t const pairs_per_line = UUSB_UVC_WIDTH / 2U;
        uint16_t const x =
            (uint16_t)((pair_index % pairs_per_line) * 2U);
        uint16_t const y = (uint16_t)(pair_index / pairs_per_line);
        uusb_yuyv_pair_t const pair =
            pattern_pair(pattern, frame_number, x, y);
        destination[output] = pair.y0;
        destination[output + 1U] = pair.u;
        destination[output + 2U] = pair.y1;
        destination[output + 3U] = pair.v;
        output += 4U;
    }
    while (output < length) {
        destination[output] = uusb_uvc_pattern_byte(
            pattern, frame_number, frame_offset + (uint32_t)output);
        ++output;
    }
}

void uusb_uvc_packet_state_initialize(
    uusb_uvc_packet_state_t *state, uusb_uvc_pattern_t pattern)
{
    if (state == NULL) {
        return;
    }
    state->pattern = uusb_uvc_pattern_is_valid(pattern) ?
                         pattern : UUSB_UVC_BARS;
    state->frame_id = 0U;
    state->frame_number = 0U;
    state->frame_offset = 0U;
    state->packet_count = 0U;
}

bool uusb_uvc_packet_state_set_pattern(
    uusb_uvc_packet_state_t *state, uusb_uvc_pattern_t pattern)
{
    if ((state == NULL) || !uusb_uvc_pattern_is_valid(pattern)) {
        return false;
    }
    state->pattern = pattern;
    return true;
}

size_t uusb_uvc_packet_next(
    uusb_uvc_packet_state_t *state, uint8_t *packet,
    size_t packet_capacity)
{
    if ((state == NULL) || (packet == NULL) ||
        (packet_capacity <= UUSB_UVC_PAYLOAD_HEADER_BYTES)) {
        return 0U;
    }
    if (packet_capacity > UUSB_UVC_PAYLOAD_BYTES) {
        packet_capacity = UUSB_UVC_PAYLOAD_BYTES;
    }

    uint32_t const remaining = UUSB_UVC_FRAME_BYTES - state->frame_offset;
    size_t data_length = packet_capacity - UUSB_UVC_PAYLOAD_HEADER_BYTES;
    if (data_length > remaining) {
        data_length = (size_t)remaining;
    }
    bool const end_of_frame = data_length == remaining;
    packet[0] = UUSB_UVC_PAYLOAD_HEADER_BYTES;
    packet[1] = (uint8_t)(UUSB_UVC_PAYLOAD_EOH |
                          (state->frame_id & UUSB_UVC_PAYLOAD_FID) |
                          (end_of_frame ? UUSB_UVC_PAYLOAD_EOF : 0U));
    uusb_uvc_pattern_fill(
        state->pattern, state->frame_number, state->frame_offset,
        packet + UUSB_UVC_PAYLOAD_HEADER_BYTES, data_length);

    state->frame_offset += (uint32_t)data_length;
    ++state->packet_count;
    if (end_of_frame) {
        state->frame_offset = 0U;
        ++state->frame_number;
        state->frame_id ^= UUSB_UVC_PAYLOAD_FID;
    }
    return data_length + UUSB_UVC_PAYLOAD_HEADER_BYTES;
}
