"""Accounts, sessions and roles.

- Passwords: scrypt (standard library), per-user salt. Never stored or logged in clear.
- Sessions: HMAC-signed cookie (HttpOnly, SameSite=Strict, Secure when served over HTTPS),
  8-hour lifetime. The signing key comes from RELECTO_SECRET or is generated once into
  the data directory.
- Roles:
    admin        manage users and settings, see everything
    radiologist  read studies, edit and SIGN reports
    secretary    upload studies, fill indications, print signed reports — cannot sign
- Login is throttled per account and per address after repeated failures.

A centre that already has a directory (Keycloak, Active Directory) can put an OIDC proxy
in front later; the role checks below stay the same.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from .db import User, audit, data_dir, session_scope

ROLES = ("admin", "radiologist", "secretary")
COOKIE = "relecto_session"
SESSION_SECONDS = 8 * 3600
MIN_PASSWORD = 10

_failures: dict = defaultdict(list)


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, dk = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(got, base64.b64decode(dk))


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Le mot de passe doit contenir au moins {MIN_PASSWORD} caractères.")
    if password.lower() == password or not any(c.isdigit() for c in password):
        raise HTTPException(400, "Le mot de passe doit contenir une majuscule et un chiffre.")


# ---------- sessions ----------

def _secret() -> bytes:
    env = os.environ.get("RELECTO_SECRET")
    if env:
        return env.encode()
    path = data_dir() / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip().encode()


def make_token(user_id: str, now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + SESSION_SECONDS)
    payload = f"{user_id}.{exp}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str | None) -> str | None:
    if not token or token.count(".") != 2:
        return None
    user_id, exp, sig = token.split(".")
    good = hmac.new(_secret(), f"{user_id}.{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return user_id


# ---------- throttling ----------

def throttled(key: str, limit: int = 5, window: int = 900) -> bool:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < window]
    return len(_failures[key]) >= limit


def record_failure(key: str) -> None:
    _failures[key].append(time.time())


def clear_failures(key: str) -> None:
    _failures.pop(key, None)


# ---------- FastAPI dependencies ----------

class CurrentUser:
    def __init__(self, u: User):
        self.id, self.email, self.name, self.role = u.id, u.email, u.name, u.role
        self.title, self.centre_id = u.title, u.centre_id

    def to_dict(self):
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role, "title": self.title}


def current_user(request: Request) -> CurrentUser:
    user_id = read_token(request.cookies.get(COOKIE))
    if not user_id:
        raise HTTPException(401, "Non connecté")
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None or not u.active:
            raise HTTPException(401, "Compte désactivé")
        return CurrentUser(u)


def require(*roles):
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(403, "Action non autorisée pour votre rôle")
        return user
    return dep


def authenticate(email: str, password: str, address: str) -> User:
    email = email.strip().lower()
    for key in (f"u:{email}", f"ip:{address}"):
        if throttled(key, 5 if key.startswith("u:") else 20):
            raise HTTPException(429, "Trop de tentatives. Réessayez dans 15 minutes.")
    with session_scope() as s:
        u = s.query(User).filter(User.email == email).one_or_none()
        if u is None or not u.active or not verify_password(password, u.password_hash):
            record_failure(f"u:{email}")
            record_failure(f"ip:{address}")
            audit(s, "auth.failed", "user", u.id if u else None, address=address)
            raise HTTPException(401, "Email ou mot de passe incorrect")
        clear_failures(f"u:{email}")
        u.last_login_at = datetime.now(timezone.utc)
        audit(s, "auth.login", "user", u.id, user_id=u.id, address=address)
        return u


def create_user(email: str, name: str, role: str, password: str, title: str | None = None) -> str:
    if role not in ROLES:
        raise HTTPException(400, f"Rôle inconnu : {role}")
    check_password_policy(password)
    email = email.strip().lower()
    with session_scope() as s:
        if s.query(User).filter(User.email == email).count():
            raise HTTPException(409, "Cet email existe déjà")
        u = User(email=email, name=name.strip(), role=role, title=title, password_hash=hash_password(password))
        s.add(u)
        s.flush()
        audit(s, "user.created", "user", u.id, role=role)
        return u.id


def user_count() -> int:
    with session_scope() as s:
        return s.query(User).count()
