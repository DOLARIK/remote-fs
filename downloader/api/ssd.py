import json
import shutil
import uuid
from pathlib import Path
from typing import Optional
from loguru import logger

from models import SSDInfo

VOLUMES_PATH = Path("/mnt/volumes")
SESSION_FILE = ".nc-downloader/session.json"
# These are macOS system volumes — never show them as selectable drives
_SYSTEM_NAMES = {"Macintosh HD", "Macintosh HD - Data", "Recovery", "VM", "Preboot", "Update"}


def list_ssds() -> list[SSDInfo]:
    if not VOLUMES_PATH.exists():
        logger.warning("Volume path /mnt/volumes not found — add /Volumes to Docker Desktop file sharing")
        return []

    drives = []
    for entry in sorted(VOLUMES_PATH.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in _SYSTEM_NAMES:
            continue
        try:
            usage = shutil.disk_usage(str(entry))
            session = _read_session(entry)
            drives.append(SSDInfo(
                name=entry.name,
                mount_path=str(entry),
                free_bytes=usage.free,
                total_bytes=usage.total,
                has_session=session is not None,
                session_id=session.get("session_id") if session else None,
                session_folder=session.get("nextcloud_folder") if session else None,
                session_ssd_uuid=session.get("ssd_uuid") if session else None,
            ))
        except (PermissionError, OSError) as e:
            logger.warning(f"Cannot read drive {entry.name}: {e}")

    return drives


def get_ssd(mount_path: str) -> Optional[SSDInfo]:
    path = Path(mount_path)
    if not path.exists():
        return None
    try:
        usage = shutil.disk_usage(str(path))
        session = _read_session(path)
        return SSDInfo(
            name=path.name,
            mount_path=str(path),
            free_bytes=usage.free,
            total_bytes=usage.total,
            has_session=session is not None,
            session_id=session.get("session_id") if session else None,
            session_folder=session.get("nextcloud_folder") if session else None,
            session_ssd_uuid=session.get("ssd_uuid") if session else None,
        )
    except (PermissionError, OSError):
        return None


def is_connected(mount_path: str) -> bool:
    return Path(mount_path).exists()


def write_session(mount_path: str, session_id: str, ssd_uuid: str, nextcloud_folder: str, nextcloud_url: str) -> None:
    path = Path(mount_path) / SESSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "session_id": session_id,
        "ssd_uuid": ssd_uuid,
        "nextcloud_url": nextcloud_url,
        "nextcloud_folder": nextcloud_folder,
    }, indent=2))
    logger.info(f"Session fingerprint written to {path}")


def new_uuid() -> str:
    return str(uuid.uuid4())


def _read_session(drive_path: Path) -> Optional[dict]:
    f = drive_path / SESSION_FILE
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
