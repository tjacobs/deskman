cmake_minimum_required(VERSION 3.10)
project(robot LANGUAGES CXX)
set(TARGET_NAME robot)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenCV REQUIRED)

add_executable(${TARGET_NAME}
    main.cpp
    face.cpp
    screen.cpp
    fan.cpp
    servos.cpp
    control.cpp
    renderer.cpp
    tracker.cpp
    camera.cpp
    config.cpp
    servos/SMS_STS.cpp
    servos/SCS.cpp
    servos/SCSerial.cpp
)

target_include_directories(${TARGET_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_include_directories(${TARGET_NAME} PRIVATE /usr/include/SDL2)
    target_link_libraries(${TARGET_NAME} PRIVATE SDL2 SDL2_image SDL2_ttf)

    find_package(PkgConfig REQUIRED)
    pkg_check_modules(GSTREAMER REQUIRED gstreamer-1.0)
    target_include_directories(${TARGET_NAME} PRIVATE ${GSTREAMER_INCLUDE_DIRS})
    target_link_libraries(${TARGET_NAME} PRIVATE ${GSTREAMER_LIBRARIES})
elseif(APPLE)
    target_include_directories(${TARGET_NAME} PRIVATE /opt/homebrew/include /opt/homebrew/include/SDL2)
    target_link_directories(${TARGET_NAME} PRIVATE /opt/homebrew/lib)
    set_target_properties(${TARGET_NAME} PROPERTIES INSTALL_RPATH "/opt/homebrew/lib" BUILD_WITH_INSTALL_RPATH TRUE)
    target_link_libraries(${TARGET_NAME} PRIVATE /opt/homebrew/lib/libSDL2.dylib /opt/homebrew/lib/libSDL2_image.dylib /opt/homebrew/lib/libSDL2_ttf.dylib)
endif()

target_link_libraries(${TARGET_NAME} PRIVATE ${OpenCV_LIBS})
