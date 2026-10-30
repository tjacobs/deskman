#!.venv/bin/python

# Realtime_openai.py streams microphone audio up to OpenAI and plays the spoken audio reply.

# Imports
import json
import time
import base64
import argparse
import threading
import websocket

# Imports from the shared voice helper, which also puts the text helper on the path
import voice
import client
import robot_move

# Config the session, config.json overrides these
REALTIME_MODEL = 'gpt-realtime-2.1-mini'
REALTIME_VOICE = 'echo'
REALTIME_ACCENT = ''

# Config the connection
REALTIME_URL = 'wss://api.openai.com/v1/realtime'
TURN_DETECTION = 'semantic_vad'

# Config the voices OpenAI speaks with and how each comes across, OpenAI publishes no gender so these are by ear
REALTIME_VOICES = {
    'cedar': 'male', 'echo': 'male',
    'ballad': 'female', 'coral': 'female', 'marin': 'female', 'shimmer': 'female',
    'alloy': 'neutral', 'ash': 'neutral', 'sage': 'neutral', 'verse': 'neutral'}

# Config example voice names given to the model when it picks one
VOICE_EXAMPLES = 'marin, cedar, or echo'

# Config the transcribers, the model hears the audio itself, these only write the heard lines to the log
LIVE_TRANSCRIPTION_MODEL = 'gpt-live-transcribe'
TURN_TRANSCRIPTION_MODEL = 'gpt-4o-mini-transcribe'

# Config which transcriber runs, live sends words during the sentence, the turn one waits for the end of it
TRANSCRIBE_LIVE = True

# Config whether the model's own words print as it speaks, the same live switch, it is not a second transcriber
PRINT_REPLY_LIVE = TRANSCRIBE_LIVE

# Config the timing
CONNECT_TIMEOUT = 30
APPEND_BYTES = 32000

# Config the rate of the audio sent up
REALTIME_RATE = 24000

# Hold one realtime conversation from the microphone
def main():
    # Parse arguments
    arguments = parse_args()

    # Load config
    config = load_config()

    # Load from the command line, overriding config.json
    if arguments.model:
        config['realtime_model'] = arguments.model
    if arguments.voice:
        config['realtime_voice'] = arguments.voice

    # Open the microphone before the socket, muted so connect time is not sent as speech
    microphone = voice.Microphone()
    microphone.mute()

    # Open the session
    session = open_session(config)

    # Talk until the room goes quiet
    try:
        # Run the conversation
        voice.run_conversation(session, microphone, arguments.ask, voice.ignore_turn, '')
    except KeyboardInterrupt:
        # Stop the conversation
        print('Stopped.', flush=True)
    finally:
        # Close the session
        close_session(session)
        microphone.stop()

# Parse command line arguments
def parse_args():
    # Describe the arguments
    parser = argparse.ArgumentParser(description='Talk to OpenAI with audio up and audio down.')
    parser.add_argument('ask', nargs='?', help='send this question first, instead of waiting for speech')
    parser.add_argument('--model', help='realtime model to use, overrides config.json')
    parser.add_argument('--voice', help='voice to speak with, overrides config.json')
    return parser.parse_args()

# Read config.json, falling back to the defaults above for anything missing
def load_config():
    # Load the settings with the defaults above
    return voice.load_config({'realtime_model': REALTIME_MODEL, 'realtime_voice': REALTIME_VOICE, 'realtime_accent': REALTIME_ACCENT})

# Connect to the realtime endpoint and configure the session
def open_session(config):
    # Get the API key
    key = client.cloud_api_key()
    if not key:
        raise SystemExit('No OpenAI key, set OPENAI_API_KEY or write talk/openai.env.')

    # Open the socket, the receive timeout lets the reply loop check the idle clock
    model = config['realtime_model']
    print('Connecting to OpenAI...', flush=True)
    socket = websocket.create_connection(f'{REALTIME_URL}?model={model}', header=[f'Authorization: Bearer {key}'], timeout=CONNECT_TIMEOUT)
    socket.settimeout(voice.RECEIVE_TIMEOUT)
    session = OpenAISession(socket)
    session.config = config
    print('Connected.', flush=True)

    # Describe the voice, the turn taking, and the tools before any audio moves
    audio_input = {'format': {'type': 'audio/pcm', 'rate': REALTIME_RATE}, 'turn_detection': {'type': TURN_DETECTION}, 'transcription': {'model': transcription_model()}}
    audio_output = {'format': {'type': 'audio/pcm', 'rate': voice.OUTPUT_RATE}, 'voice': config['realtime_voice']}
    instructions = voice.session_instructions(config['realtime_accent'])
    update = {'type': 'realtime', 'output_modalities': ['audio'], 'audio': {'input': audio_input, 'output': audio_output}, 'instructions': instructions, 'tools': session_tools(), 'tool_choice': 'auto'}
    session.send({'type': 'session.update', 'session': update})
    return session

