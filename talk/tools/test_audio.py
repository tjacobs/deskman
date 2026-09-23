#!/usr/bin/env python3

# Speaker and mic check, chimes into the mic, records you talking, plays it back

# Imports, the ones needing the venv wait until the venv interpreter is running
import os
import sys

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TALK_DIR = os.path.dirname(SCRIPT_DIR)
VENV_DIR = os.path.join(TALK_DIR, '.venv')
VENV_PYTHON = os.path.join(VENV_DIR, 'bin', 'python')

# Hand over to the venv interpreter, so this runs from any directory and any python
if os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR):
    if not os.path.isfile(VENV_PYTHON):
        sys.exit(f'No venv at {VENV_PYTHON}, run ./install.sh from {TALK_DIR}')
    os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])

# Imports that live in the venv
import wave
import time
import subprocess
import numpy
import soundfile

# Config the chime, a rising C major arpeggio that decays like a music box
CHIME_NOTES_HZ = (523.25, 659.25, 783.99, 1046.50)
CHIME_NOTE_SPACING_SECONDS = 0.16
CHIME_SECONDS = 1.6
CHIME_AMPLITUDE = 0.18
CHIME_DECAY_PER_SECOND = 3.5

# Config the second partial, a quiet octave above each note warms the timbre
CHIME_PARTIAL_GAIN = 0.25

# The chime plays inside this opening slice of the recording, then the talking starts
CHIME_WINDOW_SECONDS = 2.0

# How wide either side of a note counts as that note when looking for the chime
CHIME_NOTE_BAND_HZ = 25

# The chime counts as heard when its notes hold this much of the window energy,
# room noise alone lands near a twentieth of this and the chime well above it
CHIME_SHARE_MINIMUM = 0.10

# Pass this instead of a chime path to meter without playing anything
NO_CHIME = ''

# Config the recording, the chime window plus the talking make up the length
SPEECH_SECONDS = 4
RECORD_SECONDS = 6
RECORD_RATE_HZ = 48000
RECORD_FORMAT = 'S16_LE'

# Channels to record when the card does not say what it has
CHANNELS_FALLBACK = 1

# Pass this as the duration to record until stopped
RECORD_UNLIMITED = 0

# Wait this much longer than the recording before giving up on arecord
RECORD_GRACE_SECONDS = 5

# Count down before recording so the person is ready
COUNTDOWN_SECONDS = 3

# Config the live meter, one bar redraw per block of samples read
METER_WIDTH = 40
METER_FLOOR_DBFS = -60.0
METER_BLOCK_SECONDS = 0.05
METER_FILLED = '#'
METER_EMPTY = '-'

# Mark the loudest level so far, so a short word leaves a trace on the bar
METER_HOLD = '|'

# Names for the bars, a mono card has one and a stereo card has two
CHANNEL_LABELS_MONO = ('mic',)
CHANNEL_LABELS_STEREO = ('L', 'R')

# Bytes per sample in the raw stream, S16_LE is two
SAMPLE_BYTES = 2

# Full scale for signed 16 bit samples
SAMPLE_FULL_SCALE = 32768.0

# A recording peaking under this heard nothing but its own noise floor
SILENCE_DBFS = -50.0

# A recording peaking over this is close to clipping
LOUD_DBFS = -1.0

# Speech rises and falls, a mic hissing into an empty jack holds one flat level
LEVEL_BLOCK_SECONDS = 0.2
LEVEL_SWING_MINIMUM_DB = 6.0

# Speech energy lives in this band, hiss piles up above it
SPEECH_BAND_HZ = (100, 4000)
SPEECH_SHARE_MINIMUM = 0.3

# Colour the verdicts the same way test.py does, NO_COLOR leaves the text plain for the robot log
GREEN = '' if os.environ.get('NO_COLOR') else '\033[92m'
RED = '' if os.environ.get('NO_COLOR') else '\033[91m'
RESET = '' if os.environ.get('NO_COLOR') else '\033[0m'

# Put talk/ on the import path, utils already knows how to find the cards
sys.path.insert(0, TALK_DIR)
import utils

