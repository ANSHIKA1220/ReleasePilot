import hashlib
import hmac
import os
import secrets
import time
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from . import db

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1,
                         maxmem=64*1024*1024)
    return salt + "." + key.hex()

def verify(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(".")[0]), encoded)

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

bearer = HTTPBearer(auto_error=False)

def identity(auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if auth:
        with db.transaction() as conn:
            row = conn.execute(select(db.users.c.id, db.users.c.email, db.users.c.role)
                .join(db.sessions).where(db.sessions.c.token_hash == digest(auth.credentials),
                                         db.sessions.c.expires > time.time())).mappings().first()
            if row:
                return dict(row)
    raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate":"Bearer"})

def roles(*allowed):
    def check(user=Depends(identity)):
        if user["role"] not in allowed:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return check
