#ifndef UUSB_HID_MSC_TUSB_CONFIG_H
#define UUSB_HID_MSC_TUSB_CONFIG_H

#define CFG_TUSB_OS OPT_OS_NONE
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#define CFG_TUD_ENABLED 1
#define CFG_TUD_MAX_SPEED OPT_MODE_FULL_SPEED
#define CFG_TUD_ENDPOINT0_SIZE 64
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))

/* Phase-2 bootstrap enumerates EP0 only. HID and MSC are enabled in phase 4. */
#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_AUDIO 0
#define CFG_TUD_VIDEO 0
#define CFG_TUD_VENDOR 0

#endif