# Main
def main():
    # Parse args
    meter_mode = parse_args()

    # Find the cards, there is nothing to test without a mic
    microphone_card, channels = find_devices()

    # Just show the meter when that is all that was asked for
    if meter_mode:
        print('Live mic level, CTRL-C to stop', flush=True)
        run_meter(microphone_card, channels, RECORD_UNLIMITED, NO_CHIME)
        return

    # Record the chime and then the talking, both land in one file
    chime_path = write_chime()
    capture_path, chime_played = record_wav(microphone_card, channels, chime_path)

    # Check the file arrived whole before trusting anything in it
    results = [('speaker plays the chime', chime_played)]
    results.append(('recording is a valid wav', check_file(capture_path)))

    # Split the recording into the chime part and the talking part
    samples = read_samples(capture_path)
    chime_frames = int(RECORD_RATE_HZ * CHIME_WINDOW_SECONDS)

    # The chime proves the speaker made real sound and the mic picked it up
    results.append(('mic heard the chime', check_chime(samples, chime_frames)))

    # The rest is the voice, checked for level and then for sounding like speech
    speech = None if samples is None else samples[chime_frames:]
    results.append(('mic heard your voice', check_level(speech)))
    results.append(('recording sounds like speech', check_speech(speech)))

    # Play it back so the speaker and the recording are both confirmed by ear
    print('Playing your recording back...', flush=True)
    results.append(('recording plays back', play_wav(capture_path)))

    # Report and fail the exit code when anything did not pass
    print_summary(results, capture_path)

# Parse command line arguments
def parse_args():
    meter_mode = False
    for argument in sys.argv[1:]:
        if argument == '--meter':
            meter_mode = True
        elif argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f'Unknown argument: {argument}', flush=True)
            print_usage()
            sys.exit(1)
    return meter_mode

# Print usage help
def print_usage():
    print(f'Usage: {os.path.basename(__file__)} [--meter]', flush=True)
    print('  --meter   just show the live mic level until CTRL-C', flush=True)
    print('  (no arg)  chime into the mic, record you talking, play it back', flush=True)

# Report the cards in use, exit when there is no mic or no player
def find_devices():
    # The player has to exist, everything here plays something
    player_ok, player_error = utils.check_audio_player()
    if not player_ok:
        print(f'No audio playback: {player_error}', flush=True)
        sys.exit(1)

    # The mic card is the one utils hands to listen and talk
    microphone_card = utils.find_capture_card()
    if microphone_card is None:
        print('No microphone found. Plug in a USB mic and try again.', flush=True)
        sys.exit(1)

    # Say what is being tested, the card names make a wrong device obvious
    channels = find_capture_channels(microphone_card)
    speaker_card = utils.find_usb_card()
    print(f'Speaker: card {speaker_card} using {utils.audio_player()}', flush=True)
    print(f'Mic:     card {microphone_card} at {RECORD_RATE_HZ} Hz, {describe_channels(channels)}', flush=True)
    return microphone_card, channels

# Channels the capture hardware really has, so a mono mic is not metered as stereo
def find_capture_channels(card):
    stream_path = f'/proc/asound/card{card}/stream0'
    if not os.path.isfile(stream_path):
        return CHANNELS_FALLBACK

    # Read past the playback section, that one often has more channels than capture
    with open(stream_path) as stream_file:
        capture = stream_file.read().partition('Capture:')[2]

    # First channel count in the capture section is what the hardware offers
    for line in capture.splitlines():
        if 'Channels:' in line:
            return int(line.split(':')[1].strip())
    return CHANNELS_FALLBACK

# Name a channel count the way a person would say it
def describe_channels(channels):
    if channels == 1:
        return 'mono'
    if channels == 2:
        return 'stereo'
    return f'{channels} channels'

# Write the chime wav, returns its path
def write_chime():
    os.makedirs(utils.AUDIO_DIR, exist_ok=True)
    chime_path = os.path.join(utils.AUDIO_DIR, 'test_chime.wav')

    # Lay each note onto one buffer, staggered so they ring together
    samples = numpy.arange(int(RECORD_RATE_HZ * CHIME_SECONDS)) / RECORD_RATE_HZ
    chime = numpy.zeros_like(samples)
    for index, frequency in enumerate(CHIME_NOTES_HZ):
        chime += struck_note(samples, frequency, index * CHIME_NOTE_SPACING_SECONDS)

    # Scale to the target level, the notes overlap so the sum needs normalising
    peak = float(numpy.max(numpy.abs(chime)))
    if peak > 0:
        chime = chime / peak * CHIME_AMPLITUDE
    soundfile.write(chime_path, chime.astype('float32'), RECORD_RATE_HZ)
    return chime_path

