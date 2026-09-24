"""Resolve a sandbox map's archive path without reading its gameplay contents."""
from zipfile import ZipFile


def map_arguments(game, name):
    if not name or any(character in name for character in '/\\:') or name in {'.', '..'}:
        raise ValueError('Use a map filename from the sandbox Maps folder.')
    filename = name if name.lower().endswith('.h5m') else name + '.h5m'
    with ZipFile(game / 'Maps' / filename) as archive:
        paths = [path for path in archive.namelist()
                 if path.lower().startswith('maps/') and path.lower().endswith('/map.xdb')]
    if len(paths) != 1:
        raise ValueError('Expected exactly one map.xdb in the selected map archive.')
    path = paths[0]
    if any(character in path for character in '\r\n";') or '..' in path.split('/'):
        raise ValueError('Map archive contains an unsupported launch path.')
    return ['-advmap', path]
