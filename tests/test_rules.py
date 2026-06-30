from __future__ import annotations

import unittest

from app.rules import Rule, WhitelistEntry, evaluate_message, match_rule
from app.text import normalize_text


def rule(
    rule_id: int,
    pattern: str,
    rule_type: str = "exact",
    chat_id: int = -1001,
    priority: int = 100,
) -> Rule:
    return Rule(
        id=rule_id,
        chat_id=chat_id,
        name=pattern,
        rule_type=rule_type,
        pattern=pattern,
        priority=priority,
    )


class RuleMatchingTest(unittest.TestCase):
    def test_case_insensitive_exact_word(self) -> None:
        self.assertEqual(match_rule(rule(1, "казино"), "Посетите наше КаЗиНо"), "казино")

    def test_exact_does_not_match_word_part(self) -> None:
        self.assertIsNone(match_rule(rule(1, "банк"), "банкир пришел"))
        self.assertIsNone(match_rule(rule(1, "банк"), "банковский сервис"))

    def test_exact_handles_separated_bypass(self) -> None:
        self.assertEqual(match_rule(rule(1, "кредит"), "к р е д и т без справок"), "к р е д и т")
        self.assertEqual(match_rule(rule(1, "кредит"), "к-р-е-д-и-т"), "к-р-е-д-и-т")

    def test_exact_handles_digit_suffix_bypass(self) -> None:
        self.assertEqual(match_rule(rule(1, "кредит"), "кредит123"), "кредит")

    def test_confusable_latin_letter(self) -> None:
        self.assertIn("кредит", normalize_text("крeдит"))
        self.assertEqual(match_rule(rule(1, "кредит"), "крeдит"), "кредит")

    def test_contains(self) -> None:
        self.assertEqual(match_rule(rule(1, "крипто", "contains"), "криптовалюта"), "крипто")

    def test_phrase(self) -> None:
        self.assertEqual(
            match_rule(rule(1, "быстрый заработок", "phrase"), "это быстрый заработок сегодня"),
            "быстрый заработок",
        )

    def test_mask(self) -> None:
        self.assertEqual(match_rule(rule(1, "ставк*", "mask"), "ставками"), "ставками")

    def test_whitelist_expression_allows(self) -> None:
        evaluation = evaluate_message(
            chat_id=-1001,
            text="Ключевая ставка Центрального банка выросла",
            author_id=10,
            sender_chat_id=None,
            rules=[rule(1, "ставки", "contains")],
            whitelist=[WhitelistEntry(1, -1001, "expr", "ключевая ставка")],
        )
        self.assertTrue(evaluation.allowed)
        self.assertIsNone(evaluation.match)

    def test_whitelist_user_allows(self) -> None:
        evaluation = evaluate_message(
            chat_id=-1001,
            text="казино",
            author_id=777,
            sender_chat_id=None,
            rules=[rule(1, "казино")],
            whitelist=[WhitelistEntry(1, -1001, "user", "777")],
        )
        self.assertTrue(evaluation.allowed)

    def test_channel_isolation(self) -> None:
        evaluation = evaluate_message(
            chat_id=-1002,
            text="казино",
            author_id=1,
            sender_chat_id=None,
            rules=[rule(1, "казино", chat_id=-1001)],
            whitelist=[],
        )
        self.assertIsNone(evaluation.match)

    def test_priority_wins(self) -> None:
        evaluation = evaluate_message(
            chat_id=-1001,
            text="казино ставки",
            author_id=1,
            sender_chat_id=None,
            rules=[rule(1, "казино", priority=10), rule(2, "ставки", priority=200)],
            whitelist=[],
        )
        self.assertIsNotNone(evaluation.match)
        self.assertEqual(evaluation.match.rule.id, 2)


if __name__ == "__main__":
    unittest.main()