# One note that starts at a delay then rings away to nothing
def struck_note(samples, frequency, delay_seconds):
    # Silence before the note is struck
    elapsed = samples - delay_seconds
    sounding = elapsed >= 0
    elapsed = numpy.where(sounding, elapsed, 0.0)

    # Fade in over a few milliseconds so the strike does not click
    attack = numpy.minimum(1.0, elapsed * 200)

    # Ring down, plus a quiet octave that decays faster like a real struck note
    decay = numpy.exp(-elapsed * CHIME_DECAY_PER_SECOND)
    tone = numpy.sin(2 * numpy.pi * frequency * elapsed)
    partial = CHIME_PARTIAL_GAIN * numpy.sin(4 * numpy.pi * frequency * elapsed) * numpy.exp(-elapsed * CHIME_DECAY_PER_SECOND * 2)
    return numpy.where(sounding, (tone + partial) * decay * attack, 0.0)

# Record the chime then the talking behind a live meter, returns the path and chime result
def record_wav(microphone_card, channels, chime_path):
    capture_path = os.path.join(utils.AUDIO_DIR, 'test_capture.wav')

    # Count down first, recording the moment the command runs catches nobody ready
    print(f'Listen for the chime, then talk for {SPEECH_SECONDS} seconds', flush=True)
    for remaining in range(COUNTDOWN_SECONDS, 0, -1):
        print(f'  {remaining}...', flush=True)
        time.sleep(1)

    # Stream the audio past the meter and keep it, one recorder feeds both
    blocks, chime_played = run_meter(microphone_card, channels, RECORD_SECONDS, chime_path)

    # Write what the meter just showed, the checks below read it back
    if not blocks:
        print('  the recorder produced nothing', flush=True)
        return capture_path, chime_played
    soundfile.write(capture_path, numpy.concatenate(blocks), RECORD_RATE_HZ)
    return capture_path, chime_played

