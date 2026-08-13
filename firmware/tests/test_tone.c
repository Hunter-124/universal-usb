#include <assert.h>
#include <stddef.h>
#include <stdint.h>

#include "uusb_tone.h"

static int16_t sample_at(const uint8_t *packet, size_t index)
{
    uint16_t const encoded =
        (uint16_t)packet[index * 2U] |
        (uint16_t)((uint16_t)packet[(index * 2U) + 1U] << 8U);
    if (encoded <= (uint16_t)INT16_MAX) {
        return (int16_t)encoded;
    }
    return (int16_t)(-1 - (int32_t)(UINT16_MAX - encoded));
}

int main(void)
{
    uusb_tone_state_t state;
    uusb_tone_initialize(&state);
    assert(state.mode == UUSB_MIC_TONE);
    assert(state.frequency_hz == UUSB_TONE_DEFAULT_FREQUENCY_HZ);
    assert(state.frequency_hz == 1000U);
    assert(state.amplitude_q15 == UUSB_TONE_DEFAULT_AMPLITUDE_Q15);
    assert(state.amplitude_q15 == 8231U);
    assert(state.phase == 0U);
    assert(state.packet_count == 0U);
    assert(state.underrun_count == 0U);

    assert(!uusb_tone_config_is_valid(
        (uusb_mic_mode_t)2, 1000U, 8231U));
    assert(!uusb_tone_config_is_valid(UUSB_MIC_TONE, 19U, 8231U));
    assert(uusb_tone_config_is_valid(UUSB_MIC_TONE, 20U, 0U));
    assert(uusb_tone_config_is_valid(
        UUSB_MIC_SILENCE, 20000U, 32767U));
    assert(!uusb_tone_config_is_valid(UUSB_MIC_TONE, 20001U, 8231U));
    assert(!uusb_tone_config_is_valid(UUSB_MIC_TONE, 1000U, 32768U));

    struct {
        uint32_t before;
        uint8_t bytes[UUSB_TONE_PACKET_BYTES];
        uint32_t after;
    } packet = {
        .before = UINT32_C(0x12345678),
        .after = UINT32_C(0x89abcdef),
    };
    assert(sizeof(packet.bytes) == 96U);
    uusb_tone_fill_packet(&state, packet.bytes);
    assert(packet.before == UINT32_C(0x12345678));
    assert(packet.after == UINT32_C(0x89abcdef));
    assert(packet.bytes[0] == 0U && packet.bytes[1] == 0U);
    assert(sample_at(packet.bytes, 12U) > 8000);
    assert(sample_at(packet.bytes, 36U) < -8000);
    for (size_t index = 0U; index < UUSB_TONE_SAMPLES_PER_PACKET; ++index) {
        int16_t const sample = sample_at(packet.bytes, index);
        assert(sample <= (int16_t)UUSB_TONE_DEFAULT_AMPLITUDE_Q15);
        assert(sample >= -(int16_t)UUSB_TONE_DEFAULT_AMPLITUDE_Q15);
        assert(packet.bytes[index * 2U] == (uint8_t)(uint16_t)sample);
        assert(packet.bytes[(index * 2U) + 1U] ==
               (uint8_t)((uint16_t)sample >> 8U));
    }

    uint32_t const phase_after_tone = state.phase;
    assert(uusb_tone_configure(&state, UUSB_MIC_SILENCE, 997U, 1000U));
    assert(state.phase == phase_after_tone);
    uint32_t const silence_step = state.phase_step;
    for (size_t index = 0U; index < sizeof(packet.bytes); ++index) {
        packet.bytes[index] = UINT8_C(0xa5);
    }
    uusb_tone_fill_packet(&state, packet.bytes);
    for (size_t index = 0U; index < sizeof(packet.bytes); ++index) {
        assert(packet.bytes[index] == 0U);
    }
    assert(state.phase ==
           phase_after_tone + (silence_step * UUSB_TONE_SAMPLES_PER_PACKET));

    uint32_t const phase_after_silence = state.phase;
    assert(uusb_tone_configure(&state, UUSB_MIC_TONE, 20000U, 32767U));
    assert(state.phase == phase_after_silence);
    assert(!uusb_tone_configure(&state, UUSB_MIC_TONE, 20001U, 1U));
    assert(state.frequency_hz == 20000U);
    assert(state.amplitude_q15 == 32767U);
    assert(state.phase == phase_after_silence);

    uusb_tone_note_packet_sent(&state);
    uusb_tone_note_packet_sent(&state);
    uusb_tone_note_underrun(&state);
    assert(state.packet_count == 2U);
    assert(state.underrun_count == 1U);
    return 0;
}
