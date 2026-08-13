import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[2]
DESCRIPTORS = ROOT / "firmware/profiles/hid-msc/usb_descriptors.c"
CONFIG = ROOT / "firmware/profiles/hid-msc/tusb_config.h"


class HidMscDescriptorGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DESCRIPTORS.read_text(encoding="utf-8")
        cls.config = CONFIG.read_text(encoding="utf-8")

    def test_private_identity_and_distribution_warning(self):
        self.assertRegex(cls_source := self.source, r"UUSB_TEST_VID\s+UINT16_C\(0x1209\)")
        self.assertRegex(cls_source, r"UUSB_TEST_PID_HID_MSC\s+UINT16_C\(0x000d\)")
        self.assertIn("uusb_hid_msc_distribution_warning", cls_source)
        self.assertIn("replace before distribution, sale, or manufacture", cls_source)
        self.assertIn('"Universal USB HID + Disk"', cls_source)

    def test_device_is_interface_classed_composite(self):
        for field in ("bDeviceClass", "bDeviceSubClass", "bDeviceProtocol"):
            self.assertRegex(self.source, rf"\.{field}\s*=\s*0U")
        self.assertRegex(self.config, r"#define CFG_TUD_ENDPOINT0_SIZE\s+64")

    def test_exact_four_interfaces_and_endpoints(self):
        hid = re.findall(
            r"TUD_HID_DESCRIPTOR\((ITF_NUM_\w+),\s*0,\s*"
            r"(HID_ITF_PROTOCOL_\w+),\s*[^,]+,\s*"
            r"(0x[0-9a-f]+),\s*(\d+),\s*(\d+)\)",
            self.source,
        )
        self.assertEqual(
            hid,
            [
                ("ITF_NUM_KEYBOARD", "HID_ITF_PROTOCOL_KEYBOARD", "0x81", "8", "10"),
                ("ITF_NUM_MOUSE", "HID_ITF_PROTOCOL_MOUSE", "0x82", "4", "5"),
                ("ITF_NUM_CONSUMER", "HID_ITF_PROTOCOL_NONE", "0x83", "2", "10"),
            ],
        )
        self.assertRegex(
            self.source,
            r"TUD_MSC_DESCRIPTOR\(ITF_NUM_MSC,\s*0,\s*0x04,\s*0x84,\s*64\)",
        )
        self.assertIn("MSC_SUBCLASS_SCSI", (ROOT / "third_party/tinyusb/src/device/usbd.h").read_text())

    def test_exact_pma_budget(self):
        values = {
            name: int(value)
            for name, value in re.findall(
                r"#define UUSB_(BTAB|EP0|KEYBOARD|MOUSE|CONSUMER|MSC)_PMA_BYTES\s+(\d+)U",
                self.source,
            )
        }
        self.assertEqual(values, {
            "BTAB": 64, "EP0": 128, "KEYBOARD": 8,
            "MOUSE": 8, "CONSUMER": 2, "MSC": 128,
        })
        self.assertEqual(sum(values.values()), 338)
        self.assertIn("_Static_assert(UUSB_HID_MSC_PMA_BYTES == 338U", self.source)
        self.assertLessEqual(sum(values.values()), 512)

    def test_class_counts_and_msc_buffer(self):
        self.assertRegex(self.config, r"#define CFG_TUD_HID\s+3")
        self.assertRegex(self.config, r"#define CFG_TUD_MSC\s+1")
        self.assertRegex(self.config, r"#define CFG_TUD_MSC_EP_BUFSIZE\s+512")


if __name__ == "__main__":
    unittest.main()
