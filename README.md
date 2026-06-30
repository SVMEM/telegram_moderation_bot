# Telegram moderation bot

Бот автоматически проверяет новые и отредактированные сообщения в группах, супергруппах, каналах и обсуждениях. MVP покрывает подключение чатов, проверку прав, чёрный список, whitelist выражений и пользователей, активный/тестовый режим, удаление, уведомления, журнал и базовую статистику.

## Запуск

1. Создайте бота через BotFather.
2. Скопируйте `.env.example` в `.env`.
3. Заполните:
   - `BOT_TOKEN` — токен бота.
   - `BOT_OWNER_IDS` — Telegram ID владельцев через запятую.
   - `BOT_DEFAULT_MODE=test` — безопасный режим без удаления. Для удаления включите `active` командой `/mode`.
   - `CRYPTO_PAY_API_TOKEN` — токен Crypto Pay, если нужна подписка через крипту.
   - `SUBSCRIPTION_REQUIRED=true` — включить обязательную месячную подписку.
4. Установите зависимости:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

5. Запустите:

```bash
python -m app.main
```

Docker:

```bash
docker compose up -d --build
```

## Права Telegram

Бот должен быть администратором в каждом модерируемом канале или группе. Минимально нужно право удаления сообщений. Для комментариев к каналу добавьте бота и в привязанную группу обсуждений.

## Основные команды

- `/start` — меню с кнопками.
- `/help` — инструкция.
- `/connect <chat_id|@username>` — подключить канал или группу. В группе можно отправить `/connect` без аргумента.
- `/channels` — подключённые чаты.
- `/addword <chat_id> <exact|contains|phrase|mask|regex> <слово или выражение>` — добавить правило.
- `/rules [chat_id]` — правила.
- `/delword <rule_id>` — удалить правило.
- `/whitelist add_expr <chat_id> <выражение>` — добавить разрешённое выражение.
- `/whitelist add_user <chat_id> <telegram_user_id>` — добавить пользователя в whitelist.
- `/whitelist del <id>` — удалить исключение.
- `/mode <chat_id> <test|active|disabled>` — режим чата.
- `/test <chat_id> <текст>` — проверить текст без удаления и без записи нарушения.
- `/logs [chat_id] [limit]` — журнал.
- `/stats [chat_id]` — статистика.
- `/status` — состояние.
- `/subscription` — статус подписки.
- `/subscribe` — создать счёт на месяц через Crypto Pay.
- `/check_payment [invoice_id]` — проверить оплату.
- `/ml <chat_id> <on|off> [threshold]` — включить или выключить ML-антиспам для чата.
- `/grant_admin <user_id> [chat_id]` — выдать права админа владельцем.
- `/revoke_admin <user_id> [chat_id]` — снять права админа владельцем.

## Типы правил

- `exact` — отдельное слово: `банк` не срабатывает на `банкир`.
- `contains` — вхождение: `крипто` срабатывает на `криптовалюта`.
- `phrase` — словосочетание.
- `mask` — маска со `*`, например `ставк*`.
- `regex` — регулярное выражение. Доступно только владельцу или глобальному администратору.

## Режимы

- `test` — ничего не удаляет, но пишет срабатывания в журнал.
- `active` — удаляет сообщения по правилам с действием `delete`.
- `disabled` — не проверяет сообщения.

По умолчанию используется `BOT_DEFAULT_MODE`, в примере это `test`, чтобы первое подключение было безопасным.

## Подписка через крипту

Реализовано через Crypto Pay от `@CryptoBot`: бот создаёт invoice, отдаёт ссылку пользователю и затем проверяет статус через API. Веб-сервер и webhook не нужны, потому что есть фоновая polling-проверка и ручная команда `/check_payment`.

Поля `.env`:

```env
CRYPTO_PAY_API_TOKEN=токен_crypto_pay
CRYPTO_PAY_TESTNET=false
SUBSCRIPTION_REQUIRED=true
SUBSCRIPTION_BYPASS_OWNER=true
SUBSCRIPTION_PRICE=10.00
SUBSCRIPTION_FIAT=USD
SUBSCRIPTION_ACCEPTED_ASSETS=USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC
SUBSCRIPTION_DAYS=30
```

Команды:

```text
/subscription
/subscribe
/check_payment <invoice_id>
```

Если `SUBSCRIPTION_REQUIRED=true`, команды управления и модерация чатов требуют активную подписку владельца чата. Владельцы из `BOT_OWNER_IDS` обходят оплату, если `SUBSCRIPTION_BYPASS_OWNER=true`.

## ML-антиспам

Модель лежит в `data/ml/spam_model.joblib`. Она обучается локально:

```bash
python3 scripts/train_spam_model.py
```

Источник данных: UCI SMS Spam Collection + русские seed-примеры Telegram-рекламы и обычных сообщений. Это не внешняя API-модель, а локальный `scikit-learn` Pipeline (`TF-IDF` + `LogisticRegression`).

Поля `.env`:

```env
ML_ENABLED=true
ML_MODEL_PATH=data/ml/spam_model.joblib
ML_SPAM_THRESHOLD=0.55
ML_ACTION=delete
ML_MIN_TEXT_LENGTH=8
```

Команды:

```text
/ml <chat_id> on 0.55
/ml <chat_id> off
/test <chat_id> казино бонус перейди по ссылке
```
