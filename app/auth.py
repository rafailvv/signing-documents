from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from time import monotonic

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import Settings
from .db_models import User, UserAssets


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"
DUMMY_PASSWORD_HASH = pwd_context.hash("dummy-password")


class AuthRateLimiter:
    def __init__(self, *, limit: int = 8, window_seconds: int = 300) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = monotonic()
        attempts = self._attempts[key]
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        if len(attempts) >= self.limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many attempts")
        attempts.append(now)

    def clear(self, key: str) -> None:
        self._attempts.pop(key, None)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(*, user_id: int, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, *, settings: Settings) -> int:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        return int(payload.get("sub", ""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


def auth_rate_limit_key(request: Request, *, action: str, login: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",", 1)[0].strip() or (request.client.host if request.client else "unknown")
    return f"{action}:{ip}:{login.strip().lower()}"


def user_to_auth_payload(user: User):
    from .models import AuthUser

    assets = user.assets
    return AuthUser(
        id=user.id,
        login=user.login,
        email=user.email,
        has_signature=bool(assets and assets.signature_png),
        has_stamp=bool(assets and assets.stamp_png),
    )


def ensure_assets(db: Session, user: User) -> UserAssets:
    if user.assets:
        return user.assets
    assets = UserAssets(user_id=user.id)
    db.add(assets)
    db.flush()
    db.refresh(user)
    return assets


def make_current_user_dependency(*, settings: Settings, get_db):
    def current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        db: Session = Depends(get_db),
    ) -> User:
        if not settings.auth_required:
            user = db.query(User).filter(User.id == 0).first()
            if user is None:
                user = User(id=0, login="local", password_hash=hash_password("local-password"))
                db.add(user)
                db.commit()
                db.refresh(user)
            return user

        if credentials is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        user_id = decode_access_token(credentials.credentials, settings=settings)
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return user

    return current_user
