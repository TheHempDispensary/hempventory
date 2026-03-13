from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
import aiosqlite

from app.auth import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


async def _get_stored_credentials(db: aiosqlite.Connection) -> tuple[str, str]:
    """Get username and hashed password from settings, or use defaults."""
    cursor = await db.execute("SELECT value FROM settings WHERE key = 'username'")
    row = await cursor.fetchone()
    username = row[0] if row else DEFAULT_USERNAME

    cursor = await db.execute("SELECT value FROM settings WHERE key = 'password_hash'")
    row = await cursor.fetchone()
    if row:
        password_hash = row[0]
    else:
        password_hash = hash_password(DEFAULT_PASSWORD)

    return username, password_hash


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: aiosqlite.Connection = Depends(get_db)):
    username, password_hash = await _get_stored_credentials(db)

    if request.username != username or not verify_password(request.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    access_token = create_access_token(data={"sub": username})
    return TokenResponse(access_token=access_token)


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {"username": user["username"]}


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    _, password_hash = await _get_stored_credentials(db)

    if not verify_password(request.current_password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    new_hash = hash_password(request.new_password)
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('password_hash', ?)",
        (new_hash,),
    )
    await db.commit()
    return {"message": "Password changed successfully"}
