#include "tusb.h"

#include "descriptor_strings.h"

#define UUSB_TEST_VID UINT16_C(0x1209)
#define UUSB_TEST_PID_HID_MSC UINT16_C(0x000d)

/* TEST ONLY: pid.codes 1209:000d must be replaced by an assigned PID before
 * redistributed, sold, or manufactured hardware is shipped. */
const char uusb_hid_msc_distribution_warning[] =
    "PRIVATE-TEST VID:PID 1209:000d; replace before distribution, sale, or manufacture";

#define UUSB_EP0_PMA_BYTES 128U
#define UUSB_BTAB_PMA_BYTES 64U
#define UUSB_KEYBOARD_PMA_BYTES 8U
#define UUSB_MOUSE_PMA_BYTES 8U
#define UUSB_CONSUMER_PMA_BYTES 2U
#define UUSB_MSC_PMA_BYTES 128U
#define UUSB_HID_MSC_PMA_BYTES                                             \
    (UUSB_BTAB_PMA_BYTES + UUSB_EP0_PMA_BYTES + UUSB_KEYBOARD_PMA_BYTES + \
     UUSB_MOUSE_PMA_BYTES + UUSB_CONSUMER_PMA_BYTES + UUSB_MSC_PMA_BYTES)
#define UUSB_STM32F103_PMA_BYTES 512U
_Static_assert(UUSB_HID_MSC_PMA_BYTES == 338U,
               "HID/MSC PMA accounting changed");
_Static_assert(UUSB_HID_MSC_PMA_BYTES <= UUSB_STM32F103_PMA_BYTES,
               "HID/MSC exceeds STM32F103 packet memory");

enum {
    ITF_NUM_KEYBOARD = 0,
    ITF_NUM_MOUSE,
    ITF_NUM_CONSUMER,
    ITF_NUM_MSC,
    ITF_NUM_TOTAL
};

enum {
    CONFIG_TOTAL_LEN = TUD_CONFIG_DESC_LEN + (3 * TUD_HID_DESC_LEN) +
                       TUD_MSC_DESC_LEN
};

static uint8_t const keyboard_report_descriptor[] = {
    TUD_HID_REPORT_DESC_KEYBOARD()
};

/* Three buttons and X/Y/wheel: exactly four report bytes. */
static uint8_t const mouse_report_descriptor[] = {
    HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),
    HID_USAGE(HID_USAGE_DESKTOP_MOUSE),
    HID_COLLECTION(HID_COLLECTION_APPLICATION),
      HID_USAGE(HID_USAGE_DESKTOP_POINTER),
      HID_COLLECTION(HID_COLLECTION_PHYSICAL),
        HID_USAGE_PAGE(HID_USAGE_PAGE_BUTTON),
        HID_USAGE_MIN(1),
        HID_USAGE_MAX(3),
        HID_LOGICAL_MIN(0),
        HID_LOGICAL_MAX(1),
        HID_REPORT_COUNT(3),
        HID_REPORT_SIZE(1),
        HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),
        HID_REPORT_COUNT(1),
        HID_REPORT_SIZE(5),
        HID_INPUT(HID_CONSTANT),
        HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),
        HID_USAGE(HID_USAGE_DESKTOP_X),
        HID_USAGE(HID_USAGE_DESKTOP_Y),
        HID_USAGE(HID_USAGE_DESKTOP_WHEEL),
        HID_LOGICAL_MIN(0x81),
        HID_LOGICAL_MAX(0x7f),
        HID_REPORT_SIZE(8),
        HID_REPORT_COUNT(3),
        HID_INPUT(HID_DATA | HID_VARIABLE | HID_RELATIVE),
      HID_COLLECTION_END,
    HID_COLLECTION_END,
};

static uint8_t const consumer_report_descriptor[] = {
    TUD_HID_REPORT_DESC_CONSUMER()
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
    .idProduct = UUSB_TEST_PID_HID_MSC,
    .bcdDevice = UINT16_C(0x0001),
    .iManufacturer = 1U,
    .iProduct = 2U,
    .iSerialNumber = 3U,
    .bNumConfigurations = 1U,
};

static uint8_t const configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0, 100),
    TUD_HID_DESCRIPTOR(ITF_NUM_KEYBOARD, 0, HID_ITF_PROTOCOL_KEYBOARD,
                       sizeof(keyboard_report_descriptor), 0x81, 8, 10),
    TUD_HID_DESCRIPTOR(ITF_NUM_MOUSE, 0, HID_ITF_PROTOCOL_MOUSE,
                       sizeof(mouse_report_descriptor), 0x82, 4, 5),
    TUD_HID_DESCRIPTOR(ITF_NUM_CONSUMER, 0, HID_ITF_PROTOCOL_NONE,
                       sizeof(consumer_report_descriptor), 0x83, 2, 10),
    TUD_MSC_DESCRIPTOR(ITF_NUM_MSC, 0, 0x04, 0x84, 64),
};

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
    case 0U: return keyboard_report_descriptor;
    case 1U: return mouse_report_descriptor;
    case 2U: return consumer_report_descriptor;
    default: return NULL;
    }
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t language_id)
{
    return uusb_descriptor_string(
        index, language_id, "Universal USB HID + Disk");
}
