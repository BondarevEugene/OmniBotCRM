import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')


def env_map(name: str) -> dict[str, str]:
    value = json.loads(os.getenv(name, '{}'))
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError(f'{name} must be a JSON object with string values')
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL', 'sqlite:///./crm_local.db'))
    session_secret: str = field(default_factory=lambda: os.getenv('SESSION_SECRET', ''))
    cookie_secure: bool = field(default_factory=lambda: os.getenv('COOKIE_SECURE', 'true').lower() == 'true')
    ingest_secrets: dict[str, str] = field(default_factory=lambda: env_map('BOT_INGEST_SECRETS'))
    bot_tokens: dict[str, str] = field(default_factory=lambda: env_map('BOT_TOKENS'))
    food_porn_source_database_url: str = field(default_factory=lambda: os.getenv('FOOD_PORN_SOURCE_DATABASE_URL', ''))
    portmone_login: str = field(default_factory=lambda: os.getenv('PORTMONE_LOGIN', ''))
    portmone_password: str = field(default_factory=lambda: os.getenv('PORTMONE_PASSWORD', ''))
    portmone_payee_id: str = field(default_factory=lambda: os.getenv('PORTMONE_PAYEE_ID', ''))
    wayforpay_merchant_account: str = field(default_factory=lambda: os.getenv('WAYFORPAY_MERCHANT_ACCOUNT', ''))
    wayforpay_secret_key: str = field(default_factory=lambda: os.getenv('WAYFORPAY_SECRET_KEY', ''))
    encryption_key: str = field(default_factory=lambda: os.getenv('CRM_ENCRYPTION_KEY', ''))
    neon_api_key: str = field(default_factory=lambda: os.getenv('NEON_API_KEY', ''))
    neon_org_id: str = field(default_factory=lambda: os.getenv('NEON_ORG_ID', ''))
    seller_name: str = field(default_factory=lambda: os.getenv('INVOICE_SELLER_NAME', ''))
    seller_details: str = field(default_factory=lambda: os.getenv('INVOICE_SELLER_DETAILS', ''))

    def validate(self) -> None:
        if len(self.session_secret) < 32 or self.session_secret.startswith('REPLACE_'):
            raise RuntimeError('SESSION_SECRET must be a random string with at least 32 characters')
        if not (self.database_url.startswith('sqlite:') or self.database_url.startswith('postgresql+psycopg://')):
            raise RuntimeError('Use sqlite: for local development or postgresql+psycopg:// for Neon')
        if self.encryption_key:
            from cryptography.fernet import Fernet
            try:
                Fernet(self.encryption_key.encode())
            except (ValueError, TypeError):
                raise RuntimeError('CRM_ENCRYPTION_KEY must be a valid Fernet key') from None


def settings() -> Settings:
    result = Settings()
    result.validate()
    return result