# Name the transcriber to write the heard lines with
def transcription_model():
    # Pick the live or the end of turn transcriber
    if TRANSCRIBE_LIVE:
        return LIVE_TRANSCRIPTION_MODEL
    return TURN_TRANSCRIPTION_MODEL

# Wrap the shared tool list in the shape a realtime session expects
def session_tools():
    # Add the function type to each tool
    return [{'type': 'function', **tool} for tool in voice.tool_definitions(VOICE_EXAMPLES)]

# Run the conversation on the shared loop, talk.py calls every provider the same way
def run_conversation(session, microphone, first_question, on_turn, greet_text):
    # Run the shared loop
    return voice.run_conversation(session, microphone, first_question, on_turn, greet_text)

# Close the socket, the session is finished
def close_session(session):
    # Stop the session
    session.stop()

    # Close the socket
    try:
        session.socket.close()
    except OSError:
        pass

# Say whether the last conversation ended because OpenAI turned it down
def turn_failed():
    # Return the shared failure flag
    return voice.turn_failed()

# Speak one line in the realtime voice, the greeting lands before any conversation opens
def speak_line(text):
    # Speak the exact words
    return speak_instruction(f'Say exactly this, and nothing else: {text}')

# Let the model write its own line and speak it, and return the words it said
def speak_answer(question):
    # Speak an answer the model writes
    return speak_instruction(f'{question} Say the line out loud, and say nothing else.')

# Open a session for one spoken turn with no microphone, say it, and hang up
def speak_instruction(instruction):
    # Open a session, the same one a conversation uses
    config = load_config()
    session = open_session(config)

    # Response instructions replace the session ones, so carry the accent along
    session.say(f'{config["realtime_accent"]} {instruction}')

    # Play it and hang up, there is no conversation to keep open yet
    speaker = voice.Speaker()
    try:
        return play_reply(session, speaker)
    finally:
        speaker.stop()
        close_session(session)

# Play one spoken reply as the audio arrives, and return the words it said
def play_reply(session, speaker):
    # Start with nothing said
    said = ''

    # Give up rather than hang, this runs during startup
    deadline = time.time() + voice.SPEAK_LINE_SECONDS
    while time.time() < deadline:
        event = session.receive()
        if event is None:
            continue
        kind = event.get('type', '')

        # Play each chunk the moment it lands
        if kind == 'response.output_audio.delta':
            speaker.play(base64.b64decode(event['delta']))

        # Keep the words, a caller that did not write the line needs to know what was said
        elif kind == 'response.output_audio_transcript.done':
            said = event.get('transcript', '').strip()

        # Let the speaker empty, the audio is complete
        elif kind == 'response.output_audio.done':
            speaker.drain()

        # Wait for the whole turn, the transcript can land after the audio
        elif kind == 'response.done':
            failure = response_failure(event)
            if failure:
                report_error(session, {'error': failure})
            elif said:
                robot_move.clear_warning()
            return said

        # Show why the server gave up
        elif kind == 'error':
            report_error(session, event)
            return said
    return said

# Log the server error, put its short name on the face, and hang up when retrying cannot help
def report_error(session, event):
    # Log the error
    error = event.get('error', {})
    message = error.get('message', '')
    print(f'Realtime error: {message}', flush=True)

    # Put its short name on the face
    name, page = client.openai_warning(f'{error.get("code", "")} {message}')
    robot_move.print_warning(name, page)

    # Hang up when retrying cannot help
    if client.openai_error_is_fatal(name):
        voice.fail_turn(session)

# Return the error on a failed response, empty when it finished fine
def response_failure(event):
    # Return nothing when the response finished
    response = event.get('response', {})
    if response.get('status') != 'failed':
        return {}

    # Return the error that failed it
    return response.get('status_details', {}).get('error') or {'message': 'response failed'}


