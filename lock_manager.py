import time
import uuid
import threading
from pathlib import Path
from typing import Optional, Dict, List
from config import LOCK_TIMEOUT


class Lock:
    def __init__(
        self,
        path: str,
        token: str,
        scope: str,
        lock_type: str,
        owner: Optional[str],
        username: str,
        timeout: int = LOCK_TIMEOUT
    ):
        self.path = path
        self.token = token
        self.scope = scope
        self.type = lock_type
        self.owner = owner
        self.username = username
        self.timeout = timeout
        self.created_at = time.time()
        self.expires_at = self.created_at + timeout

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def refresh(self, timeout: Optional[int] = None) -> None:
        self.created_at = time.time()
        self.timeout = timeout or self.timeout
        self.expires_at = self.created_at + self.timeout


class LockManager:
    def __init__(self):
        self.locks: Dict[str, Lock] = {}
        self.lock = threading.RLock()

    def _cleanup_expired(self) -> None:
        with self.lock:
            expired = [token for token, lock in self.locks.items() if lock.is_expired()]
            for token in expired:
                del self.locks[token]

    def create_lock(
        self,
        path: str,
        scope: str,
        lock_type: str,
        owner: Optional[str],
        username: str,
        timeout: Optional[int] = None
    ) -> Lock:
        with self.lock:
            self._cleanup_expired()
            
            existing = self.get_lock_for_path(path)
            if existing and existing.scope == "exclusive":
                raise Exception("Resource already locked exclusively")
            if existing and existing.scope == "shared" and scope == "exclusive":
                raise Exception("Resource already locked, cannot upgrade to exclusive")
            
            token = f"urn:uuid:{uuid.uuid4()}"
            lock = Lock(
                path=path,
                token=token,
                scope=scope,
                lock_type=lock_type,
                owner=owner,
                username=username,
                timeout=timeout or LOCK_TIMEOUT
            )
            self.locks[token] = lock
            return lock

    def get_lock(self, token: str) -> Optional[Lock]:
        with self.lock:
            self._cleanup_expired()
            return self.locks.get(token)

    def get_lock_for_path(self, path: str) -> Optional[Lock]:
        with self.lock:
            self._cleanup_expired()
            path_obj = Path(path)
            for lock in self.locks.values():
                lock_path_obj = Path(lock.path)
                try:
                    path_obj.relative_to(lock_path_obj)
                    return lock
                except ValueError:
                    continue
            return None

    def get_locks_for_path(self, path: str) -> List[Lock]:
        with self.lock:
            self._cleanup_expired()
            result = []
            path_obj = Path(path)
            for lock in self.locks.values():
                lock_path_obj = Path(lock.path)
                try:
                    path_obj.relative_to(lock_path_obj)
                    result.append(lock)
                except ValueError:
                    continue
            return result

    def refresh_lock(self, token: str, timeout: Optional[int] = None) -> Optional[Lock]:
        with self.lock:
            lock = self.locks.get(token)
            if lock and not lock.is_expired():
                lock.refresh(timeout)
                return lock
            return None

    def release_lock(self, token: str) -> bool:
        with self.lock:
            if token in self.locks:
                del self.locks[token]
                return True
            return False

    def validate_lock(self, path: str, tokens: List[str], method: str) -> bool:
        with self.lock:
            self._cleanup_expired()
            locks = self.get_locks_for_path(path)
            if not locks:
                return True
            for lock in locks:
                if lock.token not in tokens:
                    if lock.scope == "exclusive":
                        return False
                    if method in ["PUT", "DELETE", "MOVE", "PROPPATCH"]:
                        return False
            return True

    def get_locks_by_username(self, username: str) -> List[Lock]:
        with self.lock:
            self._cleanup_expired()
            return [lock for lock in self.locks.values() if lock.username == username]


lock_manager = LockManager()