# Draw a live level bar while recording, returns the blocks read and the chime result
def run_meter(microphone_card, channels, seconds, chime_path):
    # Read raw frames rather than a wav, so levels can be shown as they arrive
    command = record_command(microphone_card, channels, seconds)
    recorder = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Start the chime straight away, it has to land inside the opening window
    chime = None
    if chime_path:
        chime = subprocess.Popen(utils.play_wav_command(chime_path), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # One chunk holds this many frames of every channel
    frames = int(RECORD_RATE_HZ * METER_BLOCK_SECONDS)
    chunk_bytes = frames * channels * SAMPLE_BYTES

    # Hold the loudest level per channel so a short word leaves a mark
    held = [METER_FLOOR_DBFS] * channels
    blocks = []
    try:
        while True:
            # An empty read means the recorder stopped
            raw = read_exact(recorder.stdout, chunk_bytes)
            if not raw:
                break

            # Split the interleaved frames back into channels
            samples = numpy.frombuffer(raw, dtype='<i2').astype(numpy.float32) / SAMPLE_FULL_SCALE
            samples = samples.reshape(-1, channels)
            blocks.append(samples)

            # Redraw the bars in place, one per channel
            levels = [to_dbfs(float(numpy.max(numpy.abs(samples[:, index])))) for index in range(channels)]
            held = [max(hold, level) for hold, level in zip(held, levels)]
            sys.stdout.write('\r' + format_meter(levels, held))
            sys.stdout.flush()

    # CTRL-C is how the unlimited meter is meant to end
    except KeyboardInterrupt:
        pass

    # Leave the last bar on screen, then stop the recorder
    sys.stdout.write('\n')
    sys.stdout.flush()
    report_recorder_error(recorder)
    return blocks, chime_result(chime)

# Build the recorder command, plughw lets ALSA convert when the card wants another rate
def record_command(microphone_card, channels, seconds):
    command = [utils.LINUX_RECORDER, '-D', f'plughw:{microphone_card},0', '-f', RECORD_FORMAT, '-r', str(RECORD_RATE_HZ), '-c', str(channels), '-t', 'raw', '-q']

    # Leave the duration off to record until stopped
    if seconds != RECORD_UNLIMITED:
        command += ['-d', str(seconds)]
    return command

# Read exactly this many bytes, empty when the stream ends first
def read_exact(stream, wanted):
    chunk = b''
    while len(chunk) < wanted:
        piece = stream.read(wanted - len(chunk))
        if not piece:
            return b''
        chunk += piece
    return chunk

# Build the meter line, one labelled bar per channel
def format_meter(levels, held):
    labels = CHANNEL_LABELS_STEREO if len(levels) == 2 else CHANNEL_LABELS_MONO
    bars = []
    for index, level in enumerate(levels):
        label = labels[index] if index < len(labels) else str(index + 1)
        bars.append(f'{label} {level:6.1f} dBFS [{format_bar(level, held[index])}]')
    return '  '.join(bars)

# Fill a bar up to the level, marking the loudest seen so far
def format_bar(level, hold):
    filled = scale_to_bar(level)
    marker = scale_to_bar(hold)
    cells = []
    for index in range(METER_WIDTH):
        if index < filled:
            cells.append(METER_FILLED)
        elif index == marker:
            cells.append(METER_HOLD)
        else:
            cells.append(METER_EMPTY)
    return ''.join(cells)

# Map a level in dBFS onto a position along the bar
def scale_to_bar(level):
    share = (level - METER_FLOOR_DBFS) / (0.0 - METER_FLOOR_DBFS)
    return int(max(0.0, min(1.0, share)) * METER_WIDTH)

# Stop the recorder and say why it quit when it failed
def report_recorder_error(recorder):
    # Stop it if it is still going, the unlimited meter always is
    if recorder.poll() is None:
        recorder.terminate()
        try:
            recorder.wait(timeout=RECORD_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            recorder.kill()

    # A bad device or rate lands here, the message explains the empty recording
    errors = recorder.stderr.read().decode(errors='replace').strip()
    if errors:
        print(f'  {utils.LINUX_RECORDER}: {errors.splitlines()[-1]}', flush=True)

# Whether the chime played cleanly, true when no chime was asked for
def chime_result(chime):
    if chime is None:
        return True

    # It is far shorter than the recording, so it has already finished
    chime.wait(timeout=RECORD_GRACE_SECONDS)
    if chime.returncode == 0:
        return True

    # Show why, a busy or missing device is the usual reason
    errors = chime.stderr.read().decode(errors='replace').strip()
    print(f'  {utils.audio_player()} failed: {errors.splitlines()[-1] if errors else "no output"}', flush=True)
    return False

# Play a wav and report whether the player was happy
def play_wav(wav_path):
    command = utils.play_wav_command(wav_path)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return True

    # Show why, a busy or missing device is the usual reason
    message = result.stderr.strip().splitlines()
    print(f'  {utils.audio_player()} failed: {message[-1] if message else "no output"}', flush=True)
    return False

# Check the recording is a wav with frames in it
def check_file(wav_path):
    # A missing or empty file means arecord never wrote anything
    if not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
        print('  no recording was written', flush=True)
        return False

    # Read the header, a truncated file raises here
    try:
        with wave.open(wav_path) as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
    except (wave.Error, EOFError) as error:
        print(f'  not a readable wav: {error}', flush=True)
        return False

    # No frames is a clean file with no audio, worth its own message
    if frames == 0:
        print('  wav has no audio frames', flush=True)
        return False

    # Short of the length asked for means the recorder was cut off
    if frames < int(rate * RECORD_SECONDS):
        print(f'  only {frames / rate:.1f}s of the {RECORD_SECONDS}s recorded, {describe_channels(channels)} at {rate} Hz', flush=True)
        return False
    return True

# Read a wav as mono floats, None when it cannot be read
def read_samples(wav_path):
    try:
        samples, rate = soundfile.read(wav_path)
    except Exception as error:
        print(f'  could not read the recording: {error}', flush=True)
        return None

    # Mix any stereo down, the checks do not care about the sides
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return samples

# Check the chime notes came back through the mic
def check_chime(samples, chime_frames):
    # The opening window is where the chime was played
    if samples is None or len(samples) < chime_frames:
        print('  no chime window to check', flush=True)
        return False
    window = samples[:chime_frames]

    # Energy per frequency across that window
    spectrum = numpy.abs(numpy.fft.rfft(window))
    frequencies = numpy.fft.rfftfreq(len(window), 1.0 / RECORD_RATE_HZ)
    total = float(numpy.sum(spectrum))
    if total == 0:
        print('  chime window is all zeroes', flush=True)
        return False

    # Share of it sitting on the chime notes
    share = 0.0
    for note in CHIME_NOTES_HZ:
        share += float(numpy.sum(spectrum[numpy.abs(frequencies - note) <= CHIME_NOTE_BAND_HZ]))
    share = share / total
    peak = to_dbfs(float(numpy.max(numpy.abs(window))))

    # A quiet window never held the chime, whatever its notes measure
    if peak < SILENCE_DBFS:
        print(f'  the chime window is silent at {peak:.1f} dBFS, check the speaker volume', flush=True)
        return False

    # Nothing on the notes means the speaker or the mic is not doing its job
    if share < CHIME_SHARE_MINIMUM:
        print(f'  chime notes only {share * 100:.1f}% of the window, check the speaker volume and the mic', flush=True)
        return False
    return True

# Check the recording is louder than a noise floor and not clipping
def check_level(samples):
    if samples is None or len(samples) == 0:
        print('  nothing recorded to measure', flush=True)
        return False

    # Anything this quiet is the mic hearing itself, not the room
    peak = to_dbfs(float(numpy.max(numpy.abs(samples))))
    if peak < SILENCE_DBFS:
        print(f'  peak {peak:.1f} dBFS is silence, nothing reached the mic', flush=True)
        print('  check the mic is in the pink jack and not muted in alsamixer', flush=True)
        return False

    # Warn about a level that will distort, still a working mic
    if peak > LOUD_DBFS:
        print('  close to clipping, turn the mic gain down in alsamixer', flush=True)
    return True

# Check the recording moves and sits in the speech band, so it is a voice not hiss
def check_speech(samples):
    if samples is None or len(samples) == 0:
        print('  nothing recorded to judge', flush=True)
        return False

    # Level of each short block, speech swings between words and hiss does not
    block = int(RECORD_RATE_HZ * LEVEL_BLOCK_SECONDS)
    levels = []
    for start in range(0, len(samples) - block, block):
        levels.append(to_dbfs(float(numpy.max(numpy.abs(samples[start:start + block])))))
    if not levels:
        print('  recording too short to judge', flush=True)
        return False

    # Share of energy in the speech band against the hiss above it
    spectrum = numpy.abs(numpy.fft.rfft(samples))
    frequencies = numpy.fft.rfftfreq(len(samples), 1.0 / RECORD_RATE_HZ)
    total = float(numpy.sum(spectrum))
    if total == 0:
        print('  recording is all zeroes', flush=True)
        return False
    speech_band = (frequencies >= SPEECH_BAND_HZ[0]) & (frequencies < SPEECH_BAND_HZ[1])
    speech_share = float(numpy.sum(spectrum[speech_band])) / total

    # A flat level means nothing was ever said into it
    swing = max(levels) - min(levels)
    if swing < LEVEL_SWING_MINIMUM_DB:
        print(f'  the level never moved, only {swing:.1f} dB of swing, that is hiss not a voice', flush=True)
        return False

    # Energy piled up above the speech band is a noisy or unplugged mic
    if speech_share < SPEECH_SHARE_MINIMUM:
        print(f'  only {speech_share * 100:.0f}% of energy where speech lives, check the mic jack and gain', flush=True)
        return False
    return True

# Convert an amplitude to dBFS, guarding the log against zero
def to_dbfs(amplitude):
    return 20.0 * numpy.log10(max(amplitude, 1e-9))

# Print the pass or fail lines and exit non zero when something failed
def print_summary(results, capture_path):
    print('', flush=True)
    for name, passed in results:
        if passed:
            print(f'{GREEN}PASS{RESET}  {name}', flush=True)
        else:
            print(f'{RED}FAIL{RESET}  {name}', flush=True)

    # Point at the recording so it can be listened to again
    print('', flush=True)
    print(f'Recording kept at {capture_path}', flush=True)

    # One line verdict, then the exit code for scripts
    if all(passed for name, passed in results):
        print(f'{GREEN}Speaker and mic are working.{RESET}', flush=True)
        return
    print(f'{RED}Something is not working, see the FAIL lines above.{RESET}', flush=True)
    sys.exit(1)

# Main
if __name__ == '__main__':
    main()
