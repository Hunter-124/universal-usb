#include "tusb.h"

#include "descriptor_strings.h"

/* TEST ONLY: pid.codes 1209:000f must be replaced by an assigned PID before
 * redistributed, sold, or manufactured hardware is shipped. */
static tusb_desc_device_t const device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = UINT16_C(0x0200),
    .bDeviceClass = 0U,
    .bDeviceSubClass = 0U,
    .bDeviceProtocol = 0U,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = UINT16_C(0x1209),
    .idProduct = UINT16_C(0x000f),
    .bcdDevice = UINT16_C(0x0001),
    .iManufacturer = 1U,
    .iProduct = 2U,
    .iSerialNumber = 3U,
    .bNumConfigurations = 1U,
};

static uint8_t const configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, 0, 0, TUD_CONFIG_DESC_LEN, 0, 100),
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

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t language_id)
{
    return uusb_descriptor_string(
        index, language_id, "Universal USB Webcam Bootstrap");
}
