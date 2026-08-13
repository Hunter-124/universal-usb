#include "watchdog.h"

#include <stdint.h>

#include "stm32f1xx.h"

#define UUSB_IWDG_WRITE_ENABLE UINT16_C(0x5555)
#define UUSB_IWDG_RELOAD UINT16_C(0xaaaa)
#define UUSB_IWDG_START UINT16_C(0xcccc)
#define UUSB_IWDG_UPDATE_WAIT UINT32_C(100000)

bool uusb_watchdog_start(void)
{
    IWDG->KR = UUSB_IWDG_WRITE_ENABLE;
    IWDG->PR = 4U; /* LSI / 64. */
    IWDG->RLR = UINT32_C(1250); /* Approximately two seconds at 40 kHz. */
    for (uint32_t count = 0; count < UUSB_IWDG_UPDATE_WAIT; ++count) {
        if ((IWDG->SR & (IWDG_SR_PVU | IWDG_SR_RVU)) == 0U) {
            IWDG->KR = UUSB_IWDG_RELOAD;
            IWDG->KR = UUSB_IWDG_START;
            return true;
        }
    }
    return false;
}

void uusb_watchdog_feed(void)
{
    IWDG->KR = UUSB_IWDG_RELOAD;
}
