#include <stddef.h>
#include <stdint.h>

#include "platform.h"
#include "stm32f1xx.h"

extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;
extern uint32_t _estack;

int main(void);
void Reset_Handler(void);
void Default_Handler(void);
void USB_LP_CAN1_RX0_IRQHandler(void);

#define UUSB_WEAK_HANDLER(name) \
    void name(void) __attribute__((weak, alias("Default_Handler")))

UUSB_WEAK_HANDLER(NMI_Handler);
UUSB_WEAK_HANDLER(HardFault_Handler);
UUSB_WEAK_HANDLER(MemManage_Handler);
UUSB_WEAK_HANDLER(BusFault_Handler);
UUSB_WEAK_HANDLER(UsageFault_Handler);
UUSB_WEAK_HANDLER(SVC_Handler);
UUSB_WEAK_HANDLER(DebugMon_Handler);
UUSB_WEAK_HANDLER(PendSV_Handler);
UUSB_WEAK_HANDLER(SysTick_Handler);
UUSB_WEAK_HANDLER(WWDG_IRQHandler);
UUSB_WEAK_HANDLER(PVD_IRQHandler);
UUSB_WEAK_HANDLER(TAMPER_IRQHandler);
UUSB_WEAK_HANDLER(RTC_IRQHandler);
UUSB_WEAK_HANDLER(FLASH_IRQHandler);
UUSB_WEAK_HANDLER(RCC_IRQHandler);
UUSB_WEAK_HANDLER(EXTI0_IRQHandler);
UUSB_WEAK_HANDLER(EXTI1_IRQHandler);
UUSB_WEAK_HANDLER(EXTI2_IRQHandler);
UUSB_WEAK_HANDLER(EXTI3_IRQHandler);
UUSB_WEAK_HANDLER(EXTI4_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel1_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel2_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel3_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel4_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel5_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel6_IRQHandler);
UUSB_WEAK_HANDLER(DMA1_Channel7_IRQHandler);
UUSB_WEAK_HANDLER(ADC1_2_IRQHandler);
UUSB_WEAK_HANDLER(USB_HP_CAN1_TX_IRQHandler);
UUSB_WEAK_HANDLER(CAN1_RX1_IRQHandler);
UUSB_WEAK_HANDLER(CAN1_SCE_IRQHandler);
UUSB_WEAK_HANDLER(EXTI9_5_IRQHandler);
UUSB_WEAK_HANDLER(TIM1_BRK_IRQHandler);
UUSB_WEAK_HANDLER(TIM1_UP_IRQHandler);
UUSB_WEAK_HANDLER(TIM1_TRG_COM_IRQHandler);
UUSB_WEAK_HANDLER(TIM1_CC_IRQHandler);
UUSB_WEAK_HANDLER(TIM2_IRQHandler);
UUSB_WEAK_HANDLER(TIM3_IRQHandler);
UUSB_WEAK_HANDLER(TIM4_IRQHandler);
UUSB_WEAK_HANDLER(I2C1_EV_IRQHandler);
UUSB_WEAK_HANDLER(I2C1_ER_IRQHandler);
UUSB_WEAK_HANDLER(I2C2_EV_IRQHandler);
UUSB_WEAK_HANDLER(I2C2_ER_IRQHandler);
UUSB_WEAK_HANDLER(SPI1_IRQHandler);
UUSB_WEAK_HANDLER(SPI2_IRQHandler);
UUSB_WEAK_HANDLER(USART1_IRQHandler);
UUSB_WEAK_HANDLER(USART2_IRQHandler);
UUSB_WEAK_HANDLER(USART3_IRQHandler);
UUSB_WEAK_HANDLER(EXTI15_10_IRQHandler);
UUSB_WEAK_HANDLER(RTCAlarm_IRQHandler);
UUSB_WEAK_HANDLER(USBWakeUp_IRQHandler);

typedef void (*uusb_handler_t)(void);

