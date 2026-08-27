cmake_minimum_required(VERSION 3.10)
project(robot LANGUAGES CXX)
set(TARGET_NAME robot)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenCV REQUIRED)

add_executable(${TARGET_NAME}
    src/main.cpp
    src/face.cpp
    src/screen.cpp
    src/fan.cpp
    src/battery.cpp
    src/servos.cpp
    src/renderer.cpp
    src/tracker.cpp
    src/camera.cpp
    src/config.cpp
    src/interface.cpp
    src/servos/SMS_STS.cpp
    src/servos/SCS.cpp
    src/servos/SCSerial.cpp
)

target_include_directories(${TARGET_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# Find Homebrew SDL2 on Mac, Apple Silicon or Intel
if(APPLE)
    foreach(prefix /opt/homebrew /usr/local)
        if(EXISTS "${prefix}/lib/pkgconfig")
            list(APPEND CMAKE_PREFIX_PATH "${prefix}")
            set(ENV{PKG_CONFIG_PATH} "${prefix}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")
        endif()
    endforeach()
endif()

find_package(PkgConfig)
if(NOT PKG_CONFIG_FOUND)
    message(FATAL_ERROR "SDL2 not found. Run ./install.sh to install SDL2.")
endif()

# Find SDL2, SDL2_image, and SDL2_ttf
pkg_check_modules(SDL2 IMPORTED_TARGET sdl2 SDL2_image SDL2_ttf)
if(NOT SDL2_FOUND)
    message(FATAL_ERROR "SDL2 not found. Run ./install.sh to install SDL2.")
endif()
target_link_libraries(${TARGET_NAME} PRIVATE PkgConfig::SDL2)

# pkg-config gives .../include/SDL2, sources include <SDL2/SDL.h>
foreach(sdl_include ${SDL2_INCLUDE_DIRS})
    get_filename_component(sdl_include_name "${sdl_include}" NAME)
    if(sdl_include_name STREQUAL "SDL2")
        get_filename_component(sdl_include_parent "${sdl_include}" DIRECTORY)
        target_include_directories(${TARGET_NAME} PRIVATE "${sdl_include_parent}")
    endif()
endforeach()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    pkg_check_modules(GSTREAMER REQUIRED gstreamer-1.0)
    target_include_directories(${TARGET_NAME} PRIVATE ${GSTREAMER_INCLUDE_DIRS})
    target_link_libraries(${TARGET_NAME} PRIVATE ${GSTREAMER_LIBRARIES})
elseif(APPLE AND SDL2_LIBRARY_DIRS)
    set_target_properties(${TARGET_NAME} PROPERTIES INSTALL_RPATH "${SDL2_LIBRARY_DIRS}" BUILD_WITH_INSTALL_RPATH TRUE)
endif()

target_link_libraries(${TARGET_NAME} PRIVATE ${OpenCV_LIBS})
