#include "uusb_tone.h"

#include <stddef.h>

static int16_t const quarter_sine_q15[65] = {
    0,     804,   1608,  2410,  3212,  4011,  4808,  5602,  6393,  7179,
    7962,  8739,  9512,  10278, 11039, 11793, 12539, 13279, 14010, 14732,
    15446, 16151, 16846, 17530, 18204, 18868, 19520, 20160, 20788, 21403,
    22006, 22594, 23170, 23731, 24279, 24811, 25329, 25832, 26319, 26790,
    27245, 27683, 28105, 28510, 28898, 29268, 29621, 29956, 30273, 30572,
    30852, 31113, 31356, 31580, 31785, 31971, 32137, 32285, 32412, 32521,
    32610, 32678, 32728, 32757, 32767,
};

static uint32_t phase_step_for(uint16_t frequency_hz)
{
    return (uint32_t)(((uint64_t)frequency_hz << 32U) /
                      UUSB_TONE_SAMPLE_RATE_HZ);
}

static int16_t sine_q15(uint32_t phase)
{
    uint8_t const position = (uint8_t)(phase >> 24U);
    uint8_t const quadrant = (uint8_t)(position >> 6U);
    uint8_t const offset = (uint8_t)(position & UINT8_C(0x3f));

    switch (quadrant) {
    case 0U:
        return quarter_sine_q15[offset];
    case 1U:
        return quarter_sine_q15[64U - offset];
    case 2U:
        return (int16_t)-quarter_sine_q15[offset];
    default:
        return (int16_t)-quarter_sine_q15[64U - offset];
    }
}

void uusb_tone_initialize(uusb_tone_state_t *state)
{
    state->mode = UUSB_MIC_TONE;
    state->frequency_hz = UUSB_TONE_DEFAULT_FREQUENCY_HZ;
    state->amplitude_q15 = UUSB_TONE_DEFAULT_AMPLITUDE_Q15;
    state->phase = 0U;
    state->phase_step = phase_step_for(state->frequency_hz);
    state->packet_count = 0U;
    state->underrun_count = 0U;
}

bool uusb_tone_config_is_valid(
    uusb_mic_mode_t mode, uint16_t frequency_hz, uint16_t amplitude_q15)
{
    return ((mode == UUSB_MIC_SILENCE) || (mode == UUSB_MIC_TONE)) &&
           (frequency_hz >= UUSB_TONE_MIN_FREQUENCY_HZ) &&
           (frequency_hz <= UUSB_TONE_MAX_FREQUENCY_HZ) &&
           (amplitude_q15 <= UUSB_TONE_MAX_AMPLITUDE_Q15);
}

bool uusb_tone_configure(
    uusb_tone_state_t *state,
    uusb_mic_mode_t mode,
    uint16_t frequency_hz,
    uint16_t amplitude_q15)
{
    if (!uusb_tone_config_is_valid(mode, frequency_hz, amplitude_q15)) {
        return false;
    }

    state->mode = mode;
    state->frequency_hz = frequency_hz;
    state->amplitude_q15 = amplitude_q15;
    state->phase_step = phase_step_for(frequency_hz);
    return true;
}

void uusb_tone_fill_packet(
    uusb_tone_state_t *state,
    uint8_t packet[UUSB_TONE_PACKET_BYTES])
{
    if (state->mode == UUSB_MIC_SILENCE) {
        for (size_t index = 0U; index < UUSB_TONE_PACKET_BYTES; ++index) {
            packet[index] = 0U;
        }
        state->phase += state->phase_step * UUSB_TONE_SAMPLES_PER_PACKET;
        return;
    }

    for (size_t index = 0U; index < UUSB_TONE_SAMPLES_PER_PACKET; ++index) {
        int32_t const product =
            (int32_t)sine_q15(state->phase) * state->amplitude_q15;
        int16_t const sample = (int16_t)(product / INT32_C(32768));
        uint16_t const encoded = (uint16_t)sample;
        packet[index * 2U] = (uint8_t)encoded;
        packet[(index * 2U) + 1U] = (uint8_t)(encoded >> 8U);
        state->phase += state->phase_step;
    }
}

void uusb_tone_note_packet_sent(uusb_tone_state_t *state)
{
    state->packet_count++;
}

void uusb_tone_note_underrun(uusb_tone_state_t *state)
{
    state->underrun_count++;
}
