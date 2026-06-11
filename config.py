import os
import json
import threading
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
USERS_FILE = BASE_DIR / "users.json"
FILE_OWNERSHIP_FILE = STORAGE_DIR / ".file_ownership.json"
LOCK_TIMEOUT = 30 * 60
DEFAULT_ENCODING = "utf-8"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)

if not USERS_FILE.exists():
    default_users = {
        "admin": {"password": "admin123", "home": "admin", "quota": 104857600},
        "user1": {"password": "user123", "home": "user1", "quota": 52428800},
        "user2": {"password": "user456", "home": "user2", "quota": 31457280},
    }
    USERS_FILE.write_text(json.dumps(default_users, indent=2), encoding=DEFAULT_ENCODING)

_ownership_lock = threading.RLock()
_ownership_cache: Optional[dict] = None


def load_users() -> dict:
    with open(USERS_FILE, "r", encoding=DEFAULT_ENCODING) as f:
        return json.load(f)


def get_user_home(username: str) -> Path:
    users = load_users()
    if username not in users:
        raise ValueError(f"User {username} not found")
    shared_dir = STORAGE_DIR / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    return shared_dir


def get_user_quota(username: str) -> int:
    users = load_users()
    if username not in users:
        raise ValueError(f"User {username} not found")
    return users[username].get("quota", 0)


def _load_ownership() -> dict:
    global _ownership_cache
    with _ownership_lock:
        if _ownership_cache is not None:
            return _ownership_cache
        if FILE_OWNERSHIP_FILE.exists():
            try:
                with open(FILE_OWNERSHIP_FILE, "r", encoding=DEFAULT_ENCODING) as f:
                    _ownership_cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                _ownership_cache = {}
        else:
            _ownership_cache = {}
        return _ownership_cache


def _save_ownership(ownership: dict) -> None:
    global _ownership_cache
    with _ownership_lock:
        _ownership_cache = ownership
        FILE_OWNERSHIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FILE_OWNERSHIP_FILE, "w", encoding=DEFAULT_ENCODING) as f:
            json.dump(ownership, f, indent=2)


def set_file_owner(fs_path: Path, username: str) -> None:
    with _ownership_lock:
        ownership = _load_ownership()
        key = str(fs_path.resolve())
        ownership[key] = username
        _save_ownership(ownership)


def get_file_owner(fs_path: Path) -> Optional[str]:
    with _ownership_lock:
        ownership = _load_ownership()
        key = str(fs_path.resolve())
        return ownership.get(key)


def remove_file_owner(fs_path: Path) -> None:
    with _ownership_lock:
        ownership = _load_ownership()
        key = str(fs_path.resolve())
        if key in ownership:
            del ownership[key]
            _save_ownership(ownership)


def transfer_file_ownership(src_path: Path, dest_path: Path, new_owner: Optional[str] = None) -> None:
    with _ownership_lock:
        ownership = _load_ownership()
        src_key = str(src_path.resolve())
        dest_key = str(dest_path.resolve())
        owner = new_owner if new_owner is not None else ownership.get(src_key)
        if src_key in ownership:
            del ownership[src_key]
        if owner:
            ownership[dest_key] = owner
        _save_ownership(ownership)


def _get_file_size(fs_path: Path) -> int:
    try:
        if fs_path.is_file():
            return fs_path.stat().st_size
    except OSError:
        pass
    return 0


def _calculate_dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.iterdir():
            if entry.is_file():
                total += _get_file_size(entry)
            elif entry.is_dir():
                total += _calculate_dir_size(entry)
    except OSError:
        pass
    return total


def get_user_usage(username: str) -> int:
    with _ownership_lock:
        ownership = _load_ownership()
        total = 0
        for path_str, owner in ownership.items():
            if owner == username:
                path = Path(path_str)
                if path.exists():
                    if path.is_file():
                        total += _get_file_size(path)
                    elif path.is_dir():
                        total += _calculate_dir_size(path)
        return total


def get_available_quota(username: str) -> int:
    quota = get_user_quota(username)
    if quota == 0:
        return -1
    used = get_user_usage(username)
    return max(0, quota - used)


def check_quota(username: str, additional_bytes: int) -> bool:
    quota = get_user_quota(username)
    if quota == 0:
        return True
    available = get_available_quota(username)
    return additional_bytes <= available
