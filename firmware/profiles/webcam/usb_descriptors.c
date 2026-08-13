#include "tusb.h"

#include "descriptor_strings.h"
#include "uusb_uvc_pattern.h"

#define UUSB_TEST_VID UINT16_C(0x1209)
#define UUSB_TEST_PID_WEBCAM UINT16_C(0x000f)
#define UUSB_UVC_CLOCK_HZ UINT32_C(27000000)
#define UUSB_UVC_INPUT_TERMINAL 1U
#define UUSB_UVC_OUTPUT_TERMINAL 2U
#define UUSB_UVC_ENDPOINT_IN UINT8_C(0x81)
#define UUSB_UVC_BIT_RATE \
    (UUSB_UVC_WIDTH * UUSB_UVC_HEIGHT * 16UL * 10UL)

/* TEST ONLY: pid.codes 1209:000f must be replaced by an assigned PID before
 * redistributed, sold, or manufactured hardware is shipped. */
const char uusb_webcam_distribution_warning[] =
    "PRIVATE-TEST VID:PID 1209:000f; replace before distribution, sale, or manufacture";

#define UUSB_EP0_PMA_BYTES 128U
#define UUSB_BTAB_PMA_BYTES 64U
#define UUSB_VIDEO_PMA_BYTES UUSB_UVC_PAYLOAD_BYTES
#define UUSB_WEBCAM_PMA_BYTES \
    (UUSB_BTAB_PMA_BYTES + UUSB_EP0_PMA_BYTES + UUSB_VIDEO_PMA_BYTES)
#define UUSB_STM32F103_PMA_BYTES 512U
_Static_assert(UUSB_WEBCAM_PMA_BYTES == 448U,
               "webcam PMA accounting changed");
_Static_assert(UUSB_WEBCAM_PMA_BYTES <= UUSB_STM32F103_PMA_BYTES,
               "webcam exceeds STM32F103 packet memory");
_Static_assert(UUSB_UVC_FRAME_BYTES ==
                   UUSB_UVC_WIDTH * UUSB_UVC_HEIGHT * 2UL,
               "YUY2 frame geometry changed");

enum {
    ITF_NUM_VIDEO_CONTROL = 0,
    ITF_NUM_VIDEO_STREAMING,
    ITF_NUM_TOTAL
};

typedef struct TU_ATTR_PACKED {
    tusb_desc_interface_t interface;
    tusb_desc_video_control_header_1itf_t header;
    tusb_desc_video_control_camera_terminal_t camera_terminal;
    tusb_desc_video_control_output_terminal_t output_terminal;
} uusb_uvc_control_descriptor_t;

typedef struct TU_ATTR_PACKED {
    tusb_desc_interface_t interface;
    tusb_desc_video_streaming_input_header_1byte_t header;
    tusb_desc_video_format_uncompressed_t format;
    tusb_desc_video_frame_uncompressed_1int_t frame;
    tusb_desc_video_streaming_color_matching_t color;
    tusb_desc_interface_t alternate;
    tusb_desc_endpoint_t endpoint;
} uusb_uvc_streaming_descriptor_t;

typedef struct TU_ATTR_PACKED {
    tusb_desc_configuration_t configuration;
    tusb_desc_interface_assoc_t association;
    uusb_uvc_control_descriptor_t control;
    uusb_uvc_streaming_descriptor_t streaming;
} uusb_uvc_configuration_descriptor_t;

static tusb_desc_device_t const device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = UINT16_C(0x0200),
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = UUSB_TEST_VID,
    .idProduct = UUSB_TEST_PID_WEBCAM,
    .bcdDevice = UINT16_C(0x0001),
    .iManufacturer = 1U,
    .iProduct = 2U,
    .iSerialNumber = 3U,
    .bNumConfigurations = 1U,
};

