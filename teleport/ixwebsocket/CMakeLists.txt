cmake_minimum_required(VERSION 3.7)

project(ixwebsocket)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(IXWEBSOCKET_ROOT ${CMAKE_CURRENT_LIST_DIR})
set(IXWEBSOCKET_DIR ${IXWEBSOCKET_ROOT}/ixwebsocket)

file(GLOB IXWEBSOCKET_SOURCES "${IXWEBSOCKET_DIR}/*.cpp")
list(REMOVE_ITEM IXWEBSOCKET_SOURCES
  ${IXWEBSOCKET_DIR}/IXSocketAppleSSL.cpp
  ${IXWEBSOCKET_DIR}/IXSocketMbedTLS.cpp
  ${IXWEBSOCKET_DIR}/IXSocketOpenSSL.cpp
)

add_library(ixwebsocket STATIC ${IXWEBSOCKET_SOURCES})
target_include_directories(ixwebsocket PUBLIC ${IXWEBSOCKET_ROOT})
target_compile_options(ixwebsocket PRIVATE -Wno-unused-variable -Wno-return-type -Wno-format)

set(CMAKE_THREAD_PREFER_PTHREAD TRUE)
set(THREADS_PREFER_PTHREAD_FLAG TRUE)
find_package(Threads REQUIRED)
target_link_libraries(ixwebsocket PUBLIC Threads::Threads)

find_library(ATOMIC_LIBRARIES NAMES atomic)
if (APPLE AND ATOMIC_LIBRARIES)
  target_link_libraries(ixwebsocket PUBLIC ${ATOMIC_LIBRARIES})
elseif (NOT APPLE)
  target_link_libraries(ixwebsocket PUBLIC atomic)
endif()
