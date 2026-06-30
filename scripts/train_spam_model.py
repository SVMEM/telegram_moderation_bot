from __future__ import annotations

import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

DATA_URL = "https://archive.ics.uci.edu/static/public/228/sms%2Bspam%2Bcollection.zip"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "ml"
MODEL_PATH = DATA_DIR / "spam_model.joblib"
REPORT_PATH = DATA_DIR / "spam_model_report.json"

RU_SEED = [
    ("spam", "быстрый заработок без вложений переходи по ссылке"),
    ("spam", "быстрый заработок в интернете без опыта"),
    ("spam", "заработай сегодня 5000 рублей пиши в личку"),
    ("spam", "подработка онлайн ежедневные выплаты"),
    ("spam", "легкие деньги за пару часов"),
    ("spam", "доход от 10000 в день без вложений"),
    ("spam", "пассивный доход инвестиции прибыль гарантирована"),
    ("spam", "казино бонус 5000 рублей регистрируйся сейчас"),
    ("spam", "онлайн казино фриспины бонус за регистрацию"),
    ("spam", "забирай бонус в казино ссылка ниже"),
    ("spam", "выигрывай деньги играй в казино"),
    ("spam", "ставки на спорт гарантированный доход"),
    ("spam", "букмекер лучшие коэффициенты переходи"),
    ("spam", "ставки экспресс прогнозы спорт"),
    ("spam", "договорные матчи инсайды ставки"),
    ("spam", "крипта памп сигнал заработай сегодня"),
    ("spam", "крипто сигнал покупай монету сейчас"),
    ("spam", "инвестируй в криптовалюту доход каждый день"),
    ("spam", "приватный крипто канал памп"),
    ("spam", "кредит без отказа срочно деньги"),
    ("spam", "займ на карту без проверки"),
    ("spam", "деньги до зарплаты одобрение всем"),
    ("spam", "микрозайм моментально без документов"),
    ("spam", "подпишись на канал и получи приз"),
    ("spam", "переходи в наш телеграм канал розыгрыш"),
    ("spam", "подписка обязательна конкурс подарки"),
    ("spam", "дешевые подписчики телеграм накрутка просмотров"),
    ("spam", "накрутка подписчиков лайков просмотров дешево"),
    ("spam", "продвижение канала боты подписчики"),
    ("spam", "инвестиции прибыль гарантированно напиши в личку"),
    ("spam", "розыгрыш денег перейди по ссылке"),
    ("spam", "работа онлайн доход каждый день"),
    ("spam", "напиши плюс и получи инструкцию"),
    ("spam", "скидка только сегодня переходи по ссылке"),
    ("spam", "акция ограничена успей забрать подарок"),
    ("spam", "t.me канал заработок ставки казино"),
    ("spam", "https://example.com бонус заработок регистрация"),
    ("spam", "приглашаем в закрытый канал с сигналами"),
    ("spam", "выплаты каждый день без вложений"),
    ("spam", "бесплатная консультация по заработку"),
    ("spam", "ищем людей для удаленной работы высокий доход"),
    ("spam", "гарантия прибыли 100 процентов"),
    ("ham", "ключевая ставка центрального банка изменилась"),
    ("ham", "процентные ставки банка опубликованы в отчете"),
    ("ham", "обсудим инвестиции компании на совете директоров"),
    ("ham", "нужно проверить ссылку на документ"),
    ("ham", "канал проекта обновлен новая инструкция"),
    ("ham", "ставки налогов изменились с января"),
    ("ham", "кредитный договор лежит в папке документов"),
    ("ham", "бюджет проекта согласован с командой"),
    ("ham", "сегодня встреча команды в 18 часов"),
    ("ham", "пожалуйста отправьте отчет после созвона"),
    ("ham", "обновили расписание занятий на завтра"),
    ("ham", "спасибо за комментарий поправлю текст"),
    ("ham", "документы готовы можно проверять"),
    ("ham", "видео добавлено в общий канал"),
    ("ham", "напомни пожалуйста про дедлайн"),
    ("ham", "завтра обсудим план проекта"),
    ("ham", "новая версия бота запущена в тестовом режиме"),
    ("ham", "проверь пожалуйста сообщения в тестовой группе"),
    ("ham", "ссылка на календарь в описании задачи"),
    ("ham", "подписчики канала оставили комментарии"),
    ("ham", "заработок компании снизился по отчету"),
    ("ham", "прибыль за квартал нужно добавить в презентацию"),
    ("ham", "криптовалюта обсуждается в учебном материале"),
]


def download_dataset() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cached = DATA_DIR / "sms_spam_collection.zip"
    if cached.exists():
        return cached.read_bytes()
    with urllib.request.urlopen(DATA_URL, timeout=60) as response:
        content = response.read()
    cached.write_bytes(content)
    return content


def load_rows(content: bytes) -> list[tuple[str, str]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        raw = archive.read("SMSSpamCollection").decode("utf-8", errors="replace")
    rows: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        label, text = line.split("\t", 1)
        if label in {"ham", "spam"}:
            rows.append((label, text))
    rows.extend(RU_SEED)
    return rows


def main() -> int:
    content = download_dataset()
    rows = load_rows(content)
    texts = [text for _, text in rows]
    labels = [1 if label == "spam" else 0 for label, _ in rows]
    train_x, test_x, train_y, test_y = train_test_split(
        texts,
        labels,
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )
    model = Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    lowercase=True,
                    min_df=1,
                    sublinear_tf=True,
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )
    model.fit(train_x, train_y)
    predictions = model.predict(test_x)
    report = classification_report(test_y, predictions, target_names=["ham", "spam"], output_dict=True)
    payload = {
        "model": model,
        "version": "uci_sms_spam_v1_ru_seed",
        "source": DATA_URL,
        "rows": len(rows),
        "external_rows": len(rows) - len(RU_SEED),
        "ru_seed_rows": len(RU_SEED),
    }
    joblib.dump(payload, MODEL_PATH)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {MODEL_PATH}")
    print(json.dumps({"rows": len(rows), "report": report["spam"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
