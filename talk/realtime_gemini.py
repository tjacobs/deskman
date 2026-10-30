#!.venv/bin/python

# Realtime_gemini.py streams microphone audio up to Gemini Live and plays the spoken audio reply.

# Imports
import os
import json
import time
import queue
import asyncio
import argparse
import threading
import contextlib
from google import genai
from google.genai import types

# Imports from the shared voice helper, which also puts the text helper on the path
import voice
import robot_move

# Config the session, config.json overrides these
GEMINI_MODEL = 'gemini-3.8-live'
GEMINI_VOICE = 'achird'
GEMINI_ACCENT = ''

# Config where the API key is read from when the environment has none
KEY_NAME = 'GEMINI_API_KEY'
KEY_FILE = os.path.join(voice.SCRIPT_DIR, 'gemini.env')

# Config the voices Gemini speaks with and how each comes across, the ones picked by ear
GEMINI_VOICES = {'achird': 'male', 'enceladus': 'male', 'umbriel': 'male', 'alnilam': 'male'}

# Config example voice names given to the model when it picks one
VOICE_EXAMPLES = 'achird, umbriel, or alnilam'

# Config the short names shown on the face for Gemini failures, checked in order
GEMINI_KEY_REJECTED = 'Gemini key rejected'
GEMINI_OUT_OF_QUOTA = 'Gemini out of quota'
GEMINI_ERROR_NAMES = (
    (('api key', 'permission_denied', 'unauthenticated', '401', '403'), GEMINI_KEY_REJECTED),
    (('resource_exhausted', 'quota', '429'), GEMINI_OUT_OF_QUOTA))
GEMINI_ERROR_NAME = 'Gemini error'
GEMINI_FATAL_ERRORS = (GEMINI_KEY_REJECTED, GEMINI_OUT_OF_QUOTA)

# Config the timing
CONNECT_TIMEOUT = 30
CLOSE_TIMEOUT = 5

# Config the rate of the audio sent up
GEMINI_INPUT_RATE = 16000

# Hold one Gemini Live conversation from the microphone
def main():
    # Parse arguments
    arguments = parse_args()

    # Load config
    config = load_config()

    # Load from the command line, overriding config.json
    if arguments.model:
        config['gemini_model'] = arguments.model
    if arguments.voice:
        config['realtime_voice'] = arguments.voice

    # Open the microphone before the connection, muted so connect time is not sent as speech
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
    parser = argparse.ArgumentParser(description='Talk to Gemini Live with audio up and audio down.')
    parser.add_argument('ask', nargs='?', help='send this question first, instead of waiting for speech')
    parser.add_argument('--model', help='Gemini model to use, overrides config.json')
    parser.add_argument('--voice', help='voice to speak with, overrides config.json')
    return parser.parse_args()

# Read config.json, falling back to the defaults above for anything missing
def load_config():
    # Load the settings with the defaults above
    config = voice.load_config({'gemini_model': GEMINI_MODEL, 'realtime_voice': GEMINI_VOICE, 'realtime_accent': GEMINI_ACCENT})

    # Fall back to the default voice when the saved one belongs to another provider
    if config['realtime_voice'] not in GEMINI_VOICES:
        config['realtime_voice'] = GEMINI_VOICE
    return config

# Connect to Gemini Live and configure the session
def open_session(config):
    # Get the API key
    key = load_key()
    if not key:
        raise SystemExit('No Gemini key, set GEMINI_API_KEY or write talk/gemini.env.')

    # Describe the voice, the transcripts, and the tools before any audio moves
    print('Connecting to Gemini...', flush=True)
    instructions = voice.session_instructions(config['realtime_accent'])
    if 'earlier' in config:
        instructions = f'{instructions}\n\nThe conversation so far:\n{config["earlier"]}'
    voice_config = types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config['realtime_voice'].capitalize()))
    live_config = types.LiveConnectConfig(response_modalities=['AUDIO'], system_instruction=instructions, speech_config=types.SpeechConfig(voice_config=voice_config), input_audio_transcription=types.AudioTranscriptionConfig(), output_audio_transcription=types.AudioTranscriptionConfig(), tools=session_tools())

    # Open the connection on the worker thread
    session = GeminiSession(genai.Client(api_key=key))
    session.config = config
    try:
        asyncio.run_coroutine_threadsafe(session.connect(config['gemini_model'], live_config), session.loop).result(CONNECT_TIMEOUT)
    except Exception as error:
        close_session(session)
        raise SystemExit(f'Gemini connection failed: {error!r}')
    print('Connected.', flush=True)
    return session

