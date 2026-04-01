# This file is called by external scripts (like background_listener.py) to simulate human typing.
# It uses the keyboard library to simulate keystrokes based on WPM and Accuracy settings loaded from config.yaml.
# Outputs: Simulates physical keystrokes, including generated errors and backspaces.

import time
import random
import yaml
import os
import keyboard


class HumanTyper:
    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(os.path.dirname(__file__), "config.yaml")

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        self.wpm = config.get("typing", {}).get("wpm", 76)
        self.accuracy = config.get("typing", {}).get("accuracy", 0.86)

        # Base delay: 60 sec / (WPM * 5 chars)
        self.base_delay = 60.0 / (self.wpm * 5.0)

        self.chars_typed = 0

        # Standard QWERTY neighbor map targeting proximity typing errors
        self.neighbors = {
            "q": "wa",
            "w": "qeas",
            "e": "wrsd",
            "r": "etdf",
            "t": "ryfg",
            "y": "tugh",
            "u": "yijk",
            "i": "oukl",
            "o": "ipl",
            "p": "o[",
            "a": "qwsz",
            "s": "wedxaz",
            "d": "erfcxs",
            "f": "rtgvcd",
            "g": "tyhbvf",
            "h": "yujnbg",
            "j": "uikmnh",
            "k": "iolmj",
            "l": "opk",
            "z": "asx",
            "x": "zsdc",
            "c": "xdfv",
            "v": "cfgb",
            "b": "vghn",
            "n": "bhjm",
            "m": "njk",
        }

    def type(self, text):
        """Types the given string out using realistic human timing and errors."""
        i = 0
        while i < len(text):
            # Added break condition to stop typing when Escape is pressed
            if keyboard.is_pressed("escape"):
                print("Typing cancelled by user (Escape pressed).")
                break

            char = text[i]

            # Substrings logic (Bigrams and common suffixes logic)
            # Find the longest matching sequence if any
            jump_seq = None
            for seq in ["tion", "ing", "th", "er", "in", "and", "ion"]:
                if text[i : i + len(seq)].lower() == seq:
                    jump_seq = seq
                    break

            if jump_seq:
                # Type the sequence very quickly (accelerated muscle memory)
                for _ in jump_seq:
                    self._type_char(text[i])
                    time.sleep(self.base_delay * 0.3)  # 70% faster
                    i += 1
                continue

            # Determine if this keystroke is an error
            is_error = random.random() > self.accuracy

            # We only generate neighbor typos for letters matching our neighbor layout
            if is_error and char.lower() in self.neighbors:
                wrong_char = random.choice(self.neighbors[char.lower()])
                if char.isupper():
                    wrong_char = wrong_char.upper()

                # Make the error
                self._type_char(wrong_char)

                # Realization pause (takes a moment to notice mistake)
                time.sleep(self._get_delay(char, text, i) * 4)

                # Correct it (time for corrections is distinct from the base WPM pace)
                keyboard.send("backspace")
                time.sleep(self.base_delay * 3.5)

                # Type the correct char
                self._type_char(char)
            else:
                self._type_char(char)

            time.sleep(self._get_delay(char, text, i))
            i += 1

        # Reset fatigue after a block is finished
        self.chars_typed = 0

    def _type_char(self, char):
        """Simulate single keystroke safely."""
        # Convert raw enters to shift+enter to avoid accidental submission
        if char == "\n":
            keyboard.send("shift+enter")
        else:
            keyboard.write(char)

        self.chars_typed += 1

    def _get_delay(self, current_char, full_text, idx):
        """Calculate dynamic delay for current keystroke."""
        # Burst typing cadence:
        # Fast typists often "burst" through words because their fingers know the word,
        # but pause between words to think of the next one.
        # A standard mathematical word is 5 characters (4 letters + 1 space).
        # We make letters type much faster, and compensate by making the space pause longer.
        burst_factor = 0.5  # Letters are typed ~2.0x faster than the base average WPM
        space_factor = 5.0 - (
            4.0 * burst_factor
        )  # Space takes the remainder of the 5-char block (3.0x base)

        if current_char == " ":
            delay = self.base_delay * space_factor
        else:
            delay = self.base_delay * burst_factor

        # Upper case penalty (need to coordinate Shift + Key)
        if current_char.isupper():
            delay += 0.05

        # Repeated character penalty (harder to tap same finger twice quickly with timing)
        if idx > 0 and current_char.lower() == full_text[idx - 1].lower():
            delay += 0.04

        # Punctuation / mental pauses
        if current_char in [".", "!", "?"]:
            delay += random.uniform(0.2, 0.4)
        elif current_char in [",", ";", ":"]:
            delay += random.uniform(0.1, 0.2)

        # Fatigue modeling (+0.05% compounding per char typed in a row)
        fatigue_multiplier = 1.0 + (self.chars_typed * 0.0005)
        delay *= fatigue_multiplier

        # General random variance to simulate natural inconsistencies
        variance = random.uniform(0.85, 1.15)
        return delay * variance


if __name__ == "__main__":
    import sys

    print("Testing HumanTyper. Focus a text field within 3 seconds...")
    time.sleep(3)
    typer = HumanTyper()
    typer.type(
        "This is a quick demonstration of human typing simulation with errors and varied timing."
    )