# A realtime socket that is safe to send on from more than one thread
class OpenAISession(voice.Session):
    def __init__(self, socket):
        super().__init__()
        self.socket = socket
        self.lock = threading.Lock()
        self.voices = REALTIME_VOICES
        self.input_rate = REALTIME_RATE

    # Send one event as JSON
    def send(self, event):
        with self.lock:
            if not self.running:
                return
            try:
                self.socket.send(json.dumps(event))
            except (OSError, websocket.WebSocketException):
                self.running = False

    # Return the next event, or None when nothing arrived before the timeout
    def receive(self):
        try:
            return json.loads(self.socket.recv())
        except websocket.WebSocketTimeoutException:
            return None
        except (OSError, ValueError, websocket.WebSocketException) as error:
            print(f'Realtime connection lost: {error!r}', flush=True)
            self.running = False
            return None

    # Close the socket
    def close(self):
        close_session(self)

    # Open a new session on the settings given
    def reopen(self, config):
        return open_session(config)

    # Append microphone audio to the input buffer in chunks
    def send_audio(self, audio):
        for start in range(0, len(audio), APPEND_BYTES):
            chunk = base64.b64encode(audio[start:start + APPEND_BYTES]).decode()
            self.send({'type': 'input_audio_buffer.append', 'audio': chunk})

    # Drop audio waiting in the input buffer
    def clear_input(self):
        self.send({'type': 'input_audio_buffer.clear'})

    # Stop the reply being spoken
    def cancel_response(self):
        self.send({'type': 'response.cancel'})

    # Send one written question as a user turn and ask for a spoken answer
    def ask(self, question):
        self.send({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': question}]}})
        self.answer()

    # Ask for a spoken answer
    def answer(self):
        self.send({'type': 'response.create'})

    # Speak with instructions that replace the session ones for this one reply
    def say(self, instructions):
        self.send({'type': 'response.create', 'response': {'instructions': instructions}})

    # Hand a tool result back to the model
    def send_tool_result(self, call_id, output):
        self.send({'type': 'conversation.item.create', 'item': {'type': 'function_call_output', 'call_id': call_id, 'output': output}})

    # Put the recent conversation into a session that opened with no memory of it
    def replay(self, history):
        for heard, reply in history:
            # Send what the person said
            if heard:
                self.send({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': heard}]}})

            # Send what the robot answered, an assistant turn carries output text
            if reply:
                self.send({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': reply}]}})

    # Act on one server event, return true when it counted as activity
    def handle_event(self, microphone, speaker, event):
        # Get the event type
        kind = event.get('type', '')

        # Play the reply as it arrives
        if kind == 'response.output_audio.delta':
            return voice.play_audio(self, microphone, speaker, base64.b64decode(event['delta']))

        # Let the microphone back in once the reply is done
        if kind == 'response.output_audio.done':
            return voice.finish_audio(self, microphone, speaker)

        # Print the heard words as they arrive, the live transcriber sends them mid sentence
        if kind == 'conversation.item.input_audio_transcription.delta':
            return voice.heard_delta(self, microphone, event.get('delta', ''))

        # Keep the heard text once the transcriber has finished it
        if kind == 'conversation.item.input_audio_transcription.completed':
            return voice.heard_done(self, microphone, speaker, event.get('transcript', ''))

        # Print the model's words as it speaks, these come from the realtime model itself not a transcriber
        if kind == 'response.output_audio_transcript.delta':
            if not PRINT_REPLY_LIVE:
                return True
            return voice.reply_delta(self, event.get('delta', ''))

        # Print the finished reply and log the turn
        if kind == 'response.output_audio_transcript.done':
            return voice.reply_done(self, event.get('transcript', ''))

        # Run the tool here and hand the result back, the tools are all local
        if kind == 'response.function_call_arguments.done':
            voice.run_function_call(self, event.get('name'), event.get('arguments'), event.get('call_id'))
            return True

        # Finish the turn, some failures only come back on the finished response
        if kind == 'response.done':
            failure = response_failure(event)
            if failure:
                report_error(self, {'error': failure})
                return False
            return voice.finish_turn(self, speaker)

        # Show why the server gave up
        if kind == 'error':
            voice.close_stream_lines(self)
            report_error(self, event)
            return False
        return False


# Run main
if __name__ == '__main__':
    main()
