import base64
from fastapi import Request, HTTPException, status
from typing import Optional, Tuple
from config import load_users


def parse_basic_auth(auth_header: str) -> Optional[Tuple[str, str]]:
    if not auth_header or not auth_header.startswith("Basic "):
        return None
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        return (username, password)
    except Exception:
        return None


async def authenticate(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    credentials = parse_basic_auth(auth_header)
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="WebDAV"'},
            detail="Authentication required"
        )
    
    username, password = credentials
    users = load_users()
    
    if username not in users or users[username]["password"] != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="WebDAV"'},
            detail="Invalid credentials"
        )
    
    return username
