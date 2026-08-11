"""PyInstaller's entry point for the tray app: `music_agent.ui.tray`, as a plain script.

A frozen build needs a file, not a `-m` module path, and it must be able to import the package -- so
this lives at the project root beside `music_agent/`. Running from source needs nothing from here:
use `python -m music_agent.ui`.
"""

from music_agent.ui.tray import main

if __name__ == "__main__":
    main()
