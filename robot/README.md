# Robot

C++ face, STS3215 servos, and camera tracking. Voice is Python in `../talk`.

## Compile

Needs CMake, a C++20 compiler, OpenCV, SDL2, SDL2_image, SDL2_ttf, and GStreamer.

```bash
./install.sh
mkdir -p build
cd build
cmake ..
make
```

## Run

```bash
./robot
```

Starts the face window, servos, camera tracking, and `talk/talk.py`. `./robot --help` lists flags. `--no-talk` is face and neck only.

## Service

```bash
./install_robot_service.sh
./install_robot_service.sh --start
./install_robot_service.sh --uninstall
```

## Screen

Waveshare DSI touchscreen, driver board silkscreen `Capacitive Touch Screen Rev2.1`, controller chip `WSYTH03`. One board serves the 7, 8, and 10.1 inch panels, so the silkscreen size is not the panel size.

The panel is 1280x800, even on the 7 inch. Use the `8_0_inch` parameter, it is the only 1280x800 timing in the overlay. The `7_0_inchC`, `7_0_inchH`, and every `vc4-kms-dsi-waveshare-panel-v2` variant leave the panel dark.

Connect the DSI ribbon to CAM/DISP 1, leave the board I2C DIP switch on I2C0, and power the board over its USB-C socket. The Pi needs a 5A supply, a weak one browns the panel out before it initialises.

Add to `/boot/firmware/config.txt`, then reboot:

```
[all]
# Waveshare Rev2.1 driver board, 7 inch 1280x800 panel, I2C0, CAM/DISP 1
dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch
```

Touch needs no setup, Goodix GT9271 binds at I2C address 0x14 on bus 11.

## Screen backlight

The kernel backlight device at `/sys/class/backlight/11-0045` is not wired to the hardware, writing `brightness` does nothing. Drive the panel MCU on I2C bus 11, address 0x45, directly instead. Register 0x95 enables the LCD rails and 0x96 sets backlight PWM.

```bash
sudo i2cset -y -f 11 0x45 0x95 0x17
sudo i2cset -y -f 11 0x45 0x96 0xff
```

Run these by hand if a panel ever comes up dark.

## Servos

STS3215 serial bus servos on GPIO 14 (TX) and GPIO 15 (RX), 1 Mbps. Pi 5 leaves that UART off until `uart0-pi5` is loaded, which creates `/dev/ttyAMA0`.

Add to `/boot/firmware/config.txt`, then reboot:

```
[pi5]
# GPIO 14/15 UART for the STS3215 servo bus
dtoverlay=uart0-pi5
```

`install_system.sh` writes that overlay. To enable it without a reboot:

```bash
sudo dtoverlay uart0-pi5
```

Then ping and read positions, no moves:

```bash
cd build
./robot --no-servos
```

## Battery

Pack voltage and current come from an INA219 at I2C address `0x40` on the 40-pin header, GPIO 2 (SDA) and GPIO 3 (SCL). Pi OS leaves that bus off until:

```
dtparam=i2c_arm=on
```

is in `/boot/firmware/config.txt`. `install_system.sh` turns that on. Reboot after, then `/dev/i2c-1` should show the chip at `0x40`. Without the meter, talk no longer treats `0 V` as a low pack.
