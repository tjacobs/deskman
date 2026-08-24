# Robot

C++ face, STS3215 servos, and camera tracking. Voice is Python in `../talk`.

## Compile

Needs CMake, a C++20 compiler, OpenCV, SDL2, SDL2_image, SDL2_ttf, and GStreamer.

```bash
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
