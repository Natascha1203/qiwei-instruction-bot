from datetime import datetime
from pathlib import Path


def ensure_output_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def build_output_filename(prefix: str, suffix: str = ".xlsx") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}"


def save_bytes(data: bytes, file_path: str) -> str:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)
