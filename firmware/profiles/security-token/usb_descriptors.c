#include "tusb.h"

#include <stddef.h>
#include <stdint.h>

#define UUSB_TEST_VID UINT16_C(0x1209)
#define UUSB_TEST_PID_SECURITY_TOKEN UINT16_C(0x0010)

/* TEST ONLY: pid.codes 1209:0010 must be replaced by an assigned PID before
 * redistributed, sold, or manufactured hardware is shipped. */
const char uusb_security_token_distribution_warning[] =
    "PRIVATE-TEST VID:PID 1209:0010; replace before distribution, sale, or manufacture";

#define UUSB_BTABLE_PMA_BYTES 64U
#define UUSB_EP0_DIRECTIONS_PMA_BYTES 128U
#define UUSB_FIDO_PMA_BYTES 128U
#define UUSB_CCID_BULK_PMA_BYTES 128U
#define UUSB_CCID_INTERRUPT_PMA_BYTES 8U
#define UUSB_OTP_PMA_BYTES 8U
#define UUSB_SECURITY_TOKEN_PMA_BYTES                                  \
    (UUSB_BTABLE_PMA_BYTES + UUSB_EP0_DIRECTIONS_PMA_BYTES +           \
     UUSB_FIDO_PMA_BYTES + UUSB_CCID_BULK_PMA_BYTES +                  \
     UUSB_CCID_INTERRUPT_PMA_BYTES + UUSB_OTP_PMA_BYTES)
_Static_assert(UUSB_SECURITY_TOKEN_PMA_BYTES == 464U,
               "security-token PMA accounting changed");

#define UUSB_CCID_FUNCTIONAL_DESC_LEN 54U
#define UUSB_CCID_INTERFACE_DESC_LEN \
    (9U + UUSB_CCID_FUNCTIONAL_DESC_LEN + (3U * 7U))

enum {
    ITF_NUM_FIDO = 0,
    ITF_NUM_CCID,
    ITF_NUM_OTP,
    ITF_NUM_TOTAL
};

enum {
    CONFIG_TOTAL_LEN = TUD_CONFIG_DESC_LEN + TUD_HID_INOUT_DESC_LEN +
                       UUSB_CCID_INTERFACE_DESC_LEN + TUD_HID_DESC_LEN
};

static uint8_t const fido_report_descriptor[] = {
    0x06, 0xd0, 0xf1, /* Usage Page (FIDO Alliance) */
    0x09, 0x01,       /* Usage (U2F Authenticator Device) */
    0xa1, 0x01,       /* Collection (Application) */
      0x09, 0x20,     /* Usage (Input Report Data) */
      0x15, 0x00,     /* Logical Minimum (0) */
      0x26, 0xff, 0x00, /* Logical Maximum (255) */
      0x75, 0x08,     /* Report Size (8) */
      0x95, 0x40,     /* Report Count (64) */
      0x81, 0x02,     /* Input (Data, Variable, Absolute) */
      0x09, 0x21,     /* Usage (Output Report Data) */
      0x15, 0x00,     /* Logical Minimum (0) */
      0x26, 0xff, 0x00, /* Logical Maximum (255) */
      0x75, 0x08,     /* Report Size (8) */
      0x95, 0x40,     /* Report Count (64) */
      0x91, 0x02,     /* Output (Data, Variable, Absolute) */
    0xc0,             /* End Collection */
};

static uint8_t const otp_report_descriptor[] = {
    TUD_HID_REPORT_DESC_KEYBOARD()
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
    .idProduct = UUSB_TEST_PID_SECURITY_TOKEN,
    .bcdDevice = UINT16_C(0x0001),
    .iManufacturer = 1U,
    .iProduct = 2U,
    .iSerialNumber = 3U,
    .bNumConfigurations = 1U,
};

