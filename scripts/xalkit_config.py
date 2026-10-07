"""User configuration paths and persistence shared by CLI and localization."""
import json
import os
from pathlib import Path
import typer
from workspace import workspace_root


def config_path():
    return Path(os.environ.get('XALKIT_CONFIG') or Path(typer.get_app_dir('XalKit')) / 'settings.json')


def read_settings():
    path = config_path()
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def settings():
    saved = read_settings()
    saved['workspace'] = str(Path(os.environ.get('H5_WORKSPACE') or saved.get('workspace') or workspace_root()).resolve())
    if os.environ.get('H5_GAME_DIR'):
        saved['game'] = os.environ['H5_GAME_DIR']
    os.environ['H5_WORKSPACE'] = saved['workspace']
    if saved.get('game'):
        os.environ['H5_GAME_DIR'] = saved['game']
    return saved


def save_settings(saved):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
