#include "platform.h"

#include "stm32f1xx.h"
#include "tusb.h"

#define UUSB_CLOCK_WAIT_ITERATIONS UINT32_C(1000000)
#define UUSB_SYSTICK_WAIT_ITERATIONS UINT32_C(1000000)
#define UUSB_USB_DM_PIN UINT32_C(11)
#define UUSB_USB_DP_PIN UINT32_C(12)

uint32_t SystemCoreClock = UINT32_C(8000000);

static uusb_clock_state_t clock_state = UUSB_CLOCK_UNINITIALIZED;

static bool wait_mask_set(volatile uint32_t const *reg, uint32_t mask)
{
    for (uint32_t count = 0; count < UUSB_CLOCK_WAIT_ITERATIONS; ++count) {
        if ((*reg & mask) == mask) {
            return true;
        }
    }
    return false;
}

static bool wait_mask_value(
    volatile uint32_t const *reg,
    uint32_t mask,
    uint32_t value)
{
    for (uint32_t count = 0; count < UUSB_CLOCK_WAIT_ITERATIONS; ++count) {
        if ((*reg & mask) == value) {
            return true;
        }
    }
    return false;
}

static void gpioa_configure(uint32_t pin, uint32_t nibble)
{
    volatile uint32_t *const config = (pin < 8U) ? &GPIOA->CRL : &GPIOA->CRH;
    uint32_t const shift = (pin & 7U) * 4U;
    *config = (*config & ~(UINT32_C(0xf) << shift)) | (nibble << shift);
}

static bool clock_fail(void)
{
    (void)uusb_platform_usb_peripheral_disable(NULL);
    RCC->CR |= RCC_CR_HSION;
    (void)wait_mask_set(&RCC->CR, RCC_CR_HSIRDY);
    RCC->CFGR &= ~RCC_CFGR_SW;
    (void)wait_mask_value(&RCC->CFGR, RCC_CFGR_SWS, RCC_CFGR_SWS_HSI);
    RCC->CR &= ~(RCC_CR_PLLON | RCC_CR_HSEON | RCC_CR_CSSON);
    SystemCoreClock = UINT32_C(8000000);
    clock_state = UUSB_CLOCK_HSE_FAILED;
    return false;
}

bool uusb_platform_clock_configure(void)
{
    clock_state = UUSB_CLOCK_UNINITIALIZED;
    (void)uusb_platform_usb_peripheral_disable(NULL);

    /* Two wait states are required at 72 MHz and must precede escalation. */
    FLASH->ACR = (FLASH->ACR & ~FLASH_ACR_LATENCY) |
                 FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY_2;

    RCC->CR |= RCC_CR_HSEON;
    if (!wait_mask_set(&RCC->CR, RCC_CR_HSERDY)) {
        return clock_fail();
    }

    RCC->CFGR = (RCC->CFGR &
                 ~(RCC_CFGR_HPRE | RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2 |
                   RCC_CFGR_PLLSRC | RCC_CFGR_PLLXTPRE | RCC_CFGR_PLLMULL |
                   RCC_CFGR_USBPRE)) |
                RCC_CFGR_HPRE_DIV1 | RCC_CFGR_PPRE1_DIV2 |
                RCC_CFGR_PPRE2_DIV1 | RCC_CFGR_PLLSRC |
                RCC_CFGR_PLLMULL9;

    RCC->CR |= RCC_CR_PLLON;
    if (!wait_mask_set(&RCC->CR, RCC_CR_PLLRDY)) {
        return clock_fail();
    }

    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    if (!wait_mask_value(&RCC->CFGR, RCC_CFGR_SWS, RCC_CFGR_SWS_PLL)) {
        return clock_fail();
    }

    if ((HSE_VALUE != UINT32_C(8000000)) ||
        ((RCC->CFGR & RCC_CFGR_PLLMULL) != RCC_CFGR_PLLMULL9) ||
        ((RCC->CFGR & RCC_CFGR_USBPRE) != 0U)) {
        return clock_fail();
    }

    SystemCoreClock = UINT32_C(72000000);
    clock_state = UUSB_CLOCK_VALID;
    return true;
}

