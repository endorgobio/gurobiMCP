import base64
import logging
from pathlib import Path

from app.schemas.chat import FilePayload

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = Path("data/workspaces")


def get_user_workspace(user_id: int) -> Path:
    return _WORKSPACE_ROOT / str(user_id)


def ensure_workspace(user_id: int) -> Path:
    path = get_user_workspace(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_path(user_id: int, filename: str) -> Path:
    p = Path(filename)
    if any(part == ".." for part in p.parts) or p.is_absolute():
        raise ValueError(f"Invalid filename: {filename!r}")
    workspace = get_user_workspace(user_id).resolve()
    resolved = (workspace / filename).resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"Path traversal detected: {filename!r}")
    return resolved


def write_input_files(user_id: int, files: list[FilePayload]) -> list[str]:
    ensure_workspace(user_id)
    names: list[str] = []
    for f in files:
        path = _safe_path(user_id, f.filename)
        path.write_bytes(base64.b64decode(f.content_b64))
        names.append(f.filename)
        logger.debug("Wrote input file %s for user %d", f.filename, user_id)
    return names


def read_output_files(user_id: int, filenames: list[str]) -> list[FilePayload]:
    result: list[FilePayload] = []
    for name in filenames:
        try:
            path = _safe_path(user_id, name)
            if path.exists():
                data = path.read_bytes()
                result.append(FilePayload(filename=Path(name).name, content_b64=base64.b64encode(data).decode()))
        except (ValueError, OSError) as exc:
            logger.warning("Could not read output file %s: %s", name, exc)
    return result