# Read the API key from the environment, or from talk/gemini.env
def load_key():
    # Prefer the environment
    key = os.environ.get(KEY_NAME, '').strip()
    if key or not os.path.isfile(KEY_FILE):
        return key

    # Read the key line from the file
    with open(KEY_FILE, encoding='utf-8') as key_file:
        for line in key_file:
            if line.strip().startswith(KEY_NAME + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    return ''

# Wrap the shared tool list in the shape Gemini expects, blocking so the model waits for each result
def session_tools():
    # Declare each tool
    declarations = [types.FunctionDeclaration(name=tool['name'], description=tool['description'], parameters_json_schema=tool['parameters'] or None, behavior=types.Behavior.BLOCKING) for tool in voice.tool_definitions(VOICE_EXAMPLES)]
    return [types.Tool(function_declarations=declarations)]

# Run the conversation on the shared loop, talk.py calls every provider the same way
def run_conversation(session, microphone, first_question, on_turn, greet_text):
    # Run the shared loop
    return voice.run_conversation(session, microphone, first_question, on_turn, greet_text)

# Close the connection, the session is finished
def close_session(session):
    # Stop the session, and skip a second close
    session.stop()
    if session.closed:
        return
    session.closed = True

    # Close the connection, then stop the worker thread
    try:
        asyncio.run_coroutine_threadsafe(session.shutdown(), session.loop).result(CLOSE_TIMEOUT)
    except Exception:
        pass
    session.loop.call_soon_threadsafe(session.loop.stop)
    session.thread.join(CLOSE_TIMEOUT)

# Say whether the last conversation ended because Gemini turned it down
def turn_failed():
    # Return the shared failure flag
    return voice.turn_failed()

# Speak one line in the Gemini voice, the greeting lands before any conversation opens
def speak_line(text):
    # Speak the exact words
    return speak_instruction(f'Read this line out loud, word for word, with no changes and nothing added: "{text}"')

# Let the model write its own line and speak it, and return the words it said
def speak_answer(question):
    # Speak an answer the model writes
    return speak_instruction(f'{question} Say the line out loud, and say nothing else.')

# Open a session for one spoken turn with no microphone, say it, and hang up
def speak_instruction(instruction):
    # Open a session, the same one a conversation uses
    config = load_config()
    session = open_session(config)
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
        kind = event['type']

        # Play each chunk the moment it lands
        if kind == 'audio':
            speaker.play(event['data'])

        # Keep the words, a caller that did not write the line needs to know what was said
        elif kind == 'reply':
            said += event['text']

        # Let the speaker empty, the turn is complete
        elif kind == 'turn_complete':
            speaker.drain()
            if said:
                robot_move.clear_warning()
            return said.strip()

        # Show why the connection closed
        elif kind == 'closed':
            report_error(session, event['error'])
            return said.strip()
    return said.strip()

# Log the connection error, put its short name on the face, and hang up when retrying cannot help
def report_error(session, error):
    # Log the error
    print(f'Gemini connection lost: {error}', flush=True)
    session.running = False

    # Put its short name on the face
    name = describe_error(error)
    robot_move.print_warning(name, '')

    # Hang up when retrying cannot help
    if name in GEMINI_FATAL_ERRORS:
        voice.fail_turn(session)

# Name a Gemini failure in a few words for the face, the full message stays in the log
def describe_error(error):
    # Find the first name whose phrases match
    lowered = str(error).lower()
    for phrases, name in GEMINI_ERROR_NAMES:
        if any(phrase in lowered for phrase in phrases):
            return name
    return GEMINI_ERROR_NAME

# Turn one Gemini message into plain events, a message can carry several things at once
def message_events(message):
    # Start with no events
    events = []

    # Add what the server heard, said, and whether the turn ended
    content = message.server_content
    if content:
        if content.input_transcription and content.input_transcription.text:
            events.append({'type': 'heard', 'text': content.input_transcription.text})
        if content.model_turn:
            for part in content.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    events.append({'type': 'audio', 'data': part.inline_data.data})
        if content.output_transcription and content.output_transcription.text:
            events.append({'type': 'reply', 'text': content.output_transcription.text})
        if content.interrupted:
            events.append({'type': 'interrupted'})
        if content.turn_complete:
            events.append({'type': 'turn_complete'})

    # Add when the server decided speech started or ended
    if message.voice_activity:
        events.append({'type': 'activity_end' if 'END' in str(message.voice_activity.voice_activity_type) else 'activity_start'})

    # Add each tool call, with the arguments as JSON text like the shared code expects
    if message.tool_call:
        for call in message.tool_call.function_calls or []:
            events.append({'type': 'tool', 'id': call.id, 'name': call.name, 'arguments': json.dumps(call.args or {})})
    return events


# A Gemini Live connection run on a worker thread, safe to use from the threaded conversation loop
class GeminiSession(voice.Session):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.live = None
        self.reader = None
        self.closed = False
        self.events = queue.Queue()
        self.heard_pieces = []
        self.reply_pieces = []
        self.call_names = {}
        self.stack = contextlib.AsyncExitStack()
        self.voices = GEMINI_VOICES
        self.input_rate = GEMINI_INPUT_RATE
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    # Open the connection and start reading from it, this runs on the worker thread
    async def connect(self, model, live_config):
        self.live = await self.stack.enter_async_context(self.client.aio.live.connect(model=model, config=live_config))
        self.reader = asyncio.ensure_future(self.read_events())

    # Turn server messages into events until the connection ends, this runs on the worker thread
    async def read_events(self):
        try:
            while True:
                # Read until the turn ends, the stream stops after each turn so start it again
                count = 0
                async for message in self.live.receive():
                    count += 1
                    for event in message_events(message):
                        self.events.put(event)

                # Treat a stream that gave nothing as a closed connection
                if count == 0:
                    self.events.put({'type': 'closed', 'error': 'connection ended'})
                    return
        except Exception as error:
            self.events.put({'type': 'closed', 'error': repr(error)})

    # Stop reading and close the connection, this runs on the worker thread
    async def shutdown(self):
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        await self.stack.aclose()

    # Hand one call to the worker thread without waiting for it
    def send(self, coroutine):
        # Drop it when the session is over
        if not self.running or self.live is None:
            coroutine.close()
            return

        # Run it on the worker thread and watch for a failure
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        future.add_done_callback(self.check_send)

    # Stop the session when a send failed
    def check_send(self, future):
        if future.cancelled() or not future.exception() or not self.running:
            return
        print(f'Gemini send failed: {future.exception()!r}', flush=True)
        self.running = False

    # Return the next event, or None when nothing arrived before the timeout
    def receive(self):
        try:
            return self.events.get(timeout=voice.RECEIVE_TIMEOUT)
        except queue.Empty:
            return None

    # Close the connection
    def close(self):
        close_session(self)

    # Open a new session on the settings given, with the recent conversation in its instructions
    def reopen(self, config):
        earlier = '\n'.join(f'Person: {heard}\nYou: {reply}' for heard, reply in self.history[-voice.REPLAY_TURNS:])
        return open_session(dict(config, earlier=earlier))

    # Send microphone audio up, Gemini finds the ends of sentences itself
    def send_audio(self, audio):
        self.send(self.live.send_realtime_input(audio=types.Blob(data=audio, mime_type=f'audio/pcm;rate={GEMINI_INPUT_RATE}')))

    # Do nothing, Gemini keeps no input buffer to clear
    def clear_input(self):
        return

    # Do nothing, Gemini has no cancel and every caller closes the session straight after
    def cancel_response(self):
        return

    # Send one written question as a user turn, Gemini answers on its own
    def ask(self, question):
        self.send(self.live.send_realtime_input(text=question))

    # Do nothing, Gemini carries on answering once the tool results are in
    def answer(self):
        return

    # Speak with the instructions sent as a user turn
    def say(self, instructions):
        self.send(self.live.send_realtime_input(text=instructions))

    # Hand a tool result back to the model, unless a voice change is about to replace this session
    def send_tool_result(self, call_id, output):
        if self.voice_request:
            return
        response = types.FunctionResponse(id=call_id, name=self.call_names.get(call_id, ''), response={'result': output})
        self.send(self.live.send_tool_response(function_responses=[response]))

    # Do nothing, the recent conversation went in with the instructions when the session opened
    def replay(self, history):
        return

    # Act on one event, return true when it counted as activity
    def handle_event(self, microphone, speaker, event):
        # Get the event type
        kind = event['type']

        # Print the heard words as they arrive, ignoring what the robot's own speech leaks in
        if kind == 'heard':
            if microphone.muted:
                return False
            self.heard_pieces.append(event['text'])
            return voice.heard_delta(self, microphone, event['text'])

        # Count speech starting as activity
        if kind == 'activity_start':
            return True

        # Act on the heard words once the server says the speech has ended
        if kind == 'activity_end':
            return self.finish_heard(microphone, speaker)

        # Show why the connection closed
        if kind == 'closed':
            voice.close_stream_lines(self)
            report_error(self, event['error'])
            return False

        # Act on the heard words before the model answers, Gemini sends no event for them being finished
        if kind in ('audio', 'reply', 'tool', 'turn_complete'):
            self.finish_heard(microphone, speaker)
            if not self.running:
                return False

        # Play the reply as it arrives
        if kind == 'audio':
            return voice.play_audio(self, microphone, speaker, event['data'])

        # Print the model's words as it speaks
        if kind == 'reply':
            self.reply_pieces.append(event['text'])
            return voice.reply_delta(self, event['text'])

        # Cut the speech off, the person talked over it
        if kind == 'interrupted':
            speaker.stop()
            return True

        # Run the tool here and hand the result back, a voice change reopens the session at once
        if kind == 'tool':
            self.call_names[event['id']] = event['name']
            voice.run_function_call(self, event['name'], event['arguments'], event['id'])
            if self.voice_request:
                self.voice_ready = True
            return True

        # Finish the reply, let the microphone back in, and finish the turn
        if kind == 'turn_complete':
            reply = ''.join(self.reply_pieces)
            self.reply_pieces = []
            if reply:
                voice.reply_done(self, reply)
            if microphone.muted:
                voice.finish_audio(self, microphone, speaker)
            return voice.finish_turn(self, speaker)
        return False

    # Give the shared code the heard words, return true when they counted as activity
    def finish_heard(self, microphone, speaker):
        # Skip when nothing was heard since the last turn
        if not self.heard_pieces:
            return False

        # Hand over the whole sentence
        transcript = ''.join(self.heard_pieces)
        self.heard_pieces = []
        return voice.heard_done(self, microphone, speaker, transcript)


# Run main
if __name__ == '__main__':
    main()
