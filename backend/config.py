from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    DB_NAME: str = "voxassist"
    FIREBASE_CREDENTIALS_PATH: str = "service-account-key.json"
    SECRET_KEY: str = "default_secret"
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:4173",
    ]
    FRONTEND_URL: str = "http://localhost:5173"
    MAX_PROMPT_LENGTH: int = 5000

    model_config = SettingsConfigDict(
        env_file="backend/.env",  # 👈 KEEP IT HERE
        extra="ignore"
    )


settings = Settings()