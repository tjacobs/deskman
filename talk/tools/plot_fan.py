#!/usr/bin/env -S uv run --quiet --script
# /// script
# dependencies = ["matplotlib"]
# ///

# Samples the Jetson CPU temperature and fan speed, then plots them against the fan off point.

# Imports
import os
import csv
import time
import argparse
import warnings

# Config the sampling
SAMPLE_SECONDS = 60
SAMPLE_INTERVAL_SECONDS = 2

# Config where samples and the plot go, beside this script
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLES_PATH = os.path.join(TOOLS_DIR, 'fan_samples.csv')
PLOT_PATH = os.path.join(TOOLS_DIR, 'plot_fan_output.png')

# Config the sysfs devices, matched by name as the numbers move between boots
HWMON_ROOT = '/sys/class/hwmon'
THERMAL_ROOT = '/sys/class/thermal'
FAN_PWM_NAME = 'pwmfan'
FAN_TACH_NAME = 'pwm_tach'
CPU_THERMAL_NAME = 'cpu-thermal'
MILLI_PER_C = 1000

# Config the fan profile the off point is read from
FAN_CONFIG = '/etc/nvfancontrol.conf'
FAN_PROFILE_NAME = 'deskman'

# Config the plot
PLOT_SIZE = (9, 4.5)
PLOT_DPI = 110
TEMPERATURE_RANGE = (30, 80)
RPM_RANGE = (0, 6000)

# Sample then plot, or replot saved samples
def main():
    # Parse arguments
    arguments = parse_args()

    # Sample unless replotting saved samples
    if not arguments.load:
        samples = take_samples(arguments.seconds, arguments.interval)
        save_samples(samples, SAMPLES_PATH)
    else:
        samples = load_samples(arguments.load)

    # Plot them
    draw_plot(samples, fan_off_temperature(), arguments.save)
    print(f'Saved {arguments.save}', flush=True)

# Parse command line arguments
def parse_args():
    parser = argparse.ArgumentParser(description='Plot CPU temperature and fan speed over time.')
    parser.add_argument('--seconds', type=int, default=SAMPLE_SECONDS, help='how long to sample')
    parser.add_argument('--interval', type=float, default=SAMPLE_INTERVAL_SECONDS, help='seconds between samples')
    parser.add_argument('--load', help='replot samples from this csv instead of sampling')
    parser.add_argument('--save', default=PLOT_PATH, help='where to write the plot')
    return parser.parse_args()

# Read temperature, fan duty, and fan speed every interval, printing each line
def take_samples(seconds, interval):
    # Find the sensors
    temperature_path = find_thermal_path(CPU_THERMAL_NAME)
    duty_path = find_hwmon_path(FAN_PWM_NAME, 'pwm1')
    rpm_path = find_hwmon_path(FAN_TACH_NAME, 'rpm')

    # Sample until the time is up
    print(f'Sampling for {seconds} seconds...', flush=True)
    samples = []
    start = time.time()
    while time.time() - start <= seconds:
        sample = (round(time.time() - start), read_int(temperature_path) / MILLI_PER_C, read_int(duty_path), read_int(rpm_path))
        print(f'{sample[0]}s {sample[1]:.1f}C duty {sample[2]} rpm {sample[3]}', flush=True)
        samples.append(sample)
        time.sleep(interval)
    return samples

# Find a thermal zone file by its type name
def find_thermal_path(name):
    for zone in sorted(os.listdir(THERMAL_ROOT)):
        if read_text(os.path.join(THERMAL_ROOT, zone, 'type')) == name:
            return os.path.join(THERMAL_ROOT, zone, 'temp')
    raise SystemExit(f'No {name} thermal zone.')

# Find a file in the hwmon device with this name
def find_hwmon_path(name, file_name):
    for device in sorted(os.listdir(HWMON_ROOT)):
        if read_text(os.path.join(HWMON_ROOT, device, 'name')) == name:
            return os.path.join(HWMON_ROOT, device, file_name)
    raise SystemExit(f'No {name} hwmon device.')

# Write the samples so they can be replotted with --load
def save_samples(samples, save):
    with open(save, 'w', newline='') as samples_file:
        csv.writer(samples_file).writerows(samples)

# Read samples written by save_samples
def load_samples(load):
    with open(load) as samples_file:
        return [(int(row[0]), float(row[1]), int(row[2]), int(row[3])) for row in csv.reader(samples_file)]

# Work out the temperature below which our fan profile stops the fan, zero when there is no profile
def fan_off_temperature():
    # Read the config, a board without one has no off point to show
    try:
        with open(FAN_CONFIG) as config_file:
            lines = config_file.read().splitlines()
    except OSError:
        return 0

    # Take the group maximum, profiles count degrees under it
    maximum = next((int(line.split()[1]) for line in lines if line.strip().startswith('GROUP_MAX_TEMP')), 0)

    # Find the first row of our profile where the fan is off, its margin is the off point
    in_profile = False
    for line in lines:
        words = line.split()
        if line.strip().startswith(f'FAN_PROFILE {FAN_PROFILE_NAME} '):
            in_profile = True
        elif in_profile and words and words[0] == '}':
            break
        elif in_profile and len(words) == 4 and words[0].isdigit() and words[2] == '0':
            return maximum - int(words[0])
    return 0

# Draw temperature on the left axis and fan speed on the right
def draw_plot(samples, off_temperature, save):
    # Import here, so sampling works even where matplotlib is missing
    warnings.filterwarnings('ignore', message='Unable to import Axes3D')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as pyplot

    # Split the samples into lines
    seconds = [sample[0] for sample in samples]
    temperatures = [sample[1] for sample in samples]
    rpms = [sample[3] for sample in samples]

    # Draw temperature and the fan off point
    figure, temperature_axis = pyplot.subplots(figsize=PLOT_SIZE)
    temperature_axis.plot(seconds, temperatures, color='tab:red', marker='o', markersize=3, label='CPU temperature')
    if off_temperature:
        temperature_axis.axhline(off_temperature, color='tab:red', linestyle='--', alpha=0.5, label=f'Fan off below {off_temperature}C')
    temperature_axis.set_xlabel('Seconds')
    temperature_axis.set_ylabel('CPU temperature, C', color='tab:red')
    temperature_axis.set_ylim(*TEMPERATURE_RANGE)

    # Put fan speed on its own axis
    fan_axis = temperature_axis.twinx()
    fan_axis.plot(seconds, rpms, color='tab:blue', alpha=0.6, label='Fan RPM')
    fan_axis.set_ylabel('Fan RPM', color='tab:blue')
    fan_axis.set_ylim(*RPM_RANGE)

    # Title and save
    temperature_axis.set_title(f'Deskman CPU temperature and fan, {seconds[-1]} seconds')
    figure.legend(loc='upper right', bbox_to_anchor=(0.88, 0.88))
    figure.tight_layout()
    figure.savefig(save, dpi=PLOT_DPI)

# Read one integer from a sysfs file
def read_int(path):
    return int(read_text(path))

# Read a small text file, empty when it cannot be read
def read_text(path):
    try:
        with open(path) as text_file:
            return text_file.read().strip()
    except OSError:
        return ''

# Run
if __name__ == '__main__':
    main()
