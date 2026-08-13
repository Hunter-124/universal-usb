#ifndef UUSB_TONE_H
#define UUSB_TONE_H

#include <stdbool.h>
#include <stdint.h>

#include "uusb_mailbox.h"

#define UUSB_TONE_SAMPLE_RATE_HZ UINT32_C(48000)
#define UUSB_TONE_SAMPLES_PER_PACKET 48U
#define UUSB_TONE_PACKET_BYTES (UUSB_TONE_SAMPLES_PER_PACKET * 2U)
#define UUSB_TONE_DEFAULT_FREQUENCY_HZ UINT16_C(1000)
#define UUSB_TONE_DEFAULT_AMPLITUDE_Q15 UINT16_C(8231)
#define UUSB_TONE_MIN_FREQUENCY_HZ UINT16_C(20)
#define UUSB_TONE_MAX_FREQUENCY_HZ UINT16_C(20000)
#define UUSB_TONE_MAX_AMPLITUDE_Q15 UINT16_C(32767)

typedef struct {
    uusb_mic_mode_t mode;
    uint16_t frequency_hz;
    uint16_t amplitude_q15;
    uint32_t phase;
    uint32_t phase_step;
    uint32_t packet_count;
    uint32_t underrun_count;
} uusb_tone_state_t;

void uusb_tone_initialize(uusb_tone_state_t *state);
bool uusb_tone_config_is_valid(
    uusb_mic_mode_t mode, uint16_t frequency_hz, uint16_t amplitude_q15);
bool uusb_tone_configure(
    uusb_tone_state_t *state,
    uusb_mic_mode_t mode,
    uint16_t frequency_hz,
    uint16_t amplitude_q15);
void uusb_tone_fill_packet(
    uusb_tone_state_t *state,
    uint8_t packet[UUSB_TONE_PACKET_BYTES]);
void uusb_tone_note_packet_sent(uusb_tone_state_t *state);
void uusb_tone_note_underrun(uusb_tone_state_t *state);

#endif
