from pydantic import BaseModel, EmailStr

class RequestLogin(BaseModel):
    email: EmailStr

class VerifyToken(BaseModel):
    token: str  # одноразовый токен входа
