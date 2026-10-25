#!/usr/bin/env python3

# Drink ordering mode, Drinkman takes drink orders with its own prompt in place of the Deskman prompt

# Imports
import re
import time

# Import the head and hat mover
import move
import robot_move

# Config the drinks on offer
DRINKS = ["Grapefruit", "Lemon", "Lime", "Blackberry", "Plum"]

# Config the drinks as spoken when offering them, with no article
OFFER = ", ".join(drink.lower() for drink in DRINKS[:-1]) + ", and " + DRINKS[-1].lower()

# Config the spoken lines
GREETING = "What drink would you like?"
STOP_MESSAGE = "Drink ordering is off."
START_EXCHANGE = "Start drink ordering."

# Config what Drinkman says when taking an order, and then after a pause when handing over the drink, followed by the moves, close hat, look left, open hat, look forward
DISPENSE_MESSAGE = "A tasty {drink} drink, sure thing. Coming right up."
DISPENSE_HANDOVER = "Here's your drink."
DISPENSE_MOVES = ["hat_down", "turn", "hat_up", "center"]
DISPENSE_TURN = "left"
DISPENSE_OTHER_TURN = "right"
DISPENSE_OTHER_TURN_DRINKS = ["blackberry"]
DISPENSE_MOVE_SECONDS = 1.0

# Config the words speech to text mishears and the drink each one means
HEARD_AS = {"lima": "lime", "line": "lime", "lyme": "lime"}
HEARD_PATTERN = re.compile(r"\b(" + "|".join(HEARD_AS) + r")\b", re.IGNORECASE)

# Config the words that cancel an order, and the questions about drinks that are not orders
NO_PATTERN = re.compile(r"\b(don t|dont|do not|no|not|never|without|nothing|none)\b")
QUESTION_PATTERN = re.compile(r"^(do you|have you|is there|are there|what|which|how|why|who)\b")

# Config the words that name the app and the words that start or stop it
APP_PATTERN = re.compile(r"\b(drinks?\s+(ordering|order|app\w*|mode)|drinkman)\b")
START_APP_PATTERN = re.compile(r"\b(drinks?|drinkman)\b")
MISHEARD_START_PATTERN = re.compile(r"\bthe drinks\s+(out|at|up|ap|apps)\b")
ASK_FOR_DRINK_PATTERN = re.compile(r"\b(want|like|need|get|have|give)\b.*\b(a|some|one|any)\s+drinks?\b|\bdrinks?\s+please\b")
START_PATTERN = re.compile(r"\b(start|begin|open|launch|run|enable|activate|turn on|switch to)\b")
STOP_WORDS = r"stop|end|close|leave|disable|cancel|turn\b.*\boff|switch\b.*\boff"
STOP_PATTERN = re.compile(r"\b(" + STOP_WORDS + r")\b")

# Config the words that end ordering on their own, while ordering is on there is no need to name the app
STOP_NOW_WORDS = r"exit|enough|done|never mind|nevermind|no more|that\s*s all|thats all"
STOP_NOW_PATTERN = re.compile(r"\b(" + STOP_WORDS + "|" + STOP_NOW_WORDS + r")\b")

# Prompt that replaces the Deskman prompt while ordering
PROMPT = (
    "You are a drink ordering robot, Drinkman. "
    f'You have already asked "{GREETING}" '
    "Take the order for the drink the person wants. "
    "Available drinks are:\n" + "\n".join(DRINKS) + "\n"
    "Ignore the word soda. "
    "The words may be misheard, so match to the closest available drink. "
    "Only when they clearly ask for one of the available drinks, confirm the order by saying only the drink name, with no other words. "
    "If they ask a question, are unsure, or do not want a drink, answer them in a friendly way and ask what drink they would like, and do not say a drink name on its own. "
    "If they ask whether a drink is available, say yes and ask if they would like one. "
    "If they ask for a drink that is not available, say sorry, then say we offer " + OFFER + ". "
    "Answer in one or two short sentences suitable for speaking aloud."
)

# Mode state, true while drink ordering is on, and the name of the drink ordered that is not handed over yet
ordering_on = False
dispense_pending = ""

# Main
def main():
    # Print the drink ordering prompt
    print(PROMPT)

# True while drink ordering mode is on
def is_on():
    return ordering_on

# Turn drink ordering mode on or off
def set_on(value):
    global ordering_on
    ordering_on = value

# Turn the drinks look on the face on or off, a robot that is down just misses it
def set_face(on):
    try:
        robot_move.send_command({"command": "drinks", "on": bool(on)})
    except Exception:
        pass

# Rewrite misheard words in what was said to the drink they mean
def fix_heard(prompt):
    return HEARD_PATTERN.sub(lambda match: HEARD_AS[match.group(1).lower()], prompt)

# Return true when the reply is only the name of a drink
def is_drink_name(reply):
    return clean_words(reply) in [drink.lower() for drink in DRINKS]

# Return true when what the person said was a no or a question, so a drink name in the reply is not an order
def is_declined(prompt):
    text = clean_words(prompt)
    return bool(NO_PATTERN.search(text) or QUESTION_PATTERN.search(text))

# Mark a drink as waiting to be handed over, or clear it with an empty name
def set_dispense(value):
    global dispense_pending
    dispense_pending = value

# Return the drink name once for each order that is waiting to be handed over, empty when none
def take_dispense():
    global dispense_pending
    waiting = dispense_pending
    dispense_pending = ""
    return waiting

# Hand over the drink, saying the handover line, then the hat and head moves
def dispense(say, drink):
    # Say the handover line
    say(DISPENSE_HANDOVER)

    # Turn the other way for some drinks, then make each move and give the servos time to get there
    turn = DISPENSE_OTHER_TURN if clean_words(drink) in DISPENSE_OTHER_TURN_DRINKS else DISPENSE_TURN
    for direction in [turn if move_name == "turn" else move_name for move_name in DISPENSE_MOVES]:
        move.run_look({"direction": direction})
        time.sleep(DISPENSE_MOVE_SECONDS)

# Lowercase the spoken words with punctuation removed
def clean_words(prompt):
    text = re.sub(r"[^\w\s]", " ", str(prompt or "").lower())
    return " ".join(text.split())

# Return true when the person asked to start drink ordering
def needs_start(prompt):
    text = clean_words(prompt)
    if STOP_PATTERN.search(text):
        return False

    # Accept a start word with the app name, or with just the word drinks, and a few ways speech to text mishears the app
    if APP_PATTERN.search(text) and START_PATTERN.search(text):
        return True
    if ordering_on:
        return False

    # Asking for a drink starts it too, unless it was a no or a question
    if ASK_FOR_DRINK_PATTERN.search(text) and not is_declined(prompt):
        return True
    return bool((START_APP_PATTERN.search(text) and START_PATTERN.search(text)) or MISHEARD_START_PATTERN.search(text))

# Return true when the person asked to stop drink ordering, a stop word or the app name alone is enough while ordering is on
def needs_stop(prompt):
    text = clean_words(prompt)
    if APP_PATTERN.search(text) and STOP_PATTERN.search(text):
        return True

    # Naming the app with no start word while it is on means stop, speech to text often turns quit into quick
    if ordering_on and APP_PATTERN.search(text) and not START_PATTERN.search(text):
        return True
    return ordering_on and bool(STOP_NOW_PATTERN.search(text))

# Main
if __name__ == "__main__":
    main()
