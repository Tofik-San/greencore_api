from pydantic import BaseModel, EmailStr

class EmailAuthRequest(BaseModel):
    email: EmailStr

class EmailAuthVerify(BaseModel):
    email: EmailStr
    code: str
