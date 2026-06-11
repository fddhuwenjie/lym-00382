import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
USERS_FILE = BASE_DIR / "users.json"
LOCK_TIMEOUT = 30 * 60
DEFAULT_ENCODING = "utf-8"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)

if not USERS_FILE.exists():
    default_users = {
        "admin": {"password": "admin123", "home": "admin"},
        "user1": {"password": "user123", "home": "user1"},
        "user2": {"password": "user456", "home": "user2"},
    }
    USERS_FILE.write_text(json.dumps(default_users, indent=2), encoding=DEFAULT_ENCODING)


def load_users() -> dict:
    with open(USERS_FILE, "r", encoding=DEFAULT_ENCODING) as f:
        return json.load(f)


def get_user_home(username: str) -> Path:
    users = load_users()
    if username not in users:
        raise ValueError(f"User {username} not found")
    home_dir = STORAGE_DIR / users[username]["home"]
    home_dir.mkdir(parents=True, exist_ok=True)
    return home_dir
