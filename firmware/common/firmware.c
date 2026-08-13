#include "uusb_firmware.h"

#include <stdbool.h>

#include "stm32f1xx.h"
#include "platform.h"
#include "tusb.h"
#include "usb_lifecycle.h"
#include "uusb_status.h"
#include "watchdog.h"

static uusb_usb_lifecycle_t usb_lifecycle;
TU_ATTR_WEAK void uusb_profile_task(void) {}
TU_ATTR_WEAK void uusb_profile_mount(void) {}
TU_ATTR_WEAK void uusb_profile_umount(void) {}
TU_ATTR_WEAK void uusb_profile_suspend(void) {}
TU_ATTR_WEAK void uusb_profile_resume(void) {}


static void idle_forever(void)
{
    for (;;) {
        __WFI();
    }
}

int uusb_firmware_run(uusb_profile_t profile)
{
    return uusb_firmware_run_with_hooks(profile, NULL, NULL);
}

int uusb_firmware_run_with_hooks(
    uusb_profile_t profile,
    const uusb_mailbox_hooks_t *mailbox_hooks,
    void *mailbox_context)
{
    uusb_clock_state_t const clock = uusb_platform_clock_state();
    uusb_mailbox_boot(
        profile,
        UUSB_FIRMWARE_VERSION(0, 1, 0),
        clock == UUSB_CLOCK_VALID,
        mailbox_hooks,
        mailbox_context);
    uusb_status_initialize(profile, clock);
    if (clock != UUSB_CLOCK_VALID) {
        (void)uusb_platform_usb_peripheral_disable(NULL);
        idle_forever();
    }

    static uusb_usb_lifecycle_hooks_t const hooks = {
        .usb_peripheral_disable = uusb_platform_usb_peripheral_disable,
        .pa11_input_high_impedance = uusb_platform_pa11_input_high_impedance,
        .pa12_output_low = uusb_platform_pa12_output_low,
        .delay_ms_bounded = uusb_platform_delay_ms_bounded,
        .pa11_pa12_restore_usb_mode = uusb_platform_pa11_pa12_restore_usb_mode,
        .tinyusb_init = uusb_platform_tinyusb_init,
    };

    uusb_usb_lifecycle_reset(&usb_lifecycle);
    (void)uusb_usb_lifecycle_begin(&usb_lifecycle, &hooks, NULL);
    uusb_status_set_lifecycle(&usb_lifecycle);
    if (usb_lifecycle.state == UUSB_USB_LIFECYCLE_FAILED) {
        (void)uusb_platform_usb_peripheral_disable(NULL);
        uusb_status_set_runtime(UUSB_STATUS_USB_START_FAILED);
        idle_forever();
    }
    if (SysTick_Config(SystemCoreClock / UINT32_C(1000)) != 0U) {
        (void)uusb_platform_usb_peripheral_disable(NULL);
        uusb_status_set_runtime(UUSB_STATUS_USB_START_FAILED);
        idle_forever();
    }

    bool const watchdog_enabled = uusb_watchdog_start();
    uusb_status_set_watchdog(watchdog_enabled);
    uusb_status_set_runtime(UUSB_STATUS_RUNNING);

    for (;;) {
        tud_task();
        uusb_mailbox_poll();
        uusb_profile_task();
        if (watchdog_enabled) {
            uusb_watchdog_feed();
        }
    }
}

void SysTick_Handler(void)
{
    uusb_mailbox_tick_1ms();
}

void tud_mount_cb(void)
{
    uusb_status_set_mounted(true);
    uusb_status_set_suspended(false);
    uusb_mailbox_set_mounted(true);
    uusb_mailbox_set_suspended(false);
    uusb_profile_mount();
}

void tud_umount_cb(void)
{
    uusb_status_set_mounted(false);
    uusb_status_set_suspended(false);
    uusb_mailbox_usb_unmounted();
    uusb_profile_umount();
}

void tud_suspend_cb(bool remote_wakeup_en)
{
    (void)remote_wakeup_en;
    uusb_status_set_suspended(true);
    uusb_mailbox_set_suspended(true);
    uusb_profile_suspend();
}

void tud_resume_cb(void)
{
    uusb_status_set_suspended(false);
    uusb_mailbox_set_suspended(false);
    uusb_profile_resume();
}
