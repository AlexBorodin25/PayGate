from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    stripe_secret_key: str = Field(alias="STRIPE_SECRET_KEY")
    stripe_webhook_url: str = Field(alias="STRIPE_WEBHOOK_URL")
    database_url: str = Field(alias="DATABASE_URL")
    app_base_url: str = Field(alias="APP_BASE_URL")
    orders_api_key: str = Field(alias="ORDERS_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()