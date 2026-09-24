"""Separate tool locations from the user's mutable workspace and installed game."""
import os
from pathlib import Path

DEVKIT = Path(__file__).resolve().parents[1]
GAME_NAME = 'Heroes of Might and Magic 5 Tribes of the East'


def workspace_root():
    return Path(os.environ.get('H5_WORKSPACE') or DEVKIT).expanduser().resolve()


def game_installation(root):
    configured = os.environ.get('H5_GAME_DIR')
    return Path(configured).expanduser().resolve() if configured else root / GAME_NAME
