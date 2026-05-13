"""Main entrypoint.

Runs the playable Pygame UI (human vs AI). The Tkinter analysis GUI is still
available via `python -c "from ui.gui import ChessGUI; ChessGUI().run()"`.
"""

from ui.pygame_gui import PygameChessGUI


if __name__ == "__main__":
	PygameChessGUI().run()