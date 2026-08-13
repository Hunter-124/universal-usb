#ifndef UUSB_WATCHDOG_H
#define UUSB_WATCHDOG_H

#include <stdbool.h>

bool uusb_watchdog_start(void);
void uusb_watchdog_feed(void);

#endif
