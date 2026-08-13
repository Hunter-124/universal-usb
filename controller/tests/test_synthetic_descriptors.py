from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[2]
PROFILES = ROOT / "firmware" / "profiles"


class MicrophoneDescriptorGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (PROFILES / "microphone" / "usb_descriptors.c").read_text(
            encoding="utf-8"
        )
        cls.config = (PROFILES / "microphone" / "tusb_config.h").read_text(
            encoding="utf-8"
        )

    def test_private_identity_and_uac1_topology(self) -> None:
        self.assertRegex(
            self.source, r"UUSB_TEST_PID_MICROPHONE\s+UINT16_C\(0x000e\)"
        )
        self.assertIn('"Universal USB Test Microphone"', self.source)
        self.assertRegex(
            self.source,
            r"(?s)TUD_AUDIO10_DESC_CS_AC\(\s*0x0100,.*?"
            r"TUD_AUDIO10_DESC_INPUT_TERM\(\s*1,\s*AUDIO_TERM_TYPE_IN_GENERIC_MIC"
            r".*?TUD_AUDIO10_DESC_OUTPUT_TERM\(\s*2,\s*AUDIO_TERM_TYPE_USB_STREAMING",
        )

    def test_exact_pcm_format_and_endpoint(self) -> None:
        self.assertRegex(
            self.source,
            r"(?s)TUD_AUDIO10_DESC_STD_AS_INT\(ITF_NUM_AUDIO_STREAMING,\s*0,\s*0,\s*0\)"
            r".*?TUD_AUDIO10_DESC_STD_AS_INT\(ITF_NUM_AUDIO_STREAMING,\s*1,\s*1,\s*0\)"
            r".*?TUD_AUDIO10_DESC_CS_AS_INT\(2,\s*1,\s*AUDIO10_DATA_FORMAT_TYPE_I_PCM\)"
            r".*?TUD_AUDIO10_DESC_TYPE_I_FORMAT\(1,\s*2,\s*16,\s*48000\)"
            r".*?TUD_AUDIO10_DESC_STD_AS_ISO_EP\(\s*0x81,.*?96,\s*1,\s*0\)",
        )
        self.assertRegex(self.config, r"#define CFG_TUD_AUDIO\s+1")
        self.assertRegex(self.config, r"#define CFG_TUD_AUDIO_FUNC_1_EP_IN_SZ_MAX\s+96")

    def test_pma_budget_is_288_bytes(self) -> None:
        self.assertIn("_Static_assert(UUSB_MICROPHONE_PMA_BYTES == 288U", self.source)


class WebcamDescriptorGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (PROFILES / "webcam" / "usb_descriptors.c").read_text(
            encoding="utf-8"
        )
        cls.config = (PROFILES / "webcam" / "tusb_config.h").read_text(
            encoding="utf-8"
        )

    def test_identity_uvc_version_and_single_format(self) -> None:
        self.assertRegex(self.source, r"UUSB_TEST_PID_WEBCAM\s+UINT16_C\(0x000f\)")
        self.assertIn('"Universal USB Test Camera"', self.source)
        self.assertIn(".bcdUVC = VIDEO_BCD_1_50", self.source)
        self.assertIn(".bNumFormats = 1U", self.source)
        self.assertIn(".bNumFrameDescriptors = 1U", self.source)
        self.assertIn(".guidFormat = {TUD_VIDEO_GUID_YUY2}", self.source)

    def test_exact_geometry_interval_and_iso_endpoint(self) -> None:
        self.assertIn("#define UUSB_UVC_WIDTH UINT16_C(128)",
                      (ROOT / "firmware/include/uusb_uvc_pattern.h").read_text())
        self.assertIn("#define UUSB_UVC_HEIGHT UINT16_C(96)",
                      (ROOT / "firmware/include/uusb_uvc_pattern.h").read_text())
        self.assertIn("#define UUSB_UVC_FRAME_INTERVAL_100NS UINT32_C(1000000)",
                      (ROOT / "firmware/include/uusb_uvc_pattern.h").read_text())
        self.assertIn(".bEndpointAddress = UUSB_UVC_ENDPOINT_IN", self.source)
        self.assertIn(".xfer = TUSB_XFER_ISOCHRONOUS", self.source)
        self.assertIn(".wMaxPacketSize = UUSB_UVC_PAYLOAD_BYTES", self.source)
        self.assertIn(".bInterval = 1U", self.source)
        self.assertRegex(self.config, r"#define CFG_TUD_VIDEO\s+1")
        self.assertRegex(self.config, r"#define CFG_TUD_VIDEO_STREAMING_EP_BUFSIZE\s+256")

    def test_pma_budget_is_448_bytes(self) -> None:
        self.assertIn("_Static_assert(UUSB_WEBCAM_PMA_BYTES == 448U", self.source)


class SecurityTokenDescriptorGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (PROFILES / "security-token" / "usb_descriptors.c").read_text(
            encoding="utf-8"
        )
        cls.config = (PROFILES / "security-token" / "tusb_config.h").read_text(
            encoding="utf-8"
        )

    def test_generic_private_identity(self) -> None:
        self.assertRegex(self.source, r"UUSB_TEST_VID\s+UINT16_C\(0x1209\)")
        self.assertRegex(
            self.source, r"UUSB_TEST_PID_SECURITY_TOKEN\s+UINT16_C\(0x0010\)"
        )
        self.assertIn('"Universal USB Synthetic Security Token"', self.source)
        self.assertIn("replace before distribution, sale, or manufacture", self.source)
        self.assertNotRegex(self.source.lower(), r"yubico|yubikey")

    def test_fido_ccid_and_otp_interfaces(self) -> None:
        self.assertIn("0x06, 0xd0, 0xf1", self.source)
        self.assertIn("0x95, 0x40", self.source)
        self.assertRegex(
            self.source,
            r"(?s)TUD_HID_INOUT_DESCRIPTOR\(ITF_NUM_FIDO,\s*0,\s*"
            r"HID_ITF_PROTOCOL_NONE,.*?0x01,\s*0x81,\s*64,\s*5\)",
        )
        self.assertRegex(
            self.source,
            r"(?s)TUSB_CLASS_SMART_CARD,\s*0,\s*0,\s*0,.*?"
            r"0x02,\s*TUSB_XFER_BULK.*?0x82,\s*TUSB_XFER_BULK.*?"
            r"0x83,\s*TUSB_XFER_INTERRUPT",
        )
        self.assertRegex(
            self.source,
            r"(?s)TUD_HID_DESCRIPTOR\(ITF_NUM_OTP,\s*0,\s*"
            r"HID_ITF_PROTOCOL_KEYBOARD,.*?0x84,\s*8,\s*10\)",
        )
        self.assertRegex(self.config, r"#define CFG_TUD_HID\s+2")
        self.assertRegex(self.config, r"#define CFG_TUD_ENDPOINT0_SIZE\s+64")

    def test_exact_464_byte_pma_budget(self) -> None:
        values = {
            name: int(value)
            for name, value in re.findall(
                r"#define UUSB_(BTABLE|EP0_DIRECTIONS|FIDO|CCID_BULK|"
                r"CCID_INTERRUPT|OTP)_PMA_BYTES\s+(\d+)U",
                self.source,
            )
        }
        self.assertEqual(
            values,
            {
                "BTABLE": 64,
                "EP0_DIRECTIONS": 128,
                "FIDO": 128,
                "CCID_BULK": 128,
                "CCID_INTERRUPT": 8,
                "OTP": 8,
            },
        )
        self.assertEqual(sum(values.values()), 464)
        self.assertIn(
            "_Static_assert(UUSB_SECURITY_TOKEN_PMA_BYTES == 464U", self.source
        )


if __name__ == "__main__":
    unittest.main()
