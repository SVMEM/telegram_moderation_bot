from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseTest(unittest.TestCase):
    def test_chat_rule_whitelist_event_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Database(Path(tmpdir) / "bot.sqlite3")
            database.init_schema({111}, "test")
            database.seed_settings(
                {
                    "subscription_required": "true",
                    "subscription_price": "10.00",
                    "subscription_fiat": "USD",
                    "subscription_days": "30",
                }
            )

            self.assertTrue(database.is_owner(111))
            self.assertTrue(database.get_bool_setting("subscription_required"))
            self.assertEqual(database.get_setting("subscription_price"), "10.00")
            database.set_setting("subscription_price", "15.50", 111)
            self.assertEqual(database.get_setting("subscription_price"), "15.50")
            database.upsert_chat(
                chat_id=-1001,
                title="Test chat",
                chat_type="supergroup",
                owner_id=111,
                can_delete_messages=True,
                status="connected",
            )
            database.grant_chat_access(222, -1001, "admin")
            self.assertTrue(database.can_manage_chat(222, -1001))

            rule_id = database.add_rule(
                chat_id=-1001,
                name="казино",
                rule_type="exact",
                pattern="казино",
                action="delete",
                priority=100,
                created_by=111,
            )
            rules = database.list_rules(-1001, active_only=True)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].id, rule_id)

            entry_id = database.add_whitelist(
                chat_id=-1001,
                entry_type="expr",
                value="ключевая ставка",
                created_by=111,
            )
            whitelist = database.list_whitelist(-1001)
            self.assertEqual(whitelist[0].id, entry_id)

            database.add_event(
                chat_id=-1001,
                message_id=10,
                author_id=222,
                author_username="user",
                text_excerpt="казино",
                rule_id=rule_id,
                matched="казино",
                action="delete",
                result="deleted",
                error=None,
                is_edited=False,
                message_link=None,
                ml_score=0.91,
                ml_model_version="test",
            )
            self.assertEqual(database.stats(-1001)["deleted"], 1)
            self.assertEqual(database.stats(-1001)["ml_hits"], 1)

            payment_id = database.create_payment(
                user_id=222,
                provider_invoice_id=555,
                amount="10.00",
                fiat="USD",
                invoice_url="https://pay.example/invoice",
            )
            database.update_payment_status(payment_id=payment_id, status="paid", raw_payload={"status": "paid"})
            database.activate_subscription(
                user_id=222,
                valid_until="2999-01-01T00:00:00+00:00",
                payment_id=payment_id,
            )
            self.assertTrue(database.subscription_active(222))
            self.assertEqual(database.get_payment_by_invoice(555)["status"], "paid")


if __name__ == "__main__":
    unittest.main()
