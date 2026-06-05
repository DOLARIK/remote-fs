from pydantic import BaseModel
from enum import Enum
from typing import Optional


class DownloadStatus(str, Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class FileStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # already on disk with correct size


class NCItem(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int = 0


class SSDInfo(BaseModel):
    name: str
    mount_path: str
    free_bytes: int
    total_bytes: int
    has_session: bool
    session_id: Optional[str] = None
    session_folder: Optional[str] = None
    session_ssd_uuid: Optional[str] = None


class SessionInfo(BaseModel):
    id: str
    ssd_path: str
    ssd_uuid: str
    nextcloud_folder: str
    total_files: int
    total_bytes: int
    downloaded_files: int
    downloaded_bytes: int
    failed_files: int
    skipped_files: int
    status: DownloadStatus
    speed_bps: float
    eta_seconds: Optional[int]
    current_files: list[str]
    created_at: str
    updated_at: str


class StartRequest(BaseModel):
    ssd_path: str
    nextcloud_folder: str
    force: bool = False  # override wrong-SSD warning
