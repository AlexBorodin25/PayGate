from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stripe_secret_key: str = Field(alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(alias="STRIPE_WEBHOOK_SECRET")
    database_url: str = Field(alias="DATABASE_URL")
    app_base_url: str = Field(alias="APP_BASE_URL")
    orders_api_key: str = Field(alias="ORDERS_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("orders_api_key")
    @classmethod
    def orders_api_key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Orders API key cannot be blank")
        return value


settings = Settings()  # type: ignore[call-arg]