static uusb_uvc_configuration_descriptor_t const configuration_descriptor = {
    .configuration = {
        .bLength = sizeof(tusb_desc_configuration_t),
        .bDescriptorType = TUSB_DESC_CONFIGURATION,
        .wTotalLength = sizeof(uusb_uvc_configuration_descriptor_t),
        .bNumInterfaces = ITF_NUM_TOTAL,
        .bConfigurationValue = 1U,
        .iConfiguration = 0U,
        .bmAttributes = TU_BIT(7),
        .bMaxPower = 100U / 2U,
    },
    .association = {
        .bLength = sizeof(tusb_desc_interface_assoc_t),
        .bDescriptorType = TUSB_DESC_INTERFACE_ASSOCIATION,
        .bFirstInterface = ITF_NUM_VIDEO_CONTROL,
        .bInterfaceCount = 2U,
        .bFunctionClass = TUSB_CLASS_VIDEO,
        .bFunctionSubClass = VIDEO_SUBCLASS_INTERFACE_COLLECTION,
        .bFunctionProtocol = VIDEO_ITF_PROTOCOL_UNDEFINED,
        .iFunction = 0U,
    },
    .control = {
        .interface = {
            .bLength = sizeof(tusb_desc_interface_t),
            .bDescriptorType = TUSB_DESC_INTERFACE,
            .bInterfaceNumber = ITF_NUM_VIDEO_CONTROL,
            .bAlternateSetting = 0U,
            .bNumEndpoints = 0U,
            .bInterfaceClass = TUSB_CLASS_VIDEO,
            .bInterfaceSubClass = VIDEO_SUBCLASS_CONTROL,
            .bInterfaceProtocol = VIDEO_ITF_PROTOCOL_15,
            .iInterface = 0U,
        },
        .header = {
            .bLength = sizeof(tusb_desc_video_control_header_1itf_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VC_HEADER,
            .bcdUVC = VIDEO_BCD_1_50,
            .wTotalLength = sizeof(uusb_uvc_control_descriptor_t) -
                            sizeof(tusb_desc_interface_t),
            .dwClockFrequency = UUSB_UVC_CLOCK_HZ,
            .bInCollection = 1U,
            .baInterfaceNr = {ITF_NUM_VIDEO_STREAMING},
        },
        .camera_terminal = {
            .bLength = sizeof(tusb_desc_video_control_camera_terminal_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VC_INPUT_TERMINAL,
            .bTerminalID = UUSB_UVC_INPUT_TERMINAL,
            .wTerminalType = VIDEO_ITT_CAMERA,
            .bAssocTerminal = 0U,
            .iTerminal = 0U,
            .wObjectiveFocalLengthMin = 0U,
            .wObjectiveFocalLengthMax = 0U,
            .wOcularFocalLength = 0U,
            .bControlSize = 3U,
            .bmControls = {0U, 0U, 0U},
        },
        .output_terminal = {
            .bLength = sizeof(tusb_desc_video_control_output_terminal_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VC_OUTPUT_TERMINAL,
            .bTerminalID = UUSB_UVC_OUTPUT_TERMINAL,
            .wTerminalType = VIDEO_TT_STREAMING,
            .bAssocTerminal = 0U,
            .bSourceID = UUSB_UVC_INPUT_TERMINAL,
            .iTerminal = 0U,
        },
    },
    .streaming = {
        .interface = {
            .bLength = sizeof(tusb_desc_interface_t),
            .bDescriptorType = TUSB_DESC_INTERFACE,
            .bInterfaceNumber = ITF_NUM_VIDEO_STREAMING,
            .bAlternateSetting = 0U,
            .bNumEndpoints = 0U,
            .bInterfaceClass = TUSB_CLASS_VIDEO,
            .bInterfaceSubClass = VIDEO_SUBCLASS_STREAMING,
            .bInterfaceProtocol = VIDEO_ITF_PROTOCOL_15,
            .iInterface = 0U,
        },
        .header = {
            .bLength = sizeof(tusb_desc_video_streaming_input_header_1byte_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VS_INPUT_HEADER,
            .bNumFormats = 1U,
            .wTotalLength = sizeof(uusb_uvc_streaming_descriptor_t) -
                            (2U * sizeof(tusb_desc_interface_t)) -
                            sizeof(tusb_desc_endpoint_t),
            .bEndpointAddress = UUSB_UVC_ENDPOINT_IN,
            .bmInfo = 0U,
            .bTerminalLink = UUSB_UVC_OUTPUT_TERMINAL,
            .bStillCaptureMethod = 0U,
            .bTriggerSupport = 0U,
            .bTriggerUsage = 0U,
            .bControlSize = 1U,
            .bmaControls = {0U},
        },
        .format = {
            .bLength = sizeof(tusb_desc_video_format_uncompressed_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VS_FORMAT_UNCOMPRESSED,
            .bFormatIndex = 1U,
            .bNumFrameDescriptors = 1U,
            .guidFormat = {TUD_VIDEO_GUID_YUY2},
            .bBitsPerPixel = 16U,
            .bDefaultFrameIndex = 1U,
            .bAspectRatioX = 4U,
            .bAspectRatioY = 3U,
            .bmInterlaceFlags = 0U,
            .bCopyProtect = 0U,
        },
        .frame = {
            .bLength = sizeof(tusb_desc_video_frame_uncompressed_1int_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VS_FRAME_UNCOMPRESSED,
            .bFrameIndex = 1U,
            .bmCapabilities = 0U,
            .wWidth = UUSB_UVC_WIDTH,
            .wHeight = UUSB_UVC_HEIGHT,
            .dwMinBitRate = UUSB_UVC_BIT_RATE,
            .dwMaxBitRate = UUSB_UVC_BIT_RATE,
            .dwMaxVideoFrameBufferSize = UUSB_UVC_FRAME_BYTES,
            .dwDefaultFrameInterval = UUSB_UVC_FRAME_INTERVAL_100NS,
            .bFrameIntervalType = 1U,
            .dwFrameInterval = {UUSB_UVC_FRAME_INTERVAL_100NS},
        },
        .color = {
            .bLength = sizeof(tusb_desc_video_streaming_color_matching_t),
            .bDescriptorType = TUSB_DESC_CS_INTERFACE,
            .bDescriptorSubType = VIDEO_CS_ITF_VS_COLORFORMAT,
            .bColorPrimaries = VIDEO_COLOR_PRIMARIES_BT709,
            .bTransferCharacteristics = VIDEO_COLOR_XFER_CH_BT709,
            .bMatrixCoefficients = VIDEO_COLOR_COEF_SMPTE170M,
        },
        .alternate = {
            .bLength = sizeof(tusb_desc_interface_t),
            .bDescriptorType = TUSB_DESC_INTERFACE,
            .bInterfaceNumber = ITF_NUM_VIDEO_STREAMING,
            .bAlternateSetting = 1U,
            .bNumEndpoints = 1U,
            .bInterfaceClass = TUSB_CLASS_VIDEO,
            .bInterfaceSubClass = VIDEO_SUBCLASS_STREAMING,
            .bInterfaceProtocol = VIDEO_ITF_PROTOCOL_15,
            .iInterface = 0U,
        },
        .endpoint = {
            .bLength = sizeof(tusb_desc_endpoint_t),
            .bDescriptorType = TUSB_DESC_ENDPOINT,
            .bEndpointAddress = UUSB_UVC_ENDPOINT_IN,
            .bmAttributes = {
                .xfer = TUSB_XFER_ISOCHRONOUS,
                .sync = 1U,
            },
            .wMaxPacketSize = UUSB_UVC_PAYLOAD_BYTES,
            .bInterval = 1U,
        },
    },
};

const uint8_t *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&device_descriptor;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return (const uint8_t *)&configuration_descriptor;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t language_id)
{
    return uusb_descriptor_string(
        index, language_id, "Universal USB Test Camera");
}
