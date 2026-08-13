ENTRY(Reset_Handler)

/* Guaranteed STM32F103C8 resources only.  Clone-reported excess memory is not
 * addressable by this image. */
MEMORY
{
  FLASH   (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
  MAILBOX (rwx) : ORIGIN = 0x20000000, LENGTH = 4K
  RAM     (rwx) : ORIGIN = 0x20001000, LENGTH = 16K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);
_Min_Heap_Size = 0;
_Min_Stack_Size = 1024;

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } > FLASH

  .text :
  {
    . = ALIGN(4);
    *(.text .text.*)
    *(.rodata .rodata.*)
    KEEP(*(.init))
    KEEP(*(.fini))
    . = ALIGN(4);
    __text_end__ = .;
  } > FLASH

  .ARM.extab :
  {
    *(.ARM.extab* .gnu.linkonce.armextab.*)
  } > FLASH

  .ARM.exidx :
  {
    __exidx_start = .;
    *(.ARM.exidx* .gnu.linkonce.armexidx.*)
    __exidx_end = .;
  } > FLASH

  .preinit_array :
  {
    PROVIDE_HIDDEN(__preinit_array_start = .);
    KEEP(*(.preinit_array*))
    PROVIDE_HIDDEN(__preinit_array_end = .);
  } > FLASH

  .init_array :
  {
    PROVIDE_HIDDEN(__init_array_start = .);
    KEEP(*(SORT(.init_array.*)))
    KEEP(*(.init_array*))
    PROVIDE_HIDDEN(__init_array_end = .);
  } > FLASH

  .fini_array :
  {
    PROVIDE_HIDDEN(__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*)))
    KEEP(*(.fini_array*))
    PROVIDE_HIDDEN(__fini_array_end = .);
  } > FLASH

  /* Retained SWD transport window.  Startup must never initialize this range. */
  .mailbox (NOLOAD) :
  {
    . = ALIGN(4);
    __mailbox_start__ = .;
    KEEP(*(.mailbox .mailbox.*))
    . = __mailbox_start__ + 4096;
    __mailbox_end__ = .;
  } > MAILBOX

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data .data.*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT > FLASH
  _sidata = LOADADDR(.data);

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss .bss.*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > RAM

  .noinit (NOLOAD) :
  {
    . = ALIGN(4);
    *(.noinit .noinit.*)
    . = ALIGN(4);
  } > RAM

  .heap_stack (NOLOAD) :
  {
    . = ALIGN(8);
    __heap_start__ = .;
    . = . + _Min_Heap_Size;
    __heap_end__ = .;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
    __ram_used_end__ = .;
  } > RAM

  __flash_image_end__ = LOADADDR(.data) + SIZEOF(.data);

  /DISCARD/ :
  {
    *(.note.GNU-stack)
    *(.comment)
  }
}

ASSERT(ORIGIN(FLASH) == 0x08000000, "unexpected FLASH origin")
ASSERT(LENGTH(FLASH) == 64K, "FLASH must be limited to 64 KiB")
ASSERT(ORIGIN(MAILBOX) == 0x20000000, "mailbox origin changed")
ASSERT(LENGTH(MAILBOX) == 4096, "mailbox region must be 4096 bytes")
ASSERT(__mailbox_start__ == 0x20000000, ".mailbox must start at 0x20000000")
ASSERT((__mailbox_end__ - __mailbox_start__) == 4096,
       ".mailbox must occupy exactly 4096 bytes")
ASSERT(ORIGIN(RAM) == 0x20001000, "application RAM must follow mailbox")
ASSERT((LENGTH(MAILBOX) + LENGTH(RAM)) == 20K,
       "total SRAM must be limited to 20 KiB")
ASSERT(__flash_image_end__ <= (ORIGIN(FLASH) + LENGTH(FLASH)),
       "firmware exceeds guaranteed 64 KiB flash")
ASSERT(__ram_used_end__ <= (ORIGIN(RAM) + LENGTH(RAM)),
       "firmware exceeds SRAM after mailbox reservation")
ASSERT(_sbss >= ORIGIN(RAM), "startup zero range overlaps mailbox")
