# Human Typing

This file is called by external scripts (like background_listener.py) to simulate human typing.
It uses the keyboard library to simulate keystrokes based on WPM and Accuracy settings loaded from config.yaml.
Outputs: Simulates physical keystrokes, including generated errors and backspaces.

## Features
- Speed calculated from Target WPM (Words Per Minute)
- Accuracy modeling (QWERTY neighbor typos and backspaces)
- Bigram acceleration for common sequences ("tion", "ing")
- Fatigue modeling
- Shift-key and repeated-letter penalties
