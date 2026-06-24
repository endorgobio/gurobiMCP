from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    jwt_secret_key: str
    fernet_key: str
    idle_timeout_minutes: int = 15
    port_pool_start: int = 61100
    port_pool_end: int = 61200
    domain: str = ""
    db_path: str = "data/app.db"


settings = Config()
