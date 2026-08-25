# Teleport

Video calling. Logs into the Teleport websocket server and handles WebRTC signaling.

Web look commands move the Deskman head over `$XDG_RUNTIME_DIR/robot.interface`.

## Compile

Needs CMake, a C++ compiler, GStreamer WebRTC, json-glib, libnice, SDL2, and SDL2_ttf.

```bash
./install.sh
mkdir -p build
cd build
cmake ..
make
```

Optional anti-echo: `./install_anti_echo.sh`.

## Run

```bash
./teleport
```

`./teleport --help` lists flags. `--local` uses `ws://127.0.0.1:8080`. `--call teleport2` dials after login.

## Service

```bash
./install_teleport_service.sh
./install_teleport_service.sh --start
./install_teleport_service.sh --uninstall
```
