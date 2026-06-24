from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    access_id: str
    gurobi_secret: str


class SignupResponse(BaseModel):
    id: int
    username: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
