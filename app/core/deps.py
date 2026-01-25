from typing import Annotated
import uuid

from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.security import verify_password
from app.db.database import get_db_session
from app.db.models import User, Tenant


DEFAULT_TECH_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
DEFAULT_MERCEARIA_TENANT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_admin_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Decodifica o JWT e retorna o usuário admin atual ou lança 401/403.

    Importante: o login já restringe a admins, mas este helper reforça a verificação.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id: str | None = payload.get("user_id")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise credentials_exception

    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not admin")

    return user


async def get_tenant_id(
    db: AsyncSession = Depends(get_db_session),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> uuid.UUID:
    """Resolve o tenant atual via header X-Tenant-Id.

    Fase 1 (compatível): se header não vier ou for inválido, cai para o primeiro tenant do banco.
    """
    if x_tenant_id:
        try:
            tid = uuid.UUID(x_tenant_id)
            if tid != DEFAULT_TECH_TENANT_ID:
                return tid
        except Exception:
            pass

    # Fallback seguro: nunca cair no tenant técnico 1111...
    result_pref = await db.execute(select(Tenant).where(Tenant.id == DEFAULT_MERCEARIA_TENANT_ID))
    tenant = result_pref.scalars().first()
    if not tenant:
        result = await db.execute(
            select(Tenant)
            .where(Tenant.id != DEFAULT_TECH_TENANT_ID)
            .order_by(Tenant.created_at)
        )
        tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=500, detail="Nenhum tenant configurado")

    return tenant.id
