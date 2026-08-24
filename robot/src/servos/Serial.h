#include <stdio.h>
#include <string>
#include <iostream>
#include <fcntl.h>
#include <unistd.h>
#include <cstring>
#include <cerrno>
#include <sys/ioctl.h>
#ifndef TCFLSH
#define TCFLSH 0x540B
#endif
#ifdef __linux__
#include <asm/termbits.h>
#else
#include <termios.h>
#endif
using namespace std;

// Jetson 40-pin UART, else Pi GPIO UART
// Servo bus baud, 1 Mbps is Feetech SMS/STS index 0, 115200 is index 4
const int SERVO_BAUD_RATE_1M = 1000000;
const int SERVO_BAUD_RATE_115200 = 115200;
const int SERVO_BAUD_RATE = SERVO_BAUD_RATE_1M;
constexpr char SERVO_PORT_JETSON[] = "/dev/ttyTHS1";
constexpr char SERVO_PORT_PI[] = "/dev/ttyAMA0";

// Pick the servo UART for this board
inline string servo_port_name() {
    if (access(SERVO_PORT_JETSON, F_OK) == 0) {
        return SERVO_PORT_JETSON;
    }
    return SERVO_PORT_PI;
}

class SerialPort {
private:
    int serial_fd;
    string port_name;

public:
    SerialPort(const string &port) : port_name(port), serial_fd(-1) {}

    ~SerialPort() {
        if (serial_fd != -1) {
            close(serial_fd);
        }
    }

    bool configurePort(int serial_fd, int baud_rate) {
#ifdef __linux__
        struct termios2 options;
        if (ioctl(serial_fd, TCGETS2, &options) != 0) {
            cerr << "Failed to get serial port attributes: " << strerror(errno) << endl;
            return false;
        }

        // Set baud rate, including 1000000
        options.c_cflag &= ~CBAUD;
        options.c_cflag |= BOTHER;
        options.c_ispeed = baud_rate;
        options.c_ospeed = baud_rate;
#else
        struct termios options;
        if (tcgetattr(serial_fd, &options) != 0) {
            cerr << "Failed to get serial port attributes: " << strerror(errno) << endl;
            return false;
        }
        if (cfsetspeed(&options, B115200) != 0) {
            cerr << "Failed to set BAUD_RATE" << endl;
            return false;
        }
#endif

        // Configure 8N1
        options.c_cflag &= ~PARENB;
        options.c_cflag &= ~CSTOPB;
        options.c_cflag &= ~CSIZE;
        options.c_cflag |= CS8;

        // Enable the receiver and set local mode
        options.c_cflag |= (CLOCAL | CREAD);

        // Disable hardware flow control
        options.c_cflag &= ~CRTSCTS;

        // Raw input/output mode
        options.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
        options.c_iflag &= ~(IXON | IXOFF | IXANY);
        options.c_oflag &= ~OPOST;

#ifdef __linux__
        if (ioctl(serial_fd, TCSETS2, &options) != 0) {
#else
        if (tcsetattr(serial_fd, TCSANOW, &options) != 0) {
#endif
            cerr << "Failed to set serial port attributes: " << strerror(errno) << endl;
            return false;
        }
        return true;
    }

    // Change UART speed on an open port
    bool setBaudRate(int baud_rate) {
        if (serial_fd == -1) return false;
        if (!configurePort(serial_fd, baud_rate)) return false;
#ifdef __linux__
        ioctl(serial_fd, TCFLSH, 2);
#endif
        return true;
    }

    bool openPort() {
        if (serial_fd != -1) {
            return true;
        }

        serial_fd = open(port_name.c_str(), O_RDWR | O_NOCTTY | O_NDELAY);
        if (serial_fd == -1) {
            #ifndef __APPLE__
            cerr << "Failed to open serial port " << port_name << endl;
            #endif
            return false;
        }

        if (configurePort(serial_fd, SERVO_BAUD_RATE)) {
            //cout << "Serial port configured successfully with baud rate: " << SERVO_BAUD_RATE << endl;
        } else {
            cerr << "Failed to configure serial port." << endl;
            close(serial_fd);
            return false;
        }

        return true;
    }

    int readIn() {
        // Check if serial port is valid
        if (serial_fd == -1) {
            return -1;
        }

        // Use select to check if data is available
        fd_set read_fds;
        struct timeval timeout;

        FD_ZERO(&read_fds);
        FD_SET(serial_fd, &read_fds);

        // Set timeout to 0 for non-blocking check
        timeout.tv_sec = 0;
        timeout.tv_usec = 0;

        int result = select(serial_fd + 1, &read_fds, nullptr, nullptr, &timeout);
        if (result > 0 && FD_ISSET(serial_fd, &read_fds)) {
            unsigned char byte;
            int bytes_read = read(serial_fd, &byte, 1);

            if (bytes_read == 1) {
                return static_cast<int>(byte);
            }
        }

        // No data available or an error occurred
        return -1;
    }

    int writeOut(unsigned char *nDat, int nLen) {
        //printf("Writing: %d bytes.\n", nLen);
        string data(reinterpret_cast<char*>(nDat), nLen);
        writeData(data);
        return nLen;
    }

    bool writeData(const string &data) {
        if (serial_fd == -1) {
            cerr << "Serial port not open." << endl;
            return false;
        }

        int bytes_written = write(serial_fd, data.c_str(), data.length());
        if (bytes_written < 0) {
            cerr << "Failed to write to serial port." << endl;
            return false;
        }

        return true;
    }

    string readData(size_t max_length = 256) {
        if (serial_fd == -1) {
            cerr << "Serial port not open." << endl;
            return "";
        }

        char buffer[max_length];
        memset(buffer, 0, max_length);

        int bytes_read = read(serial_fd, buffer, max_length - 1);
        if (bytes_read < 0) {
            cerr << "Failed to read from serial port." << endl;
            return "";
        }

        return string(buffer, bytes_read);
    }
};
