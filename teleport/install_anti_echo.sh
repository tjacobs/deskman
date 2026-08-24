#!/bin/bash

# Build the AEC3 echo canceller. It is better than the jetson default decade old AEC.

# Exit on error
set -e

# Settings
WORK_DIR="$HOME/aec3"
LIBRARY_VERSION="1.3"
LIBRARY_URL="http://deb.debian.org/debian/pool/main/w/webrtc-audio-processing/webrtc-audio-processing_${LIBRARY_VERSION}.orig.tar.gz"
PLUGIN_TAG="1.24.0"
PLUGIN_URL="https://raw.githubusercontent.com/GStreamer/gstreamer/${PLUGIN_TAG}/subprojects/gst-plugins-bad/ext/webrtcdsp"
PLUGIN_DIR="/usr/local/lib/deskman-gstreamer-1.0"

# Only Linux has the distro GStreamer this works around
if [ "$(uname -s)" != "Linux" ]; then
    echo "This script only supports Linux"
    exit 1
fi

# Meson 0.63 or newer is required and distros often ship older, so use pip
echo "Installing build tools..."
pip3 install --user --quiet --upgrade meson ninja
export PATH="$HOME/.local/bin:$PATH"

# Build the new audio processing library, it carries its own abseil subproject
echo "Building webrtc-audio-processing $LIBRARY_VERSION..."
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
if [ ! -d "webrtc-audio-processing-$LIBRARY_VERSION" ]; then
    curl -fsSL -o library.tar.gz "$LIBRARY_URL"
    tar xf library.tar.gz
    rm library.tar.gz
fi
cd "webrtc-audio-processing-$LIBRARY_VERSION"
if [ ! -d build ]; then
    meson setup build --prefix=/usr/local --buildtype=release -Ddefault_library=shared
fi
ninja -C build

# Install the library, its soname differs from the old one so nothing is replaced
echo "Installing library..."
USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())')"
sudo env "PATH=$PATH" "PYTHONPATH=$USER_SITE" meson install -C build
sudo ldconfig

# Fetch the plugin sources, 1.24 is the first release ported to this library
echo "Fetching webrtcdsp $PLUGIN_TAG sources..."
mkdir -p "$WORK_DIR/webrtcdsp"
cd "$WORK_DIR/webrtcdsp"
for FILE in gstwebrtcdsp.cpp gstwebrtcdsp.h gstwebrtcdspplugin.cpp gstwebrtcechoprobe.cpp gstwebrtcechoprobe.h; do
    curl -fsSL -o "$FILE" "$PLUGIN_URL/$FILE"
done

# Stand in for the gst-plugins-bad build config the sources expect
cat > config.h <<'EOF'
#define VERSION "1.24.0"
#define PACKAGE "gst-plugins-bad"
#define PACKAGE_VERSION VERSION
#define GST_LICENSE "LGPL"
#define GST_PACKAGE_NAME "GStreamer Bad Plug-ins, AEC3 rebuild"
#define GST_PACKAGE_ORIGIN "https://gstreamer.freedesktop.org"
EOF

# Build the plugin against the system GStreamer so it stays loadable
cat > meson.build <<'EOF'
project('gst-webrtcdsp-aec3', 'cpp',
  version : '1.24.0',
  default_options : ['cpp_std=c++17', 'buildtype=release'])

gst_dep = dependency('gstreamer-1.0')
gstbase_dep = dependency('gstreamer-base-1.0')
gstaudio_dep = dependency('gstreamer-audio-1.0')
gstbadaudio_dep = dependency('gstreamer-bad-audio-1.0')
webrtc_dep = dependency('webrtc-audio-processing-1', version : '>= 1.0')

shared_library('gstwebrtcdsp',
  ['gstwebrtcdsp.cpp', 'gstwebrtcechoprobe.cpp', 'gstwebrtcdspplugin.cpp'],
  cpp_args : ['-DHAVE_CONFIG_H'],
  include_directories : include_directories('.'),
  dependencies : [gst_dep, gstbase_dep, gstaudio_dep, gstbadaudio_dep, webrtc_dep],
  name_prefix : 'lib',
  install : false)
EOF

# Compile the plugin
echo "Building webrtcdsp plugin..."
export PKG_CONFIG_PATH="/usr/local/lib/$(gcc -dumpmachine)/pkgconfig:$PKG_CONFIG_PATH"
if [ ! -d build ]; then
    meson setup build
fi
ninja -C build

# Install beside the system plugins, GST_PLUGIN_PATH makes this one win
echo "Installing plugin to $PLUGIN_DIR..."
sudo mkdir -p "$PLUGIN_DIR"
sudo cp build/libgstwebrtcdsp.so "$PLUGIN_DIR/"

# Confirm GStreamer picks the new plugin over the distro one
echo "Verifying..."
FOUND=$(GST_PLUGIN_PATH="$PLUGIN_DIR" gst-inspect-1.0 webrtcdsp | awk '/^  Version/ {print $2}')
if [ "$FOUND" != "$PLUGIN_TAG" ]; then
    echo "Plugin version is $FOUND, expected $PLUGIN_TAG"
    exit 1
fi
echo "AEC3 webrtcdsp $FOUND installed, teleport.service sets GST_PLUGIN_PATH to use it"
