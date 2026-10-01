import hashlib
import hmac
import os
import secrets
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from .models import Admin, Audit


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError('Use at least 12 characters')
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, salt, digest = encoded.split('$')
        return algo == 'scrypt' and hmac.compare_digest(
            hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1),
            bytes.fromhex(digest),
        )
    except (ValueError, TypeError):
        return False


def admin_for(request: Request, db: Session) -> Admin | None:
    admin_id = request.session.get('admin_id')
    return db.get(Admin, admin_id) if isinstance(admin_id, int) else None


def require_admin(request: Request, db: Session, *, owner=False) -> Admin:
    admin = admin_for(request, db)
    if not admin or not admin.active:
        raise HTTPException(401, 'Authentication required')
    if owner and admin.role != 'owner':
        raise HTTPException(403, 'Owner role required')
    return admin


def csrf(request: Request, value: str) -> None:
    expected = request.session.get('csrf', '')
    if not expected or not hmac.compare_digest(expected, value):
        raise HTTPException(403, 'CSRF verification failed')


def new_csrf(request: Request) -> str:
    if 'csrf' not in request.session:
        request.session['csrf'] = secrets.token_urlsafe(32)
    return request.session['csrf']


def audit(db: Session, action: str, target: str, admin_id: int | None = None, detail: str = ''):
    db.add(Audit(admin_id=admin_id, action=action, target=target, detail=detail[:2000]))
