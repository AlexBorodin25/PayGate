from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stripe_secret_key: str = Field(alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(alias="STRIPE_WEBHOOK_SECRET")
    database_url: str = Field(alias="DATABASE_URL")
    app_base_url: str = Field(alias="APP_BASE_URL")
    orders_api_key: str = Field(alias="ORDERS_API_KEY")
    qstash_token: str
    internal_fulfillment_secret: str
    qstash_current_signing_key: str
    qstash_next_signing_key: str
    internal_fulfillment_next_secret: str | None = None
    qstash_publish_url: str = Field(
        default="https://qstash.upstash.io/v2/publish",
        alias="QSTASH_PUBLISH_URL",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator(
        "orders_api_key",
        "qstash_token",
        "internal_fulfillment_secret",
        "qstash_current_signing_key",
        "qstash_next_signing_key",
    )
    @classmethod
    def required_secret_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Required secret cannot be blank")
        return value

    @field_validator("app_base_url")
    @classmethod
    def app_base_url_without_trailing_slash(cls, value: str) -> str:
        cleaned_value = value.strip().rstrip("/")

        if not cleaned_value:
            raise ValueError("APP_BASE_URL cannot be blank")

        return cleaned_value

    @field_validator("qstash_publish_url")
    @classmethod
    def qstash_publish_url_without_trailing_slash(cls, value: str) -> str:
        cleaned_value = value.strip().rstrip("/")

        if not cleaned_value:
            raise ValueError("QSTASH_PUBLISH_URL cannot be blank")

        return cleaned_value


settings = Settings()  # type: ignore[call-arg]
