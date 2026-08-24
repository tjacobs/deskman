#!.venv/bin/python

# Imports
import sys
import numpy as np
import utils

# Config
MODEL_SIZE = 'base'
CHUNK_SECONDS = 3

# Main
def main():
    # Parse args
    parse_args()

    # Build the record command, quits when no microphone is available
    command = utils.record_command()

    # Silence onnxruntime GPU discovery warning from the VAD
    if not utils.import_onnxruntime_quietly():
        print('onnxruntime not found. Run ./install.sh --listen to install it.')
        sys.exit(1)

    # Load model and transcribe the microphone until stopped
    model = load_model()
    run_transcribe_loop(model, command)

# Parse command line arguments
def parse_args():
    for argument in sys.argv[1:]:
        if argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f"Unknown argument: {argument}")
            print_usage()
            sys.exit(1)

# Print usage help
def print_usage():
    print('Usage: ./speech_listen.py')
    print(f'  (no arg)  transcribe the microphone with the whisper {MODEL_SIZE} model, CTRL-C to stop')

# Load whisper model on gpu when available
def load_model():
    # Pick device
    print(f'Loading {MODEL_SIZE} model...', flush=True)
    import ctranslate2
    from faster_whisper import WhisperModel
    device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'float32'

    # Load model
    model = WhisperModel(MODEL_SIZE, device=device, compute_type=compute_type)
    label = 'GPU' if device == 'cuda' else device.upper()
    print(f'Whisper {MODEL_SIZE} on {label} {compute_type}, VAD on CPU')
    return model

# Record chunks from the microphone and print transcripts
def run_transcribe_loop(model, command):
    recorder = utils.start_recorder(command)
    chunk_bytes = utils.SAMPLE_RATE * 2 * CHUNK_SECONDS
    print('Listening, speak now, CTRL-C to stop.', flush=True)
    try:
        while True:
            # Read one chunk of raw audio
            data = recorder.stdout.read(chunk_bytes)
            if not data:
                break

            # Transcribe and print each spoken segment
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = model.transcribe(audio, language='en', vad_filter=True)
            for segment in segments:
                print(segment.text.strip(), flush=True)
    except KeyboardInterrupt:
        print('\nDone.')
    finally:
        recorder.terminate()

    # Suggest the full talk loop next
    print('Next: put it all together with ./talk.py.')

# Main
if __name__ == '__main__':
    main()
