import os
from pathlib import Path


def get_media_root() -> Path:
    return Path(os.getenv("MEDIA_ROOT", "media")).resolve()