void SystemCoreClockUpdate(void)
{
    uint32_t const source = RCC->CFGR & RCC_CFGR_SWS;
    SystemCoreClock = (source == RCC_CFGR_SWS_PLL)
                          ? UINT32_C(72000000)
                          : UINT32_C(8000000);
}

uusb_clock_state_t uusb_platform_clock_state(void)
{
    return clock_state;
}

bool uusb_platform_usb_peripheral_disable(void *context)
{
    (void)context;
    NVIC_DisableIRQ(USB_LP_CAN1_RX0_IRQn);
    NVIC_ClearPendingIRQ(USB_LP_CAN1_RX0_IRQn);
    RCC->APB1ENR |= RCC_APB1ENR_USBEN;
    RCC->APB1RSTR |= RCC_APB1RSTR_USBRST;
    RCC->APB1RSTR &= ~RCC_APB1RSTR_USBRST;
    RCC->APB1ENR &= ~RCC_APB1ENR_USBEN;
    return true;
}

bool uusb_platform_pa11_input_high_impedance(void *context)
{
    (void)context;
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    gpioa_configure(UUSB_USB_DM_PIN, UINT32_C(0x4));
    return true;
}

bool uusb_platform_pa12_output_low(void *context)
{
    (void)context;
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    GPIOA->BRR = UINT32_C(1) << UUSB_USB_DP_PIN;
    gpioa_configure(UUSB_USB_DP_PIN, UINT32_C(0x2));
    GPIOA->BRR = UINT32_C(1) << UUSB_USB_DP_PIN;
    return true;
}

bool uusb_platform_delay_ms_bounded(void *context, uint32_t milliseconds)
{
    (void)context;
    if ((milliseconds == 0U) || (milliseconds > UINT32_C(1000)) ||
        (SystemCoreClock < UINT32_C(1000))) {
        return false;
    }

    uint32_t const reload = (SystemCoreClock / UINT32_C(1000)) - 1U;
    if (reload > SysTick_LOAD_RELOAD_Msk) {
        return false;
    }

    SysTick->CTRL = 0U;
    SysTick->LOAD = reload;
    SysTick->VAL = 0U;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
    for (uint32_t elapsed = 0; elapsed < milliseconds; ++elapsed) {
        bool ticked = false;
        for (uint32_t count = 0; count < UUSB_SYSTICK_WAIT_ITERATIONS; ++count) {
            if ((SysTick->CTRL & SysTick_CTRL_COUNTFLAG_Msk) != 0U) {
                ticked = true;
                break;
            }
        }
        if (!ticked) {
            SysTick->CTRL = 0U;
            return false;
        }
    }
    SysTick->CTRL = 0U;
    return true;
}

bool uusb_platform_pa11_pa12_restore_usb_mode(void *context)
{
    (void)context;
    gpioa_configure(UUSB_USB_DM_PIN, UINT32_C(0x4));
    gpioa_configure(UUSB_USB_DP_PIN, UINT32_C(0x4));
    if (clock_state != UUSB_CLOCK_VALID) {
        return false;
    }
    RCC->APB1ENR |= RCC_APB1ENR_USBEN;
    NVIC_SetPriority(USB_LP_CAN1_RX0_IRQn, 2U);
    NVIC_ClearPendingIRQ(USB_LP_CAN1_RX0_IRQn);
    NVIC_EnableIRQ(USB_LP_CAN1_RX0_IRQn);
    return true;
}

bool uusb_platform_tinyusb_init(void *context)
{
    (void)context;
    return (clock_state == UUSB_CLOCK_VALID) && tud_init(0U);
}

void USB_LP_CAN1_RX0_IRQHandler(void)
{
    tud_int_handler(0U);
}
