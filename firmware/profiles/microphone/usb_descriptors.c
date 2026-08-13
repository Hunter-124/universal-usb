#include "tusb.h"

#include "descriptor_strings.h"

#define UUSB_TEST_VID UINT16_C(0x1209)
#define UUSB_TEST_PID_MICROPHONE UINT16_C(0x000e)

/* TEST ONLY: pid.codes 1209:000e must be replaced by an assigned PID before
 * redistributed, sold, or manufactured hardware is shipped. */
const char uusb_microphone_distribution_warning[] =
    "PRIVATE-TEST VID:PID 1209:000e; replace before distribution, sale, or manufacture";

#define UUSB_EP0_PMA_BYTES 128U
#define UUSB_BTAB_PMA_BYTES 64U
#define UUSB_AUDIO_IN_PMA_BYTES 96U
#define UUSB_MICROPHONE_PMA_BYTES \
    (UUSB_BTAB_PMA_BYTES + UUSB_EP0_PMA_BYTES + UUSB_AUDIO_IN_PMA_BYTES)
#define UUSB_STM32F103_PMA_BYTES 512U
_Static_assert(UUSB_MICROPHONE_PMA_BYTES == 288U,
               "microphone PMA accounting changed");
_Static_assert(UUSB_MICROPHONE_PMA_BYTES <= UUSB_STM32F103_PMA_BYTES,
               "microphone exceeds STM32F103 packet memory");

enum {
    ITF_NUM_AUDIO_CONTROL = 0,
    ITF_NUM_AUDIO_STREAMING,
    ITF_NUM_TOTAL
};

enum {
    AUDIO_CONTROL_CLASS_LEN = TUD_AUDIO10_DESC_CS_AC_LEN(1) +
                              TUD_AUDIO10_DESC_INPUT_TERM_LEN +
                              TUD_AUDIO10_DESC_OUTPUT_TERM_LEN,
    CONFIG_TOTAL_LEN = TUD_CONFIG_DESC_LEN +
                       TUD_AUDIO10_DESC_STD_AC_LEN +
                       AUDIO_CONTROL_CLASS_LEN +
                       (2 * TUD_AUDIO10_DESC_STD_AS_LEN) +
                       TUD_AUDIO10_DESC_CS_AS_INT_LEN +
                       TUD_AUDIO10_DESC_TYPE_I_FORMAT_LEN(1) +
                       TUD_AUDIO10_DESC_STD_AS_ISO_EP_LEN +
                       TUD_AUDIO10_DESC_CS_AS_ISO_EP_LEN
};

static tusb_desc_device_t const device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = UINT16_C(0x0200),
    .bDeviceClass = 0U,
    .bDeviceSubClass = 0U,
    .bDeviceProtocol = 0U,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = UUSB_TEST_VID,
    .idProduct = UUSB_TEST_PID_MICROPHONE,
    .bcdDevice = UINT16_C(0x0001),
    .iManufacturer = 1U,
    .iProduct = 2U,
    .iSerialNumber = 3U,
    .bNumConfigurations = 1U,
};

static uint8_t const configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0, 100),

    /* AudioControl 1.0: a mono microphone terminal feeds USB streaming. */
    TUD_AUDIO10_DESC_STD_AC(ITF_NUM_AUDIO_CONTROL, 0, 0),
    TUD_AUDIO10_DESC_CS_AC(
        0x0100,
        TUD_AUDIO10_DESC_INPUT_TERM_LEN + TUD_AUDIO10_DESC_OUTPUT_TERM_LEN,
        ITF_NUM_AUDIO_STREAMING),
    TUD_AUDIO10_DESC_INPUT_TERM(
        1, AUDIO_TERM_TYPE_IN_GENERIC_MIC, 0, 1,
        AUDIO10_CHANNEL_CONFIG_NON_PREDEFINED, 0, 0),
    TUD_AUDIO10_DESC_OUTPUT_TERM(
        2, AUDIO_TERM_TYPE_USB_STREAMING, 0, 1, 0),

    /* Alternate zero consumes no bandwidth. */
    TUD_AUDIO10_DESC_STD_AS_INT(ITF_NUM_AUDIO_STREAMING, 0, 0, 0),

    /* Alternate one is mono PCM Type-I, signed 16-bit at exactly 48 kHz. */
    TUD_AUDIO10_DESC_STD_AS_INT(ITF_NUM_AUDIO_STREAMING, 1, 1, 0),
    TUD_AUDIO10_DESC_CS_AS_INT(2, 1, AUDIO10_DATA_FORMAT_TYPE_I_PCM),
    TUD_AUDIO10_DESC_TYPE_I_FORMAT(1, 2, 16, 48000),
    TUD_AUDIO10_DESC_STD_AS_ISO_EP(
        0x81,
        (uint8_t)(TUSB_XFER_ISOCHRONOUS | TUSB_ISO_EP_ATT_ASYNCHRONOUS),
        96, 1, 0),
    TUD_AUDIO10_DESC_CS_AS_ISO_EP(
        0, AUDIO10_CS_AS_ISO_DATA_EP_LOCK_DELAY_UNIT_UNDEFINED, 0),
};

_Static_assert(sizeof(configuration_descriptor) == CONFIG_TOTAL_LEN,
               "UAC1 microphone descriptor length changed");

const uint8_t *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&device_descriptor;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return configuration_descriptor;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t language_id)
{
    return uusb_descriptor_string(
        index, language_id, "Universal USB Test Microphone");
}
