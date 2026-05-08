from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    secret_key: str = "change-me"
    admin_username: str = "admin"
    admin_password: str = "changeme"
    token_expire_hours: int = 8

    class Config:
        env_file = ".env"

settings = Settings()
