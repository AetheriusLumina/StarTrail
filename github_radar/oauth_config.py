"""Public publisher identity shipped with the app; never contains a client secret."""
import json
import os
import re
from pathlib import Path

def publisher_client_id(config_path=None,environ=None):
    environ=os.environ if environ is None else environ
    value=environ.get('GITHUB_RADAR_OAUTH_CLIENT_ID')
    if value is None:
        path=Path(config_path) if config_path is not None else Path(__file__).with_name('public_config.json')
        try:
            if path.stat().st_size>4096:return None
            data=json.loads(path.read_text(encoding='utf-8'))
            value=data.get('github_oauth_client_id') if isinstance(data,dict) else None
        except (OSError,ValueError,UnicodeError):return None
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9]{16,80}',value) else None
