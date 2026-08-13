#ifndef UUSB_FIRMWARE_H
#define UUSB_FIRMWARE_H

#include "uusb_mailbox.h"

int uusb_firmware_run(uusb_profile_t profile);
int uusb_firmware_run_with_hooks(
    uusb_profile_t profile,
    const uusb_mailbox_hooks_t *hooks,
    void *hook_context);

#endif
