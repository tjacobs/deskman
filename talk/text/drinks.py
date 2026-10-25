#!/usr/bin/env python3

# Drink ordering mode, Drinkman takes drink orders with its own prompt in place of the Deskman prompt

# Imports
import re
import time

# Import the head and hat mover
import move

# Config the drinks on offer
DRINKS = ["Grapefruit", "Lemon", "Lime", "Blackberry", "Peach", "Water"]

# Config the drinks as spoken when offering them, with no article
OFFER = ", ".join(drink.lower() for drink in DRINKS[:-1]) + ", and " + DRINKS[-1].lower()

# Config the spoken lines
GREETING = "What drink would you like?"
STOP_MESSAGE = "Drink ordering is off."
START_EXCHANGE = "Start drink ordering."

# Config what Drinkman says when taking an order, and then after a pause when handing over the drink, followed by the moves, close hat, look left, open hat, look forward
DISPENSE_MESSAGE = "Coming right up."
DISPENSE_HANDOVER = "Here's your drink."
DISPENSE_PAUSE_SECONDS = 0.5
DISPENSE_MOVES = ["hat_down", "left", "hat_up", "center"]
DISPENSE_MOVE_SECONDS = 1.0

# Config the words speech to text mishears and the drink each one means
HEARD_AS = {"lima": "lime", "line": "lime", "lyme": "lime"}
HEARD_PATTERN = re.compile(r"\b(" + "|".join(HEARD_AS) + r")\b", re.IGNORECASE)

# Config the words that name the app and the words that start or stop it
APP_PATTERN = re.compile(r"\b(drinks?\s+(ordering|order|app|mode)|drinkman)\b")
START_PATTERN = re.compile(r"\b(start|begin|open|launch|run|enable|activate|turn on|switch to)\b")
STOP_PATTERN = re.compile(r"\b(stop|end|close|leave|disable|cancel|turn off|switch off)\b")

# Prompt that replaces the Deskman prompt while ordering
PROMPT = (
    "You are a drink ordering robot, Drinkman. "
    f'You have already asked "{GREETING}" '
    "Take the order for the drink the person wants. "
    "Available drinks are:\n" + "\n".join(DRINKS) + "\n"
    "Ignore the word soda. "
    "The words may be misheard, so match to the closest available drink. "
    "Confirm the order by saying only the drink name, with no other words. "
    "If they ask for a drink that is not available, say sorry, then say we offer " + OFFER + ". "
    "Answer in one or two short sentences suitable for speaking aloud."
)

# Mode state, true while drink ordering is on, and true when an order was taken and the drink is not handed over yet
ordering_on = False
dispense_pending = False

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

# Rewrite misheard words in what was said to the drink they mean
def fix_heard(prompt):
    return HEARD_PATTERN.sub(lambda match: HEARD_AS[match.group(1).lower()], prompt)

# Return true when the reply is only the name of a drink, which means the order was taken
def is_order(reply):
    return clean_words(reply) in [drink.lower() for drink in DRINKS]

# Mark a drink as waiting to be handed over
def set_dispense(value):
    global dispense_pending
    dispense_pending = value

# Return true once for each order that is waiting to be handed over
def take_dispense():
    global dispense_pending
    waiting = dispense_pending
    dispense_pending = False
    return waiting

# Hand over the drink, saying the handover line after a pause, then the hat and head moves
def dispense(say):
    # Pause, then say it
    time.sleep(DISPENSE_PAUSE_SECONDS)
    say(DISPENSE_HANDOVER)

    # Make each move, then give the servos time to get there
    for direction in DISPENSE_MOVES:
        move.run_look({"direction": direction})
        time.sleep(DISPENSE_MOVE_SECONDS)

# Lowercase the spoken words with punctuation removed
def clean_words(prompt):
    text = re.sub(r"[^\w\s]", " ", str(prompt or "").lower())
    return " ".join(text.split())

# Return true when the person asked to start drink ordering
def needs_start(prompt):
    text = clean_words(prompt)
    return bool(APP_PATTERN.search(text) and START_PATTERN.search(text) and not STOP_PATTERN.search(text))

# Return true when the person asked to stop drink ordering
def needs_stop(prompt):
    text = clean_words(prompt)
    return bool(APP_PATTERN.search(text) and STOP_PATTERN.search(text))

# Main
if __name__ == "__main__":
    main()
