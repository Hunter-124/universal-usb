#ifndef UUSB_FIRMWARE_H
#define UUSB_FIRMWARE_H

#include <stdint.h>

typedef enum {
    UUSB_PROFILE_HID_MSC = 1,
    UUSB_PROFILE_MICROPHONE = 2,
    UUSB_PROFILE_WEBCAM = 3
} uusb_profile_t;

int uusb_firmware_run(uusb_profile_t profile);

#endif
