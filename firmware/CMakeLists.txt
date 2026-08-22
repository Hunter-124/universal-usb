cmake_minimum_required(VERSION 3.24)

set(BOARD stm32f103_bluepill CACHE STRING "TinyUSB board name")
if(NOT BOARD STREQUAL "stm32f103_bluepill")
  message(FATAL_ERROR "Universal USB supports only BOARD=stm32f103_bluepill")
endif()
set(FAMILY stm32f1 CACHE STRING "TinyUSB MCU family" FORCE)
set(TOOLCHAIN gcc CACHE STRING "TinyUSB toolchain selector" FORCE)
set(RTOS noos CACHE STRING "TinyUSB RTOS selector" FORCE)

find_program(UUSB_PYTHON NAMES python3 REQUIRED)
set(UUSB_ROOT "${CMAKE_CURRENT_LIST_DIR}/..")
get_filename_component(UUSB_ROOT "${UUSB_ROOT}" ABSOLUTE)
set(UUSB_TINYUSB_SOURCE "${UUSB_ROOT}/third_party/tinyusb")
set(UUSB_TINYUSB_BUILD_COPY "${CMAKE_BINARY_DIR}/_deps/tinyusb")
set(UUSB_TINYUSB_PATCH "${UUSB_ROOT}/patches/tinyusb-stm32f1-no-usb-suspend.patch")

execute_process(
  COMMAND "${UUSB_PYTHON}" "${UUSB_ROOT}/tools/prepare_tinyusb.py"
          --source "${UUSB_TINYUSB_SOURCE}"
          --destination "${UUSB_TINYUSB_BUILD_COPY}"
          --patch "${UUSB_TINYUSB_PATCH}"
  RESULT_VARIABLE UUSB_TINYUSB_PREPARE_RESULT
  ERROR_VARIABLE UUSB_TINYUSB_PREPARE_ERROR
)
if(NOT UUSB_TINYUSB_PREPARE_RESULT EQUAL 0)
  message(FATAL_ERROR "TinyUSB build-copy preparation failed:\n${UUSB_TINYUSB_PREPARE_ERROR}")
endif()

# Import TinyUSB's pinned STM32F1 family metadata and helper functions.  The
# project deliberately does not call its permissive Blue Pill startup rule.
include("${UUSB_TINYUSB_BUILD_COPY}/hw/bsp/family_support.cmake")

# These project-owned paths override the BSP defaults after family support has
# been included.  Every target therefore links the guarded 64 KiB/20 KiB map.
set(LD_FILE_GNU "${CMAKE_CURRENT_LIST_DIR}/linker/stm32f103x8-mailbox.ld")
set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/cmake/arm-none-eabi-gcc.cmake"
    CACHE FILEPATH "Universal USB GNU Arm toolchain" FORCE)
set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/cmake/arm-none-eabi-gcc.cmake")

project(universal_usb_firmware VERSION 0.1.0 LANGUAGES C)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
include(CTest)

if(NOT CMAKE_C_COMPILER_ID STREQUAL "GNU")
  message(FATAL_ERROR "Firmware requires GNU Arm Embedded GCC")
endif()

add_library(uusb_stm32f1_hal STATIC
  "${ST_HAL_DRIVER}/Src/stm32f1xx_hal.c"
  "${ST_HAL_DRIVER}/Src/stm32f1xx_hal_cortex.c"
  "${ST_HAL_DRIVER}/Src/stm32f1xx_hal_gpio.c"
  "${ST_HAL_DRIVER}/Src/stm32f1xx_hal_rcc.c"
  "${ST_HAL_DRIVER}/Src/stm32f1xx_hal_rcc_ex.c"
)
target_include_directories(uusb_stm32f1_hal PUBLIC
  "${CMSIS_5}/CMSIS/Core/Include"
  "${ST_CMSIS}/Include"
  "${ST_HAL_DRIVER}/Inc"
  "${UUSB_TINYUSB_BUILD_COPY}/hw/bsp/stm32f1"
)
target_compile_definitions(uusb_stm32f1_hal PUBLIC
  STM32F103xB
  HSE_VALUE=8000000U
)
target_compile_features(uusb_stm32f1_hal PUBLIC c_std_17)
target_compile_options(uusb_stm32f1_hal PRIVATE
  -ffunction-sections -fdata-sections -fno-common
)

set(UUSB_SHARED_SOURCES
  common/descriptor_strings.c
  common/hid.c
  common/crc32c.c
  common/firmware.c
  common/status.c
  common/mailbox.c
  common/watchdog.c
  platform/stm32f1/platform.c
  platform/stm32f1/startup.c
  platform/stm32f1/usb_lifecycle.c
)

