from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from aiohttp import ClientSession

from .config import Config
from .database import Database, now_iso


@dataclass(frozen=True)
class CreatedInvoice:
    invoice_id: int
    url: str


class CryptoPayError(RuntimeError):
    pass


class CryptoPayClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(self.config.crypto_pay_api_token)

    async def request(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.config.crypto_pay_api_token:
            raise CryptoPayError("CRYPTO_PAY_API_TOKEN is not configured")
        url = f"{self.config.crypto_pay_base_url}/{method}"
        headers = {"Crypto-Pay-API-Token": self.config.crypto_pay_api_token}
        async with ClientSession(headers=headers) as session:
            async with session.post(url, json=payload or {}) as response:
                data = await response.json(content_type=None)
        if not data.get("ok"):
            raise CryptoPayError(str(data.get("error") or data))
        return data["result"]

    async def create_invoice(self, *, user_id: int, amount: str, fiat: str, accepted_assets: str, expires_in: int) -> CreatedInvoice:
        result = await self.request(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": fiat,
                "accepted_assets": accepted_assets,
                "amount": amount,
                "description": f"Подписка на Telegram moderation bot на {self.config.subscription_days} дней",
                "payload": f"subscription:{user_id}:{now_iso()}",
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": expires_in,
            },
        )
        url = result.get("bot_invoice_url") or result.get("pay_url") or result.get("web_app_invoice_url")
        if not url:
            raise CryptoPayError("Invoice URL missing in Crypto Pay response")
        return CreatedInvoice(invoice_id=int(result["invoice_id"]), url=str(url))

    async def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        result = await self.request("getInvoices", {"invoice_ids": str(invoice_id), "count": 1})
        if isinstance(result, dict) and "items" in result:
            items = result["items"]
        else:
            items = result
        if not items:
            return None
        return dict(items[0])


def add_days(base_iso: str | None, days: int) -> str:
    now = datetime.now(timezone.utc)
    base = now
    if base_iso:
        try:
            parsed = datetime.fromisoformat(base_iso)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed > now:
                base = parsed
        except ValueError:
            pass
    return (base + timedelta(days=days)).isoformat(timespec="seconds")


async def check_pending_payments(database: Database, client: CryptoPayClient, config: Config) -> int:
    if not client.configured:
        return 0
    activated = 0
    for payment in database.list_pending_payments():
        invoice = await client.get_invoice(payment["provider_invoice_id"])
        if not invoice:
            continue
        status = invoice.get("status")
        database.update_payment_status(
            payment_id=payment["id"],
            status=str(status),
            raw_payload=invoice,
        )
        if status != "paid":
            continue
        current_until = database.get_subscription_until(payment["user_id"])
        valid_until = add_days(current_until, config.subscription_days)
        database.activate_subscription(
            user_id=payment["user_id"],
            valid_until=valid_until,
            payment_id=payment["id"],
        )
        activated += 1
    return activated


async def billing_watcher(database: Database, client: CryptoPayClient, config: Config) -> None:
    while True:
        try:
            await check_pending_payments(database, client, config)
        except Exception:
            pass
        await asyncio.sleep(max(15, config.subscription_check_interval_seconds))


def money_ok(amount: str) -> str:
    value = Decimal(amount)
    if value <= 0:
        raise ValueError("amount must be positive")
    return format(value, "f")