static uint8_t const configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0, 100),

    TUD_HID_INOUT_DESCRIPTOR(ITF_NUM_FIDO, 0, HID_ITF_PROTOCOL_NONE,
                             sizeof(fido_report_descriptor), 0x01, 0x81,
                             64, 5),
    /* CCID 1.10, one slot, T=1, short-APDU exchange level. */
    9, TUSB_DESC_INTERFACE, ITF_NUM_CCID, 0, 3, TUSB_CLASS_SMART_CARD, 0, 0, 0,
    UUSB_CCID_FUNCTIONAL_DESC_LEN, 0x21,
    U16_TO_U8S_LE(0x0110), /* bcdCCID */
    0x00,                  /* bMaxSlotIndex: one slot */
    0x01,                  /* bVoltageSupport: 5 V */
    U32_TO_U8S_LE(0x00000002), /* dwProtocols: T=1 */
    U32_TO_U8S_LE(4000),   /* dwDefaultClock, kHz */
    U32_TO_U8S_LE(4000),   /* dwMaximumClock, kHz */
    0x00,                  /* bNumClockSupported */
    U32_TO_U8S_LE(9600),   /* dwDataRate, bits/s */
    U32_TO_U8S_LE(9600),   /* dwMaxDataRate, bits/s */
    0x00,                  /* bNumDataRatesSupported */
    U32_TO_U8S_LE(254),    /* dwMaxIFSD for T=1 */
    U32_TO_U8S_LE(0),      /* dwSynchProtocols */
    U32_TO_U8S_LE(0),      /* dwMechanical */
    U32_TO_U8S_LE(0x00020000), /* dwFeatures: short APDU exchange */
    U32_TO_U8S_LE(512),    /* dwMaxCCIDMessageLength, including header */
    0x00,                  /* bClassGetResponse */
    0x00,                  /* bClassEnvelope */
    U16_TO_U8S_LE(0),      /* wLcdLayout */
    0x00,                  /* bPINSupport */
    0x01,                  /* bMaxCCIDBusySlots */
    7, TUSB_DESC_ENDPOINT, 0x02, TUSB_XFER_BULK, U16_TO_U8S_LE(64), 0,
    7, TUSB_DESC_ENDPOINT, 0x82, TUSB_XFER_BULK, U16_TO_U8S_LE(64), 0,
    7, TUSB_DESC_ENDPOINT, 0x83, TUSB_XFER_INTERRUPT, U16_TO_U8S_LE(8), 16,

    TUD_HID_DESCRIPTOR(ITF_NUM_OTP, 0, HID_ITF_PROTOCOL_KEYBOARD,
                       sizeof(otp_report_descriptor), 0x84, 8, 10),
};
_Static_assert(sizeof(configuration_descriptor) == CONFIG_TOTAL_LEN,
               "security-token configuration length changed");

const uint8_t *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&device_descriptor;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return configuration_descriptor;
}

const uint8_t *tud_hid_descriptor_report_cb(uint8_t instance)
{
    switch (instance) {
    case 0U: return fido_report_descriptor;
    case 1U: return otp_report_descriptor;
    default: return NULL;
    }
}

#define UUSB_UID_ADDRESS UINT32_C(0x1ffff7e8)
#define UUSB_UID_BYTES 12U
#define UUSB_STRING_CAPACITY 38U

static uint16_t string_descriptor[UUSB_STRING_CAPACITY + 1U];

static size_t ascii_to_utf16(const char *text)
{
    size_t count = 0U;
    while ((text[count] != '\0') && (count < UUSB_STRING_CAPACITY)) {
        string_descriptor[count + 1U] = (uint8_t)text[count];
        ++count;
    }
    return count;
}

static size_t uid_to_utf16(void)
{
    static char const hexadecimal[] = "0123456789ABCDEF";
    volatile uint8_t const *const uid =
        (volatile uint8_t const *)(uintptr_t)UUSB_UID_ADDRESS;
    for (size_t index = 0U; index < UUSB_UID_BYTES; ++index) {
        uint8_t const byte = uid[index];
        string_descriptor[1U + (index * 2U)] =
            (uint16_t)hexadecimal[byte >> 4U];
        string_descriptor[2U + (index * 2U)] =
            (uint16_t)hexadecimal[byte & UINT8_C(0x0f)];
    }
    return UUSB_UID_BYTES * 2U;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t language_id)
{
    (void)language_id;
    size_t count;
    if (index == 0U) {
        string_descriptor[1] = UINT16_C(0x0409);
        count = 1U;
    } else if (index == 1U) {
        count = ascii_to_utf16("Universal USB");
    } else if (index == 2U) {
        count = ascii_to_utf16("Universal USB Synthetic Security Token");
    } else if (index == 3U) {
        count = uid_to_utf16();
    } else {
        return NULL;
    }

    string_descriptor[0] =
        (uint16_t)((TUSB_DESC_STRING << 8U) | (2U + (count * 2U)));
    return string_descriptor;
}