typedef struct {
    void *initial_stack;
    uusb_handler_t reset;
    uusb_handler_t nmi;
    uusb_handler_t hard_fault;
    uusb_handler_t memory_management;
    uusb_handler_t bus_fault;
    uusb_handler_t usage_fault;
    uusb_handler_t reserved_7;
    uusb_handler_t reserved_8;
    uusb_handler_t reserved_9;
    uusb_handler_t reserved_10;
    uusb_handler_t svc;
    uusb_handler_t debug_monitor;
    uusb_handler_t reserved_13;
    uusb_handler_t pend_sv;
    uusb_handler_t systick;
    uusb_handler_t irq[43];
} uusb_vector_table_t;

__attribute__((used, section(".isr_vector")))
static uusb_vector_table_t const vector_table = {
    .initial_stack = &_estack,
    .reset = Reset_Handler,
    .nmi = NMI_Handler,
    .hard_fault = HardFault_Handler,
    .memory_management = MemManage_Handler,
    .bus_fault = BusFault_Handler,
    .usage_fault = UsageFault_Handler,
    .reserved_7 = NULL,
    .reserved_8 = NULL,
    .reserved_9 = NULL,
    .reserved_10 = NULL,
    .svc = SVC_Handler,
    .debug_monitor = DebugMon_Handler,
    .reserved_13 = NULL,
    .pend_sv = PendSV_Handler,
    .systick = SysTick_Handler,
    .irq = {
        WWDG_IRQHandler,
        PVD_IRQHandler,
        TAMPER_IRQHandler,
        RTC_IRQHandler,
        FLASH_IRQHandler,
        RCC_IRQHandler,
        EXTI0_IRQHandler,
        EXTI1_IRQHandler,
        EXTI2_IRQHandler,
        EXTI3_IRQHandler,
        EXTI4_IRQHandler,
        DMA1_Channel1_IRQHandler,
        DMA1_Channel2_IRQHandler,
        DMA1_Channel3_IRQHandler,
        DMA1_Channel4_IRQHandler,
        DMA1_Channel5_IRQHandler,
        DMA1_Channel6_IRQHandler,
        DMA1_Channel7_IRQHandler,
        ADC1_2_IRQHandler,
        USB_HP_CAN1_TX_IRQHandler,
        USB_LP_CAN1_RX0_IRQHandler,
        CAN1_RX1_IRQHandler,
        CAN1_SCE_IRQHandler,
        EXTI9_5_IRQHandler,
        TIM1_BRK_IRQHandler,
        TIM1_UP_IRQHandler,
        TIM1_TRG_COM_IRQHandler,
        TIM1_CC_IRQHandler,
        TIM2_IRQHandler,
        TIM3_IRQHandler,
        TIM4_IRQHandler,
        I2C1_EV_IRQHandler,
        I2C1_ER_IRQHandler,
        I2C2_EV_IRQHandler,
        I2C2_ER_IRQHandler,
        SPI1_IRQHandler,
        SPI2_IRQHandler,
        USART1_IRQHandler,
        USART2_IRQHandler,
        USART3_IRQHandler,
        EXTI15_10_IRQHandler,
        RTCAlarm_IRQHandler,
        USBWakeUp_IRQHandler,
    },
};

_Static_assert(
    sizeof(uusb_vector_table_t) == (59U * sizeof(uint32_t)),
    "STM32F103 medium-density vector table size changed");

void Reset_Handler(void)
{
    uint32_t const *source = &_sidata;
    for (uint32_t *destination = &_sdata; destination < &_edata; ++destination) {
        *destination = *source;
        ++source;
    }
    for (uint32_t *destination = &_sbss; destination < &_ebss; ++destination) {
        *destination = 0U;
    }

    SCB->VTOR = (uint32_t)(uintptr_t)&vector_table;
    (void)uusb_platform_clock_configure();
    (void)main();
    for (;;) {
        __WFI();
    }
}

void Default_Handler(void)
{
    for (;;) {
        __WFI();
    }
}