function(uusb_add_profile target profile_directory profile_id)
  add_executable(${target}
    ${UUSB_SHARED_SOURCES}
    "profiles/${profile_directory}/main.c"
    "profiles/${profile_directory}/usb_descriptors.c"
  )
  set_source_files_properties(
    ${UUSB_SHARED_SOURCES}
    "profiles/${profile_directory}/main.c"
    "profiles/${profile_directory}/usb_descriptors.c"
    PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror"
  )
  set_target_properties(${target} PROPERTIES
    C_STANDARD 17
    C_STANDARD_REQUIRED YES
    C_EXTENSIONS NO
    SUFFIX ".elf"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
  )
  target_include_directories(${target} PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/profiles/${profile_directory}"
    "${CMAKE_CURRENT_LIST_DIR}/include"
    "${CMAKE_CURRENT_LIST_DIR}/common"
    "${CMAKE_CURRENT_LIST_DIR}/platform/stm32f1"
    "${UUSB_TINYUSB_BUILD_COPY}/hw/bsp"
    "${UUSB_TINYUSB_BUILD_COPY}/hw/bsp/stm32f1"
    "${UUSB_TINYUSB_BUILD_COPY}/hw/bsp/stm32f1/boards/${BOARD}"
  )
  target_compile_definitions(${target} PRIVATE
    CFG_TUSB_MCU=OPT_MCU_STM32F1
    CFG_TUSB_OS=OPT_OS_NONE
    UUSB_FIRMWARE_PROFILE=${profile_id}
  )
  target_compile_options(${target} PRIVATE
    -ffunction-sections -fdata-sections -fno-common
  )
  target_link_libraries(${target} PRIVATE uusb_stm32f1_hal)

  family_add_tinyusb(${target} OPT_MCU_STM32F1)
  target_sources(${target} PRIVATE
    "${UUSB_TINYUSB_BUILD_COPY}/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c"
    "${UUSB_TINYUSB_BUILD_COPY}/src/portable/st/stm32_fsdev/fsdev_common.c"
  )
  # TinyUSB's F1 packet-memory helper uses the GNU `asm` spelling. Keep
  # project-owned sources strict C17 while compiling that pinned upstream file
  # as GNU C17.
  set_source_files_properties(
    "${UUSB_TINYUSB_BUILD_COPY}/src/portable/st/stm32_fsdev/fsdev_common.c"
    PROPERTIES COMPILE_OPTIONS "-std=gnu17"
  )

  target_link_options(${target} PRIVATE
    -nostartfiles
    --specs=nosys.specs
    --specs=nano.specs
    "LINKER:--gc-sections"
    "LINKER:--script=${LD_FILE_GNU}"
    "LINKER:-Map=${CMAKE_BINARY_DIR}/${target}.map"
    "LINKER:--fatal-warnings"
  )

  add_custom_command(TARGET ${target} POST_BUILD
    COMMAND "${UUSB_PYTHON}" "${UUSB_ROOT}/tools/check-firmware-size.py"
            --objdump "${CMAKE_OBJDUMP}" "$<TARGET_FILE:${target}>"
    COMMAND "${CMAKE_OBJCOPY}" -Obinary "$<TARGET_FILE:${target}>"
            "${CMAKE_BINARY_DIR}/${target}.bin"
    COMMAND "${CMAKE_OBJCOPY}" -Oihex "$<TARGET_FILE:${target}>"
            "${CMAKE_BINARY_DIR}/${target}.hex"
    VERBATIM
  )
endfunction()

uusb_add_profile(uusb-hid-msc hid-msc 1)
target_sources(uusb-hid-msc PRIVATE common/msc.c)
set_source_files_properties(common/msc.c PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")

uusb_add_profile(uusb-microphone microphone 2)
target_sources(uusb-microphone PRIVATE common/tone.c)
set_source_files_properties(common/tone.c PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")

uusb_add_profile(uusb-webcam webcam 3)
target_sources(uusb-webcam PRIVATE common/uvc_pattern.c)
set_source_files_properties(common/uvc_pattern.c PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")

uusb_add_profile(uusb-security-token security-token 4)
target_sources(uusb-security-token PRIVATE common/token_usb.c)
set_source_files_properties(common/token_usb.c PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")

if(BUILD_TESTING)
  find_program(UUSB_HOST_CC NAMES cc gcc clang REQUIRED)

  function(uusb_add_native_test name)
    add_test(
      NAME "native-${name}"
      COMMAND "${UUSB_PYTHON}" "${UUSB_ROOT}/tools/run-native-c-test.py"
              --compiler "${UUSB_HOST_CC}"
              --include "${CMAKE_CURRENT_LIST_DIR}/include"
              --include "${CMAKE_CURRENT_LIST_DIR}/platform/stm32f1"
              --include "${CMAKE_CURRENT_LIST_DIR}/tests"
              ${ARGN}
    )
  endfunction()

  uusb_add_native_test(crc-mailbox
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_mailbox.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/mailbox.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/crc32c.c")
  uusb_add_native_test(hid-state
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_hid.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/hid.c")
  uusb_add_native_test(usb-lifecycle
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_usb_lifecycle.c"
    "${CMAKE_CURRENT_LIST_DIR}/platform/stm32f1/usb_lifecycle.c")
  uusb_add_native_test(msc-state
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_msc.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/msc.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/mailbox.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/crc32c.c")
  uusb_add_native_test(tone
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_tone.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/tone.c")
  uusb_add_native_test(uvc-pattern
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_uvc_pattern.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/uvc_pattern.c")
  uusb_add_native_test(token-usb
    "${CMAKE_CURRENT_LIST_DIR}/tests/test_token_usb.c"
    "${CMAKE_CURRENT_LIST_DIR}/common/token_usb.c")
endif()
