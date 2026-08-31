# Configure CMake
set(CMAKE_WARN_DEPRECATED OFF CACHE BOOL "" FORCE)
cmake_minimum_required(VERSION 3.7)

# Create project
project(robot)

# Configure C++
set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Add websocket library
add_subdirectory(ixwebsocket)

# Create teleport executable
add_executable(teleport src/main.cpp src/video.cpp src/audio.cpp src/interface.cpp src/call.cpp src/look.cpp src/move.cpp src/config.cpp)
target_link_libraries(teleport ixwebsocket)
target_include_directories(teleport PRIVATE include)

# Add WebRTC video backend
find_package(PkgConfig)
if(NOT PKG_CONFIG_FOUND)
  message(FATAL_ERROR "GStreamer WebRTC not found. Run teleport/install.sh to install it.")
endif()

# Find the GStreamer WebRTC packages
pkg_check_modules(GST_WEBRTC QUIET gstreamer-1.0 gstreamer-app-1.0 gstreamer-webrtc-1.0 gstreamer-sdp-1.0 gstreamer-video-1.0 gstreamer-fft-1.0 json-glib-1.0)
if(NOT GST_WEBRTC_FOUND)
  message(FATAL_ERROR "GStreamer WebRTC not found. Run teleport/install.sh to install it.")
endif()
message(STATUS "GStreamer WebRTC found")

# Warn when the nice plugin is missing, WebRTC needs it to connect
find_program(GST_INSPECT gst-inspect-1.0)
if(GST_INSPECT)
  execute_process(COMMAND ${GST_INSPECT} nicesrc RESULT_VARIABLE GST_NICE_RESULT OUTPUT_QUIET ERROR_QUIET)
  if(GST_NICE_RESULT EQUAL 0)
    message(STATUS "GStreamer nice plugin found")
  else()
    message(WARNING "GStreamer nice plugin not found. Run teleport/install.sh to install it.")
  endif()
endif()

# Build with the WebRTC backend
target_compile_definitions(teleport PRIVATE HAVE_GSTREAMER_WEBRTC GST_USE_UNSTABLE_API)
target_include_directories(teleport PRIVATE ${GST_WEBRTC_INCLUDE_DIRS})
target_compile_options(teleport PRIVATE ${GST_WEBRTC_CFLAGS_OTHER})
target_link_libraries(teleport ${GST_WEBRTC_LDFLAGS})

# Fullscreen remote video window on Linux X11
if(UNIX AND NOT APPLE)
  find_package(X11)
  if(X11_FOUND)
    message(STATUS "X11 found, fullscreen remote video enabled")
    target_compile_definitions(teleport PRIVATE HAVE_X11_FULLSCREEN)
    target_include_directories(teleport PRIVATE ${X11_INCLUDE_DIR})
    target_link_libraries(teleport ${X11_LIBRARIES})

    # Round the drag circles, XShape lives in Xext
    if(X11_Xext_FOUND)
      target_link_libraries(teleport ${X11_Xext_LIB})
    else()
      message(FATAL_ERROR "Xext not found, install libxext-dev")
    endif()
  endif()
endif()

# Call overlay on the local HDMI screen
if(UNIX AND NOT APPLE)
  pkg_check_modules(SDL2 REQUIRED sdl2 SDL2_ttf)
  target_include_directories(teleport PRIVATE ${SDL2_INCLUDE_DIRS})
  target_compile_options(teleport PRIVATE ${SDL2_CFLAGS_OTHER})
  target_link_libraries(teleport ${SDL2_LDFLAGS} ${SDL2_LIBRARIES})
  find_package(X11)
  if(X11_FOUND)
    target_include_directories(teleport PRIVATE ${X11_INCLUDE_DIR})
    target_link_libraries(teleport ${X11_LIBRARIES})
  endif()
endif()
