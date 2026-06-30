from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        value = chunk.strip()
        if not value:
            continue
        ids.add(int(value))
    return ids


def _parse_bool(raw: str, default: bool) -> bool:
    if raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_ids: set[int]
    database_path: Path
    default_mode: str
    store_message_text: bool
    log_retention_days: int | None
    crypto_pay_api_token: str
    crypto_pay_base_url: str
    subscription_required: bool
    subscription_bypass_owner: bool
    subscription_price: str
    subscription_fiat: str
    subscription_accepted_assets: str
    subscription_days: int
    subscription_invoice_expires_seconds: int
    subscription_check_interval_seconds: int
    ml_enabled: bool
    ml_model_path: Path
    ml_spam_threshold: float
    ml_action: str
    ml_min_text_length: int


def load_config() -> Config:
    _load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    owner_ids = _parse_ids(os.getenv("BOT_OWNER_IDS", ""))
    if not owner_ids:
        raise RuntimeError("BOT_OWNER_IDS is required")

    default_mode = os.getenv("BOT_DEFAULT_MODE", "test").strip().lower()
    if default_mode not in {"test", "active", "disabled"}:
        raise RuntimeError("BOT_DEFAULT_MODE must be one of: test, active, disabled")

    retention_raw = os.getenv("LOG_RETENTION_DAYS", "30").strip()
    retention = None if retention_raw in {"", "0", "none", "forever"} else int(retention_raw)
    crypto_testnet = _parse_bool(os.getenv("CRYPTO_PAY_TESTNET", "false"), False)

    return Config(
        bot_token=bot_token,
        owner_ids=owner_ids,
        database_path=Path(os.getenv("BOT_DATABASE_PATH", "data/bot.sqlite3")),
        default_mode=default_mode,
        store_message_text=_parse_bool(os.getenv("STORE_MESSAGE_TEXT", "true"), True),
        log_retention_days=retention,
        crypto_pay_api_token=os.getenv("CRYPTO_PAY_API_TOKEN", "").strip(),
        crypto_pay_base_url="https://testnet-pay.crypt.bot/api" if crypto_testnet else "https://pay.crypt.bot/api",
        subscription_required=_parse_bool(os.getenv("SUBSCRIPTION_REQUIRED", "false"), False),
        subscription_bypass_owner=_parse_bool(os.getenv("SUBSCRIPTION_BYPASS_OWNER", "true"), True),
        subscription_price=os.getenv("SUBSCRIPTION_PRICE", "10.00").strip(),
        subscription_fiat=os.getenv("SUBSCRIPTION_FIAT", "USD").strip().upper(),
        subscription_accepted_assets=os.getenv(
            "SUBSCRIPTION_ACCEPTED_ASSETS",
            "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC",
        ).strip().upper(),
        subscription_days=int(os.getenv("SUBSCRIPTION_DAYS", "30")),
        subscription_invoice_expires_seconds=int(os.getenv("SUBSCRIPTION_INVOICE_EXPIRES_SECONDS", "86400")),
        subscription_check_interval_seconds=int(os.getenv("SUBSCRIPTION_CHECK_INTERVAL_SECONDS", "60")),
        ml_enabled=_parse_bool(os.getenv("ML_ENABLED", "true"), True),
        ml_model_path=Path(os.getenv("ML_MODEL_PATH", "data/ml/spam_model.joblib")),
        ml_spam_threshold=float(os.getenv("ML_SPAM_THRESHOLD", "0.55")),
        ml_action=os.getenv("ML_ACTION", "delete").strip().lower(),
        ml_min_text_length=int(os.getenv("ML_MIN_TEXT_LENGTH", "8")),
    )
