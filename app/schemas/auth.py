from pydantic import BaseModel


class TokenUser(BaseModel):
    id: str
    usuario: str
    nome: str | None = None
    is_admin: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str
    user: TokenUser | None = None


class LoginRequest(BaseModel):
    username: str
    password: str
