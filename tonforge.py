# -*- coding: utf-8 -*-
# ================================================================
#   TONFORGE // консольный менеджер кошельков TON + USDT
#   Зависимости:   pip install tonutils cryptography
#   Запуск:        python tonforge.py
# ----------------------------------------------------------------
#   • создаёт кошельки пачками по 10 шт и хранит seed-фразы
#     в зашифрованном паролем файле-хранилище wallets.vault
#   • показывает балансы TON и USDT по каждому кошельку
#   • переводит TON / USDT с любого кошелька на любой адрес
#   • собирает средства со всех кошельков на один адрес
#   • экспорт адресов в CSV, импорт seed-фраз, карточки кошельков
#   • история переводов показывает ПОЛНЫЕ адреса (friendly + raw)
#     и имена своих кошельков — сразу видно, от кого пришло и кому ушло
#   • пункт [11] — переключение языка интерфейса RU/EN
#     (выбор хранится в tonforge.config.json рядом со скриптом)
#   Интерфейс страничный: экран очищается перед каждым шагом,
#   поэтому в консоли нет бесконечной ленты сообщений.
# ================================================================

import asyncio
import base64
import csv
import getpass
import json
import os
import re
import secrets
import shutil
import sys
import time
import warnings
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

# requests — единственная не-tonutils сетевая зависимость: используется только
# для tonapi.io/v2/accounts/{addr}/events, который отдаёт УЖЕ РАСШИФРОВАННУЮ
# историю входящих/исходящих переводов (кто, сколько, кому). tonutils.get_transactions()
# отдаёт только сырые BOC транзакций, разбирать их вручную под TON+jetton-переводы —
# отдельная большая и рискованная для точности денежных сумм задача, поэтому для
# истории транзакций используется публичный индексатор, а не низкоуровневый парсинг.
import requests

if sys.version_info < (3, 9):
    sys.exit("Нужен Python 3.9 или новее / Python 3.9 or newer is required")

from ton_core import Address, NetworkGlobalID, to_nano
from tonutils.clients import ToncenterClient
from tonutils.contracts import (
    JettonTransferBuilder,
    SeqnoGuard,
    WalletV3R2,
    WalletV4R2,
    WalletV5R1,
    get_wallet_address_get_method,
)
from tonutils.types import DEFAULT_HTTP_RETRY_POLICY

# ══════════════════════ НАСТРОЙКИ ══════════════════════
NETWORK           = "mainnet"        # mainnet | testnet
TONCENTER_API_KEY = ""   # ключ от @toncenter; без ключа ~1 запрос в 1.3 с
RPS_LIMIT         = 10          # лимит запросов/с — применяется только с ключом
WALLET_VERSION    = "v4r2"     # v3r2 | v4r2 | v5r1
BATCH_SIZE        = 10          # сколько кошельков создавать за раз
NAME_PREFIX       = "worker"   # имена: worker-01, worker-02, …
VAULT_FILE        = "wallets.vault"  # файл-хранилище seed-фраз
CONFIG_FILE       = "tonforge.config.json"  # файл мелких настроек (язык)
ENCRYPT_VAULT     = True       # шифровать хранилище паролем (нужен пакет cryptography)
JETTON_GAS_TON    = 0.05       # TON, прикладываемые к переводу USDT (излишек вернётся)
SWEEP_RESERVE_TON = 0       # сколько TON оставлять при сборе (0 = забрать всё)
CONFIRM_SENDS     = True       # спрашивать подтверждение перед каждой отправкой
EXPLORER          = "tonviewer"  # tonviewer | tonscan
ROWS_PER_PAGE     = 15          # строк списка на одной странице
TONAPI_API_KEY    = ""          # ключ tonapi.io (необязательно, поднимает лимиты)
TX_HISTORY_LIMIT  = 30          # сколько последних событий запрашивать для истории

# ═══════════════════════ КОНСТАНТЫ ═══════════════════════
IS_TESTNET = NETWORK == "testnet"
TONAPI_BASE = "https://testnet.tonapi.io" if IS_TESTNET else "https://tonapi.io"
USDT_MASTER = (
    "kQB0ZYUL5M3KfrW0tSnwdFO1nC-BQHC2gcZl-WaF2on_USDT"
    if IS_TESTNET
    else "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
)
USDT_DECIMALS = 6
TON_DECIMALS = 9
# не даём этой величине провалиться ниже разумного порога, даже если
# кто-то поменяет JETTON_GAS_TON на 0 в настройках
MIN_TON_FOR_JETTON = max(to_nano(JETTON_GAS_TON) + to_nano(0.01), to_nano(0.05))
# отдельный, более строгий порог для массового сбора (см. screen_sweep):
# там за одной операцией часто следует ещё и перевод TON, нужен запас побольше
MIN_SAFE_JETTON_SWEEP = to_nano(0.1)
WALLET_CLASSES = {"v3r2": WalletV3R2, "v4r2": WalletV4R2, "v5r1": WalletV5R1}
STATE_RU = {
    "nonexist": "новый",
    "uninit": "не развёрнут",
    "active": "активен",
    "frozen": "заморожен",
}

# одна запись истории занимает ДВЕ строки: на первой — дата и сумма,
# на второй — ПОЛНЫЙ адрес контрагента (48 символов)
TX_ROWS_PER_PAGE = 7


def _page_width():
    """Ширина «страницы» подстраивается под терминал, но не уже 84 (полные
    адреса обрезались бы) и не шире 96 — окно держим компактным."""
    try:
        return min(96, max(84, shutil.get_terminal_size(fallback=(92, 24)).columns - 2))
    except Exception:
        return 92


W = _page_width()  # ширина «страницы» в символах (адаптивная)


# ═══════════════════════ ЯЗЫК / LANGUAGE ═══════════════════════
# ВСЕ пользовательские строки собраны здесь: tr("русский текст") вернёт
# английский вариант, когда включен English. Если перевода для строки нет —
# показывается исходный русский текст (ничего не ломается).
LANG = "ru"  # "ru" | "en" — читается из CONFIG_FILE при старте

EN = {
    # ── вход / хранилище ─────────────────────────────────────────────
    "Нужен Python 3.9 или новее": "Python 3.9 or newer is required",
    "файл хранилища повреждён (битый JSON): {e}": "vault file is corrupted (broken JSON): {e}",
    "файл хранилища повреждён (неожиданный формат)": "vault file is corrupted (unexpected format)",
    "неверный пароль": "wrong password",
    "  Файл хранилища: ": "  Vault file: ",
    "  Сеть: ": "  Network: ",
    "   версия кошельков: ": "   wallet version: ",
    "   хранилище: ": "   vault: ",
    "  Хранилище найдено. Введи пароль, чтобы расшифровать.": "  Vault found. Enter the password to decrypt it.",
    "  Хранилище найдено. Открываю…": "  Vault found. Opening…",
    "вход": "sign in",
    "пароль хранилища": "vault password",
    "   ✖ неверный пароль ({a}/3)": "   ✖ wrong password ({a}/3)",
    "попробуй ещё раз": "try again",
    "   доступ закрыт\n": "   access denied\n",
    "внимание: хранилище создано для сети {a}, а сейчас {b}": "warning: the vault was created for {a}, current network is {b}",
    "хранилище открыто · кошельков: {n}": "vault opened · wallets: {n}",
    "  Хранилища ещё нет — создаём новое.": "  No vault yet — creating a new one.",
    "  Придумай пароль: он шифрует все seed-фразы. Потеряешь пароль —": "  Set a password: it encrypts all seed phrases. Lose the password —",
    "  потеряешь доступ к файлу (восстановить кошельки можно только по seed).": "  lose access to the file (wallets can only be recovered from seed phrases).",
    "новое хранилище": "new vault",
    "новый пароль": "new password",
    "   минимум 6 символов": "   minimum 6 characters",
    "повтори пароль": "repeat the password",
    "   пароли не совпадают": "   passwords do not match",
    "  Хранилища ещё нет — создаю новое ": "  No vault yet — creating a new one ",
    "(без шифрования!)": "(no encryption!)",
    "создано хранилище {f}": "vault {f} created",
    "  Для шифрования нужен пакет cryptography:  pip install cryptography": "  Encryption requires the cryptography package:  pip install cryptography",
    "  Либо поставь ENCRYPT_VAULT = False (хранилище будет открытым текстом).": "  Or set ENCRYPT_VAULT = False (the vault will be stored in plaintext).",
    "хранилище повреждено": "vault corrupted",
    "  Не удалось прочитать файл хранилища.": "  Could not read the vault file.",
    "  Файл: ": "  File: ",
    "  Если есть резервная копия файла — восстанови её и запусти снова.": "  If you have a backup of the file — restore it and run again.",
    "  Свежее хранилище создастся только если переименовать/удалить этот файл —": "  A fresh vault is created only if you rename/delete this file —",
    "  но тогда старые кошельки останутся недоступны без сохранённых seed-фраз.": "  but then the old wallets stay unavailable without saved seed phrases.",
    "Enter — выход": "Enter — quit",
    # ── общее ────────────────────────────────────────────────────────
    "Enter — назад": "Enter — back",
    "Enter — отмена": "Enter — cancel",
    "выбор": "choice",
    "нет такого пункта": "no such item",
    "не понял команду": "didn't get the command",
    "сумма должна быть больше нуля": "amount must be greater than zero",
    "это не число": "not a number",
    "кошельков: {n}": "wallets: {n}",
    "новый": "new",
    "не развёрнут": "not deployed",
    "активен": "active",
    "заморожен": "frozen",
    "не проверен": "not checked",
    "балансы не запрашивались": "balances not fetched yet",
    # ── главное меню ─────────────────────────────────────────────────
    "главное меню": "main menu",
    "ИТОГО": "TOTAL",
    "обновлено {t}": "updated {t}",
    "балансы ещё не запрашивались": "balances not fetched yet",
    "Список кошельков": "Wallet list",
    "Создать пачку ({n} шт)": "Create batch ({n} pcs)",
    "Создать пачку": "Create batch",
    "Обновить балансы": "Refresh balances",
    "Перевод TON": "Send TON",
    "Перевод USDT": "Send USDT",
    "Собрать всё на один адрес": "Sweep all to one address",
    "Показать seed-фразу": "Show seed phrase",
    "Экспорт адресов в CSV": "Export addresses to CSV",
    "Импорт seed-фразы": "Import seed phrase",
    "История транзакций": "Transaction history",
    "Выход": "Exit",
    # ── список / выбор кошелька ──────────────────────────────────────
    "имя": "name",
    "адрес": "address",
    "состояние": "state",
    "кошельков пока нет — создай пачку в пункте 2": "no wallets yet — create a batch in item 2",
    "  … ещё {n} — номер можно ввести вручную": "  … and {n} more — the number can be typed manually",
    "  пусто — создай пачку в пункте [2]": "  empty — create a batch in item [2]",
    "  номер — карточка кошелька · n/p — листать · Enter — назад": "  number — wallet card · n/p — pages · Enter — back",
    "кошельки · стр. {a}/{b}": "wallets · page {a}/{b}",
    "история транзакций · выбор кошелька": "transaction history · pick a wallet",
    "выбери кошелёк, чтобы посмотреть его историю": "pick a wallet to view its history",
    "seed-фраза · выбор кошелька": "seed phrase · pick a wallet",
    "seed покажется только после подтверждения": "the seed will be shown only after confirmation",
    "номер кошелька (Enter — отмена)": "wallet number (Enter — cancel)",
    "введи номер кошелька-отправителя": "enter the sender wallet number",
    # ── карточка кошелька ────────────────────────────────────────────
    "кошелёк {name}": "wallet {name}",
    "   {v} · создан {d}": "   {v} · created {d}",
    "   проверено {t}": "   checked {t}",
    "   балансы не запрашивались": "   balances not fetched yet",
    "  Баланс:": "  Balance:",
    "  Статус:": "  State:",
    "Перевести TON": "Send TON",
    "Перевести USDT": "Send USDT",
    "Обновить баланс": "Refresh balance",
    "Переименовать": "Rename",
    "Удалить из хранилища": "Remove from vault",
    "  Enter — назад к списку": "  Enter — back to the list",
    "новое имя": "new name",
    "переименован": "renamed",
    "удаление": "deletion",
    "ВНИМАНИЕ": "WARNING",
    ": запись {name} будет удалена из хранилища.": ": the record {name} will be removed from the vault.",
    "  Если seed-фраза не сохранена отдельно — доступ к средствам будет потерян.": "  If the seed phrase is not saved separately — access to the funds will be lost.",
    "  Баланс сейчас: {t} TON / {u} USDT": "  Balance now: {t} TON / {u} USDT",
    "  Для подтверждения введи имя кошелька: ": "  To confirm, enter the wallet name: ",
    "удаление отменено": "deletion cancelled",
    "{name} удалён из хранилища": "{name} removed from the vault",
    # ── создание ─────────────────────────────────────────────────────
    "создание кошельков": "creating wallets",
    "  Seed-фразы генерируются локально и сразу сохраняются в хранилище.": "  Seed phrases are generated locally and saved to the vault right away.",
    "  Сколько кошельков создать? Enter — {n}.": "  How many wallets to create? Enter — {n}.",
    "количество": "count",
    "префикс имени": "name prefix",
    "нужно число от 1 до 500": "a number from 1 to 500 is required",
    "  Создано {n} кошельков, всего в хранилище: {m}": "  Created {n} wallets, {m} total in the vault",
    "  … и ещё {n} — смотри список [1]": "  … and {n} more — see list [1]",
    "  Адреса уже принимают TON и USDT; контракт развернётся при первой отправке.": "  Addresses already accept TON and USDT; the contract is deployed on the first send.",
    "  Чтобы кошелёк мог отправлять USDT, на нём должно быть ~0.1 TON на газ.": "  To send USDT, a wallet must hold ~0.1 TON for gas.",
    "✔ сохранено в {f}": "✔ saved to {f}",
    "готово": "done",
    # ── обновление балансов ──────────────────────────────────────────
    "обновление балансов": "refreshing balances",
    "  Запрашиваю {n} кошельков через toncenter…": "  Querying {n} wallets via toncenter…",
    "  без API-ключа это ~3 с на кошелёк; с ключом — доли секунды": "  without an API key it's ~3 s per wallet; with a key — fractions of a second",
    "нечего обновлять": "nothing to refresh",
    "балансы обновлены за {s} с": "balances refreshed in {s} s",
    " · ошибок: {n} (сеть или лимит запросов — повтори позже)": " · errors: {n} (network or rate limit — retry later)",
    # ── переводы ─────────────────────────────────────────────────────
    "перевод {l} · шаг 1/3 · откуда": "send {l} · step 1/3 · from",
    "перевод {l} · шаг 2/3 · куда и сколько": "send {l} · step 2/3 · where and how much",
    "перевод {l} · проверка баланса": "send {l} · balance check",
    "перевод {l} · шаг 3/3 · подтверждение": "send {l} · step 3/3 · confirmation",
    "отправлено": "sent",
    "перевод отменён": "transfer cancelled",
    "Откуда:": "From:",
    "Куда:": "To:",
    "Сумма:": "Amount:",
    "Комментарий:": "Comment:",
    "Сеть:": "Network:",
    "Кошелёк:": "Wallet:",
    "Баланс:": "Balance:",
    "(обновлён {t})": "(updated {t})",
    "  Адрес получателя — любой: UQ…, EQ…, 0Q…/kQ… (testnet) или raw 0:…": "  Recipient address — any: UQ…, EQ…, 0Q…/kQ… (testnet) or raw 0:…",
    "  Сумма в {l}; слово ": "  Amount in {l}; type ",
    " — отправить весь баланс.": " — send the whole balance.",
    "  К переводу USDT прикладывается {g} TON на газ, излишек вернётся.": "  Sending USDT attaches {g} TON for gas, the excess is returned.",
    "адрес получателя (Enter — отмена)": "recipient address (Enter — cancel)",
    "адрес не распознан: {d}": "address not recognized: {d}",
    "это testnet-адрес, а сейчас сеть mainnet — перевод отменён": "this is a testnet address, current network is mainnet — transfer cancelled",
    "это mainnet-адрес, а сейчас сеть testnet — перевод отменён": "this is a mainnet address, current network is testnet — transfer cancelled",
    "сумма {l}": "amount {l}",
    "комментарий (необязательно)": "comment (optional)",
    "  Запрашиваю актуальный баланс перед отправкой…": "  Fetching the current balance before sending…",
    "не удалось получить баланс USDT — попробуй ещё раз": "couldn't fetch the USDT balance — try again",
    "баланс USDT нулевой — нечего отправлять": "USDT balance is zero — nothing to send",
    "сумма: {e}": "amount: {e}",
    "весь баланс": "whole balance",
    "не удалось обновить баланс сейчас — данные ниже могут быть устаревшими": "couldn't refresh the balance now — the data below may be stale",
    "на кошельке может не хватить TON с учётом комиссии (~0.01)": "the wallet may lack TON including the fee (~0.01)",
    "сумма больше баланса USDT": "the amount exceeds the USDT balance",
    "мало TON на газ: нужно ≥ {m} TON": "not enough TON for gas: need ≥ {m} TON",
    "  Баланс сейчас: ": "  Balance now: ",
    "  (только что проверено)": "  (just checked)",
    "  (проверка не удалась)": "  (check failed)",
    "  Транзакции в TON необратимы. Проверь адрес ещё раз.": "  TON transactions are irreversible. Check the address once more.",
    "отправить?": "send?",
    "ошибка отправки: {e}": "send error: {e}",
    "  Отправлено {shown} {l} с {name} → {dest}": "  Sent {shown} {l} from {name} → {dest}",
    "  Транзакция появится в обозревателе через 5–15 секунд.": "  The transaction will appear in the explorer in 5–15 seconds.",
    "✔ сообщение принято сетью": "✔ message accepted by the network",
    "{l} отправлены с {name}": "{l} sent from {name}",
    "обновить баланс кошелька сейчас?": "refresh this wallet's balance now?",
    # ── сбор средств ─────────────────────────────────────────────────
    "кошельков нет": "no wallets",
    "сбор средств · шаг 1/3": "sweep · step 1/3",
    "  Соберёт средства со ВСЕХ кошельков хранилища на один адрес.": "  Collects funds from ALL vault wallets to a single address.",
    "  Резерв TON на каждом кошельке: ": "  TON reserve on each wallet: ",
    "  (SWEEP_RESERVE_TON; 0 = забрать всё)": "  (SWEEP_RESERVE_TON; 0 = take everything)",
    "  USDT уходят только с кошельков, где есть ≥ {m} TON на газ.": "  USDT leaves only wallets with ≥ {m} TON for gas.",
    "Только USDT": "USDT only",
    "Только TON": "TON only",
    "USDT, затем TON": "USDT, then TON",
    "что собираем": "what to collect",
    "адрес-получатель": "recipient address",
    "адрес не распознан": "address not recognized",
    "это testnet-адрес, а сейчас сеть mainnet — сбор отменён": "this is a testnet address, current network is mainnet — sweep cancelled",
    "это mainnet-адрес, а сейчас сеть testnet — сбор отменён": "this is a mainnet address, current network is testnet — sweep cancelled",
    "сбор средств · обновляю балансы": "sweep · refreshing balances",
    "  Обновляю балансы всех кошельков перед сбором (это обязательный шаг)…": "  Refreshing all wallet balances before the sweep (mandatory step)…",
    "нечего собирать: балансы нулевые или мало TON на газ": "nothing to collect: balances are zero or too little TON for gas",
    "весь остаток TON": "whole TON balance",
    "  Получатель: ": "  Recipient: ",
    "  Операций: ": "  Operations: ",
    "  … и ещё {n}": "  … and {n} more",
    "  не развёрнуты: ": "  not deployed: ",
    " — контракта в сети ещё нет,": " — no contract on-chain yet,",
    "  отправка с них развернёт кошелёк автоматически (без ожидания seqno).": "  sending from them deploys the wallet automatically (no seqno wait).",
    "  Каждая отправка ждёт подтверждения предыдущей (SeqnoGuard) — это небыстро.": "  Each send waits for the previous one to confirm (SeqnoGuard) — it's not fast.",
    "сбор средств · шаг 2/3 · план": "sweep · step 2/3 · plan",
    "выполнить план?": "execute the plan?",
    "сбор отменён": "sweep cancelled",
    "сбор средств · шаг 3/3 · выполнение": "sweep · step 3/3 · running",
    "  {n} операций → {d}": "  {n} operations → {d}",
    "  ход выполнения ниже; не закрывай окно": "  progress below; do not close the window",
    "✔ напрямую": "✔ direct",
    "сбор завершён: успешно {ok}, ошибок {fail}": "sweep finished: {ok} succeeded, {fail} failed",
    "Enter — в меню (балансы обновятся через пункт 3)": "Enter — to menu (balances refresh via item 3)",
    # ── seed ─────────────────────────────────────────────────────────
    "  Seed-фраза даёт полный доступ к средствам.": "  The seed phrase gives full access to the funds.",
    "  Убедись, что за экраном никто не наблюдает и запись экрана выключена.": "  Make sure nobody is watching the screen and screen recording is off.",
    "seed-фраза · подтверждение": "seed phrase · confirmation",
    "показать seed-фразу на экране?": "show the seed phrase on screen?",
    "показ отменён": "showing cancelled",
    "seed-фраза": "seed phrase",
    "  При импорте в Tonkeeper / MyTonWallet выбери версию {v}, если адрес не совпал.": "  When importing into Tonkeeper / MyTonWallet pick version {v} if the address differs.",
    "  Экран будет очищен после Enter.": "  The screen will be cleared after Enter.",
    "Enter — скрыть": "Enter — hide",
    # ── история транзакций ───────────────────────────────────────────
    "история транзакций · {name}": "transaction history · {name}",
    "  Запрашиваю последние события через tonapi.io…": "  Fetching recent events via tonapi.io…",
    "история транзакций · ошибка": "transaction history · error",
    "  Не удалось получить историю: {e}": "  Couldn't fetch the history: {e}",
    "  Возможные причины: нет сети, лимит запросов tonapi.io,": "  Possible reasons: no network, tonapi.io rate limit,",
    "  либо адрес ещё не разворачивался в сети.": "  or the address has never been deployed on-chain.",
    "  Переводов TON/USDT не найдено (либо их пока не было).": "  No TON/USDT transfers found (or there were none yet).",
    "  Показаны только последние {n} событий по кошельку.": "  Only the last {n} events of the wallet are shown.",
    "  показаны последние {n} событий · адреса — полные": "  showing the last {n} events · addresses are full",
    "история транзакций · стр. {a}/{b}": "transaction history · page {a}/{b}",
    "  номер — детали · n/p — листать · Enter — назад": "  number — details · n/p — pages · Enter — back",
    "от": "from",
    "кому": "to",
    "детали транзакции": "transaction details",
    "входящая (получено)": "incoming (received)",
    "исходящая (отправлено)": "outgoing (sent)",
    " (этот кошелёк)": " (this wallet)",
    "Отправитель": "Sender",
    "Получатель": "Recipient",
    "  Событие: ": "  Event:   ",
    "  Адреса показаны целиком — можно скопировать прямо из консоли (выделить мышью).": "  Addresses are shown in full — you can copy them right from the console (select with the mouse).",
    "  Свежие детали всегда можно сверить в блок-эксплорере по ссылке выше.": "  Fresh details can always be verified in the block explorer via the link above.",
    "  Время:": "  Time:",
    "  Направление: ": "  Direction: ",
    "  Сумма:       ": "  Amount:    ",
    "  Кошелёк:     ": "  Wallet:     ",
    "  Комментарий: ": "  Comment:   ",
    # ── экспорт ──────────────────────────────────────────────────────
    "экспорт CSV": "CSV export",
    "экспортировать нечего": "nothing to export",
    "  CSV с колонками: name; address; version; ton; usdt.": "  CSV with columns: name; address; version; ton; usdt.",
    "  По желанию можно добавить seed-фразы — тогда файл становится секретным.": "  Optionally, seed phrases can be included — the file then becomes secret.",
    "включить seed-фразы в файл? (опасно)": "include seed phrases in the file? (dangerous)",
    "имя файла": "file name",
    "  Файл {f} содержит seed-фразы в открытом виде.": "  File {f} contains seed phrases in plaintext.",
    "  Windows не поддерживает unix-права доступа (chmod) — файл": "  Windows does not support unix file permissions (chmod) — the file",
    "  доступен всем, у кого есть доступ к этой папке/диску.": "  is accessible to anyone with access to this folder/drive.",
    "  Не удалось ограничить права доступа к файлу (chmod не сработал).": "  Couldn't restrict file permissions (chmod failed).",
    "  Права доступа ограничены (chmod 600, только текущий пользователь).": "  File permissions restricted (chmod 600, current user only).",
    "  Рекомендация: перенеси файл в зашифрованное хранилище (или на офлайн-": "  Recommendation: move the file to encrypted storage (or an offline",
    "  носитель) и удали его отсюда сразу после того, как он больше не нужен.": "  drive) and delete it here as soon as it's no longer needed.",
    "экспорт с seed-фразами": "export with seed phrases",
    "экспортировано {n} кошельков → {f}": "{n} wallets exported → {f}",
    # ── импорт ───────────────────────────────────────────────────────
    "импорт seed-фразы": "import seed phrase",
    "  Вставь seed-фразу (24 слова TON или 12/18/24 слова BIP-39) через пробел.": "  Paste a seed phrase (24 TON words or 12/18/24 BIP-39 words) separated by spaces.",
    "  Версия кошелька по умолчанию: {v} (v3r2 | v4r2 | v5r1).": "  Default wallet version: {v} (v3r2 | v4r2 | v5r1).",
    "seed-фраза (Enter — отмена)": "seed phrase (Enter — cancel)",
    "импорт отменён: нужно 12/18/24 слова": "import cancelled: 12/18/24 words are required",
    "версия": "version",
    "неизвестная версия": "unknown version",
    "seed не принят: {e}": "seed rejected: {e}",
    "такой кошелёк уже есть в хранилище": "this wallet is already in the vault",
    "импортирован {name} · {addr}": "imported {name} · {addr}",
    # ── выбор языка ──────────────────────────────────────────────────
    "выбор языка": "language selection",
    "  Текущий язык интерфейса: ": "  Current interface language: ",
    "  Выбор сохраняется в файл {f} рядом со скриптом.": "  The choice is saved to {f} next to the script.",
    "язык переключён: {l}": "language switched: {l}",
    # ── запуск / выход ───────────────────────────────────────────────
    "WALLET_VERSION должен быть одним из: {v}": "WALLET_VERSION must be one of: {v}",
    "  TONFORGE · хранилище закрыто, до встречи": "  TONFORGE · vault closed, see you",
    "  прервано (Ctrl+C) — все изменения уже сохранены в хранилище": "  interrupted (Ctrl+C) — all changes are already saved to the vault",
}


def tr(text, **kw):
    """Перевод строки интерфейса. Ключ — русский исходный текст; если перевода
    нет или язык русский — вернётся исходная строка. Подстановки — через {имя}."""
    if LANG == "en":
        text = EN.get(text, text)
    return text.format(**kw) if kw else text


def load_config():
    """Читает tonforge.config.json (сейчас там только язык)."""
    global LANG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("lang") in ("ru", "en"):
            LANG = data["lang"]
    except (OSError, ValueError, AttributeError):
        pass


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"lang": LANG}, f, ensure_ascii=False)
    except OSError:
        pass


# ═══════════════════════ ОФОРМЛЕНИЕ ═══════════════════════
class C:
    RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
    LIME, CYAN, RED = "\033[92m", "\033[96m", "\033[91m"
    YEL, GREY = "\033[93m", "\033[90m"


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def paint(text, *codes):
    return "".join(codes) + str(text) + C.RESET


def key(k):
    return paint(f"[{k}]", C.LIME, C.BOLD)


def vis_len(s):
    return len(ANSI_RE.sub("", s))


def pad(s, width):
    return s + " " * max(0, width - vis_len(s))


def two_col(a, b, split=38):
    return pad("  " + a, split) + b


def short(addr):
    return addr[:8] + "…" + addr[-6:] if len(addr) > 20 else addr


def _norm_addr(addr):
    """Приводит адрес в любом формате к raw 0:… в нижнем регистре —
    удобно для сравнения адресов из истории с адресами хранилища."""
    try:
        return Address(addr).to_str(is_user_friendly=False).lower()
    except Exception:
        return addr.lower()


def to_friendly(addr):
    """raw 0:… → user-friendly UQ…/EQ… (48 символов, целиком)."""
    try:
        return Address(addr).to_str(is_bounceable=False)
    except Exception:
        return addr


def state_ru(code):
    return tr(STATE_RU.get(code or "", code or "—"))


def to_units(text, decimals):
    """'12.5' -> 12500000 при decimals=6. Бросает ValueError."""
    try:
        d = Decimal(str(text).replace(",", ".").strip())
    except InvalidOperation:
        raise ValueError(tr("это не число"))
    if d <= 0:
        raise ValueError(tr("сумма должна быть больше нуля"))
    return int((d * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN))


def fmt_units(units, decimals, prec):
    if units is None:
        return "—"
    d = Decimal(int(units)) / (Decimal(10) ** decimals)
    return f"{d.quantize(Decimal(10) ** -prec, rounding=ROUND_DOWN):f}"


def fmt_ton(nano):
    return fmt_units(nano, TON_DECIMALS, 4)


def fmt_usdt(units):
    return fmt_units(units, USDT_DECIMALS, 2)


def explorer_link(kind, value):
    if EXPLORER == "tonscan":
        base = "testnet.tonscan.org" if IS_TESTNET else "tonscan.org"
        return f"{base}/{'address' if kind == 'address' else 'tx'}/{value}"
    base = "testnet.tonviewer.com" if IS_TESTNET else "tonviewer.com"
    return f"{base}/{value}" if kind == "address" else f"{base}/transaction/{value}"


# ═══════════════════════ ИСТОРИЯ ТРАНЗАКЦИЙ ═══════════════════════
# Используем публичный REST tonapi.io/v2/accounts/{addr}/events — он отдаёт
# готовые, уже расшифрованные "actions" (TonTransfer / JettonTransfer) с суммой,
# отправителем и получателем, а не сырые BOC, которые пришлось бы парсить
# вручную. Официальная документация tonapi.io прямо предупреждает: "actions
# can be changed at any time" — это удобный слой для показа человеку, а не
# гарантированно стабильный контракт API. Для сверки всегда есть ссылка на
# блок-эксплорер в деталях транзакции.
async def fetch_account_events(raw_address, limit=TX_HISTORY_LIMIT):
    def _do():
        headers = {"Authorization": f"Bearer {TONAPI_API_KEY}"} if TONAPI_API_KEY else {}
        r = requests.get(
            f"{TONAPI_BASE}/v2/accounts/{raw_address}/events",
            params={"limit": limit},
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    return await asyncio.to_thread(_do)


def _tx_from_ton_transfer(ev, act, my_raw):
    d = act.get("TonTransfer") or {}
    sender = (d.get("sender") or {}).get("address")
    recipient = (d.get("recipient") or {}).get("address")
    amount_nano = d.get("amount")
    if sender is None or recipient is None or amount_nano is None:
        return None
    if sender.lower() == my_raw:
        direction, counterparty = "out", recipient
    elif recipient.lower() == my_raw:
        direction, counterparty = "in", sender
    else:
        return None
    return {
        "ts": ev.get("timestamp"),
        "event_id": ev.get("event_id"),
        "asset": "TON",
        "direction": direction,
        "amount": int(amount_nano) / 1e9,
        "counterparty": counterparty,
        # полные адреса обеих сторон — для экрана «от кого / кому»
        "from_addr": sender,
        "to_addr": recipient,
        "comment": d.get("comment") or None,
    }


def _tx_from_jetton_transfer(ev, act, my_raw):
    d = act.get("JettonTransfer") or {}
    jetton = d.get("jetton") or {}
    symbol = (jetton.get("symbol") or "").upper().replace("₮", "T")
    if symbol != "USDT":
        return None  # приложение работает только с TON и USDT
    sender = (d.get("sender") or {}).get("address")
    recipient = (d.get("recipient") or {}).get("address")
    raw_amount = d.get("amount")
    if sender is None or recipient is None or raw_amount is None:
        return None
    decimals = jetton.get("decimals", USDT_DECIMALS)
    if sender.lower() == my_raw:
        direction, counterparty = "out", recipient
    elif recipient.lower() == my_raw:
        direction, counterparty = "in", sender
    else:
        return None
    return {
        "ts": ev.get("timestamp"),
        "event_id": ev.get("event_id"),
        "asset": "USDT",
        "direction": direction,
        "amount": int(raw_amount) / (10 ** decimals),
        "counterparty": counterparty,
        "from_addr": sender,
        "to_addr": recipient,
        "comment": d.get("comment") or None,
    }


def parse_events(raw_json, my_raw_address):
    """Превращает ответ tonapi.io в плоский список входящих/исходящих
    переводов TON и USDT. Ошибка в отдельном событии не должна ронять всю
    историю — такое событие просто пропускается."""
    my_raw = my_raw_address.lower()
    txs = []
    for ev in (raw_json or {}).get("events", []):
        for act in ev.get("actions", []):
            if act.get("status") != "ok":
                continue
            try:
                atype = act.get("type")
                if atype == "TonTransfer":
                    tx = _tx_from_ton_transfer(ev, act, my_raw)
                elif atype == "JettonTransfer":
                    tx = _tx_from_jetton_transfer(ev, act, my_raw)
                else:
                    tx = None
            except Exception:
                tx = None
            if tx is not None:
                txs.append(tx)
    txs.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return txs


# ═══════════════════════ ВВОД ═══════════════════════
def ask(prompt, default=None):
    hint = paint(f" [{default}]", C.GREY) if default not in (None, "") else ""
    try:
        value = input(paint(" › ", C.LIME) + prompt + hint + ": ").strip()
    except EOFError:
        return default or ""
    return value if value else (default if default is not None else "")


def confirm(prompt):
    return ask(prompt + " (y/N)", "n").lower() in ("y", "yes", "д", "да")


def pause(msg=None):
    if msg is None:
        msg = tr("Enter — назад")
    try:
        input(paint(" › " + msg, C.GREY))
    except EOFError:
        pass


def secret(prompt):
    try:
        return getpass.getpass(paint(" › ", C.LIME) + prompt + ": ")
    except EOFError:
        return ""


# ═══════════════════════ СТРАНИЦЫ ═══════════════════════
class Screen:
    """Рисует экран целиком: очистка → рамка → тело → строка статуса.
    Никакого накопления вывода — каждый шаг это новая «страница»."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def clear():
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()

    @staticmethod
    def line(text=""):
        inner = W - 4
        if vis_len(text) > inner and "\x1b" not in text:
            text = text[: inner - 1] + "…"
        print("│ " + pad(text, inner) + " │")

    def page(self, title, body, status=None):
        self.clear()
        print("╭" + "─" * (W - 2) + "╮")
        left = paint(" TONFORGE", C.BOLD, C.LIME) + paint(" · " + title, C.BOLD)
        right = paint(f"{NETWORK} · " + tr("кошельков: {n}", n=len(self.app.wallets)), C.GREY)
        gap = " " * max(1, W - 4 - vis_len(left) - vis_len(right))
        self.line(left + gap + right)
        print("├" + "─" * (W - 2) + "┤")
        self.line()
        for row in body:
            self.line(row)
        flash = status if status is not None else self.app.take_flash()
        if flash:
            print("├" + "─" * (W - 2) + "┤")
            self.line(flash)
        print("╰" + "─" * (W - 2) + "╯")


# ═══════════════════════ ХРАНИЛИЩЕ ═══════════════════════
class VaultLocked(Exception):
    pass


class Vault:
    """JSON-файл с кошельками. При ENCRYPT_VAULT содержимое шифруется
    Fernet (AES-128-CBC + HMAC-SHA256), ключ выводится из пароля через
    PBKDF2-SHA256 с 390 000 итераций и случайной солью."""

    def __init__(self, path):
        self.path = path
        self.fernet = None
        self.salt = None

    def exists(self):
        return os.path.exists(self.path)

    def file_encrypted(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return False
        except json.JSONDecodeError as exc:
            raise VaultLocked(tr("файл хранилища повреждён (битый JSON): {e}", e=exc))
        if not isinstance(data, dict):
            raise VaultLocked(tr("файл хранилища повреждён (неожиданный формат)"))
        return bool(data.get("encrypted"))

    @staticmethod
    def _derive(password, salt):
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390_000)
        return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))

    def create(self, password):
        if ENCRYPT_VAULT:
            self.salt = secrets.token_bytes(16)
            self.fernet = self._derive(password, self.salt)
        self.save([])

    def open(self, password):
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if raw.get("encrypted"):
            from cryptography.fernet import InvalidToken

            self.salt = base64.b64decode(raw["salt"])
            self.fernet = self._derive(password, self.salt)
            try:
                payload = json.loads(self.fernet.decrypt(raw["payload"].encode()).decode("utf-8"))
            except InvalidToken:
                raise VaultLocked(tr("неверный пароль"))
        else:
            payload = raw["payload"]
        return payload.get("wallets", []), raw.get("network", NETWORK)

    def save(self, wallets):
        payload = {"wallets": wallets, "saved": datetime.now().isoformat(timespec="seconds")}
        doc = {"format": "tonforge/1", "network": NETWORK, "encrypted": bool(self.fernet)}
        if self.fernet:
            doc["salt"] = base64.b64encode(self.salt).decode()
            blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            doc["payload"] = self.fernet.encrypt(blob).decode()
        else:
            doc["payload"] = payload
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


# ═══════════════════════ ПРИЛОЖЕНИЕ ═══════════════════════
class App:
    def __init__(self, client):
        self.client = client
        self.vault = Vault(VAULT_FILE)
        self.wallets = []
        self.flash = None
        self.ui = Screen(self)

    # ---------- служебное ----------
    def set_flash(self, text, ok=True):
        self.flash = paint(("✔ " if ok else "✖ ") + text, C.LIME if ok else C.RED)

    def take_flash(self):
        flash, self.flash = self.flash, None
        return flash

    def save(self):
        self.vault.save(self.wallets)

    def wallet_instance(self, w):
        cls = WALLET_CLASSES.get(w.get("version", WALLET_VERSION), WalletV4R2)
        wallet, _, _, _ = cls.from_mnemonic(self.client, w["mnemonic"])
        return wallet

    def next_name(self, prefix):
        pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        nums = [int(m.group(1)) for w in self.wallets if (m := pat.match(w["name"]))]
        return max(nums, default=0) + 1

    @staticmethod
    def ensure_crypto():
        try:
            import cryptography  # noqa: F401
        except ImportError:
            print(paint("\n" + tr("  Для шифрования нужен пакет cryptography:  pip install cryptography"), C.RED))
            print(tr("  Либо поставь ENCRYPT_VAULT = False (хранилище будет открытым текстом).") + "\n")
            sys.exit(1)

    def own_name_map(self):
        """raw 0:… (нижний регистр) → имя кошелька из хранилища. Если вторая
        сторона перевода — один из своих кошельков, в истории рядом с адресом
        покажется его имя (worker-01, worker-02, …)."""
        return { _norm_addr(w["address"]): w["name"] for w in self.wallets }

    def wallet_row(self, i, w):
        state = state_ru(w.get("state") or "") if w.get("checked") else tr("не проверен")
        return (
            f"  {i:>3}  {w['name'][:12]:<12} {short(w['address']):<15} "
            f"{fmt_ton(w.get('ton')):>10} {fmt_usdt(w.get('usdt')):>10}  " + paint(state, C.GREY)
        )

    def table_head(self):
        return paint(
            f"  {'№':>3}  {tr('имя'):<12} {tr('адрес'):<15} {'TON':>10} {'USDT':>10}  {tr('состояние')}",
            C.GREY,
        )

    def _corrupted_vault_page(self, exc):
        self.ui.page(tr("хранилище повреждено"), [
            paint(tr("  Не удалось прочитать файл хранилища."), C.RED, C.BOLD),
            f"  {exc}",
            "",
            tr("  Файл: ") + paint(VAULT_FILE, C.BOLD),
            tr("  Если есть резервная копия файла — восстанови её и запусти снова."),
            tr("  Свежее хранилище создастся только если переименовать/удалить этот файл —"),
            tr("  но тогда старые кошельки останутся недоступны без сохранённых seed-фраз."),
        ])
        pause(tr("Enter — выход"))

    # ---------- вход ----------
    async def boot(self):
        try:
            needs_crypto = ENCRYPT_VAULT or (self.vault.exists() and self.vault.file_encrypted())
        except VaultLocked as exc:
            self._corrupted_vault_page(exc)
            return False
        if needs_crypto:
            self.ensure_crypto()
        rows = [
            tr("  Файл хранилища: ") + paint(VAULT_FILE, C.BOLD),
            tr("  Сеть: ") + paint(NETWORK, C.BOLD) + tr("   версия кошельков: ") + paint(WALLET_VERSION, C.BOLD),
            "",
        ]
        if self.vault.exists():
            try:
                enc = self.vault.file_encrypted()
            except VaultLocked as exc:
                self._corrupted_vault_page(exc)
                return False
            rows.append(tr("  Хранилище найдено. Введи пароль, чтобы расшифровать.") if enc
                        else tr("  Хранилище найдено. Открываю…"))
            self.ui.page(tr("вход"), rows)
            pwd = secret(tr("пароль хранилища")) if enc else ""
            for attempt in range(3):
                try:
                    self.wallets, vault_net = self.vault.open(pwd)
                    break
                except VaultLocked:
                    if attempt < 2:
                        print(paint(tr("   ✖ неверный пароль ({a}/3)", a=attempt + 1), C.RED))
                        pwd = secret(tr("попробуй ещё раз"))
            else:
                print(paint(tr("   доступ закрыт\n"), C.RED))
                return False
            if vault_net != NETWORK:
                self.set_flash(tr("внимание: хранилище создано для сети {a}, а сейчас {b}",
                                  a=vault_net, b=NETWORK), ok=False)
            else:
                self.set_flash(tr("хранилище открыто · кошельков: {n}", n=len(self.wallets)))
            return True

        if ENCRYPT_VAULT:
            rows += [
                tr("  Хранилища ещё нет — создаём новое."),
                tr("  Придумай пароль: он шифрует все seed-фразы. Потеряешь пароль —"),
                tr("  потеряешь доступ к файлу (восстановить кошельки можно только по seed)."),
            ]
            self.ui.page(tr("новое хранилище"), rows)
            while True:
                p1 = secret(tr("новый пароль"))
                if len(p1) < 6:
                    print(paint(tr("   минимум 6 символов"), C.YEL))
                    continue
                if p1 != secret(tr("повтори пароль")):
                    print(paint(tr("   пароли не совпадают"), C.YEL))
                    continue
                break
            self.vault.create(p1)
        else:
            rows.append(tr("  Хранилища ещё нет — создаю новое ") + paint(tr("(без шифрования!)"), C.YEL))
            self.ui.page(tr("новое хранилище"), rows)
            self.vault.create("")
        self.wallets = []
        self.set_flash(tr("создано хранилище {f}", f=VAULT_FILE))
        return True

    # ---------- главный цикл ----------
    async def run(self):
        while True:
            self.ui.page(tr("главное меню"), self.menu_rows())
            ch = ask(tr("выбор")).lower()
            if ch == "1":
                await self.screen_list()
            elif ch == "2":
                await self.screen_create()
            elif ch == "3":
                await self.screen_refresh()
            elif ch == "4":
                await self.screen_send("ton")
            elif ch == "5":
                await self.screen_send("usdt")
            elif ch == "6":
                await self.screen_sweep()
            elif ch == "7":
                self.screen_seed()
            elif ch == "8":
                self.screen_export()
            elif ch == "9":
                self.screen_import()
            elif ch == "10":
                idx = self.pick_wallet(tr("история транзакций · выбор кошелька"),
                                       tr("выбери кошелёк, чтобы посмотреть его историю"))
                if idx is not None:
                    await self.screen_transactions(idx)
            elif ch == "11":
                self.screen_language()
            elif ch in ("0", "q", "exit", "выход", "quit"):
                return
            else:
                self.set_flash(tr("нет такого пункта"), ok=False)

    def menu_rows(self):
        tot_ton = sum(w.get("ton") or 0 for w in self.wallets)
        tot_usdt = sum(w.get("usdt") or 0 for w in self.wallets)
        checked = [w["checked"] for w in self.wallets if w.get("checked")]
        stamp = tr("обновлено {t}", t=max(checked)[11:19]) if checked else tr("балансы ещё не запрашивались")
        return [
            "  " + paint(tr("ИТОГО"), C.GREY) + "   " + paint(fmt_ton(tot_ton), C.BOLD) + " TON   "
            + paint(fmt_usdt(tot_usdt), C.BOLD) + " USDT   " + paint(stamp, C.GREY),
            "",
            two_col(key(1) + " " + tr("Список кошельков"), key(6) + " " + tr("Собрать всё на один адрес")),
            two_col(key(2) + " " + tr("Создать пачку ({n} шт)", n=BATCH_SIZE), key(7) + " " + tr("Показать seed-фразу")),
            two_col(key(3) + " " + tr("Обновить балансы"), key(8) + " " + tr("Экспорт адресов в CSV")),
            two_col(key(4) + " " + tr("Перевод TON"), key(9) + " " + tr("Импорт seed-фразы")),
            two_col(key(5) + " " + tr("Перевод USDT"), key(0) + " " + tr("Выход")),
            two_col(key(10) + " " + tr("История транзакций"), key(11) + " Язык / Language"),
        ]

    # ---------- выбор языка ----------
    def screen_language(self):
        global LANG
        current = "Русский" if LANG == "ru" else "English"
        rows = [
            tr("  Текущий язык интерфейса: ") + paint(current, C.BOLD),
            "",
            "  " + key(1) + " Русский",
            "  " + key(2) + " English",
            "",
            paint(tr("  Выбор сохраняется в файл {f} рядом со скриптом.", f=CONFIG_FILE), C.GREY),
            paint(tr("Enter — назад"), C.GREY),
        ]
        self.ui.page(tr("выбор языка"), rows)
        ch = ask(tr("выбор"))
        if ch == "1":
            LANG = "ru"
        elif ch == "2":
            LANG = "en"
        else:
            return
        save_config()
        self.set_flash(tr("язык переключён: {l}", l="Русский" if LANG == "ru" else "English"))

    # ---------- список и карточка ----------
    def pick_wallet(self, title, hint):
        if not self.wallets:
            self.set_flash(tr("кошельков пока нет — создай пачку в пункте 2"), ok=False)
            return None
        limit = ROWS_PER_PAGE * 2
        rows = [self.table_head()]
        rows += [self.wallet_row(i, w) for i, w in enumerate(self.wallets[:limit], 1)]
        if len(self.wallets) > limit:
            rows.append(paint(tr("  … ещё {n} — номер можно ввести вручную", n=len(self.wallets) - limit), C.GREY))
        rows += ["", paint("  " + hint, C.GREY)]
        self.ui.page(title, rows)
        ch = ask(tr("номер кошелька (Enter — отмена)"))
        if not ch.isdigit() or not (1 <= int(ch) <= len(self.wallets)):
            return None
        return int(ch) - 1

    async def screen_list(self):
        page = 0
        while True:
            total = len(self.wallets)
            pages = max(1, -(-total // ROWS_PER_PAGE))
            page = min(page, pages - 1)
            chunk = self.wallets[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
            rows = [self.table_head()]
            rows += [self.wallet_row(page * ROWS_PER_PAGE + i, w) for i, w in enumerate(chunk, 1)]
            if not chunk:
                rows.append(tr("  пусто — создай пачку в пункте [2]"))
            rows += ["", paint(tr("  номер — карточка кошелька · n/p — листать · Enter — назад"), C.GREY)]
            self.ui.page(tr("кошельки · стр. {a}/{b}", a=page + 1, b=pages), rows)
            ch = ask(tr("выбор")).lower()
            if ch == "":
                return
            if ch == "n":
                page = min(page + 1, pages - 1)
            elif ch == "p":
                page = max(page - 1, 0)
            elif ch.isdigit() and 1 <= int(ch) <= total:
                await self.screen_wallet(int(ch) - 1)
            else:
                self.set_flash(tr("не понял команду"), ok=False)

    async def screen_wallet(self, idx):
        while idx < len(self.wallets):
            w = self.wallets[idx]
            checked = paint(tr("   проверено {t}", t=w['checked'][11:19]), C.GREY) if w.get("checked") \
                else paint(tr("   балансы не запрашивались"), C.GREY)
            rows = [
                "  " + paint(w["name"], C.BOLD)
                + paint(tr("   {v} · создан {d}", v=w.get('version', WALLET_VERSION), d=w.get('created', '?')[:10]), C.GREY),
                "  " + paint(w["address"], C.CYAN),
                "  " + explorer_link("address", w["address"]),
                "",
                tr("  Баланс:") + f"  {paint(fmt_ton(w.get('ton')), C.BOLD)} TON     {paint(fmt_usdt(w.get('usdt')), C.BOLD)} USDT",
                tr("  Статус:") + f"  {state_ru(w.get('state') or '')}" + checked,
                "",
                two_col(key(1) + " " + tr("Перевести TON"), key(4) + " " + tr("Показать seed-фразу")),
                two_col(key(2) + " " + tr("Перевести USDT"), key(5) + " " + tr("Переименовать")),
                two_col(key(3) + " " + tr("Обновить баланс"), key(6) + " " + tr("Удалить из хранилища")),
                two_col(key(7) + " " + tr("История транзакций"), ""),
                "",
                paint(tr("  Enter — назад к списку"), C.GREY),
            ]
            self.ui.page(tr("кошелёк {name}", name=w['name']), rows)
            ch = ask(tr("выбор"))
            if ch in ("", "0"):
                return
            if ch == "1":
                await self.screen_send("ton", idx)
            elif ch == "2":
                await self.screen_send("usdt", idx)
            elif ch == "3":
                await self.refresh_wallets([idx])
            elif ch == "4":
                self.screen_seed(idx)
            elif ch == "5":
                w["name"] = ask(tr("новое имя"), w["name"])[:24]
                self.save()
                self.set_flash(tr("переименован"))
            elif ch == "6":
                if self.delete_wallet(idx):
                    return
            elif ch == "7":
                await self.screen_transactions(idx)
            else:
                self.set_flash(tr("не понял команду"), ok=False)

    def delete_wallet(self, idx):
        w = self.wallets[idx]
        rows = [
            "  " + paint(tr("ВНИМАНИЕ"), C.RED, C.BOLD) + tr(": запись {name} будет удалена из хранилища.", name=w['name']),
            tr("  Если seed-фраза не сохранена отдельно — доступ к средствам будет потерян."),
            tr("  Баланс сейчас: {t} TON / {u} USDT", t=fmt_ton(w.get('ton')), u=fmt_usdt(w.get('usdt'))),
            "",
            tr("  Для подтверждения введи имя кошелька: ") + paint(w['name'], C.BOLD),
        ]
        self.ui.page(tr("удаление"), rows)
        if ask(tr("имя")) != w["name"]:
            self.set_flash(tr("удаление отменено"), ok=False)
            return False
        self.wallets.pop(idx)
        self.save()
        self.set_flash(tr("{name} удалён из хранилища", name=w['name']))
        return True

    # ---------- создание ----------
    async def screen_create(self):
        rows = [
            tr("  Сеть: ") + paint(NETWORK, C.BOLD) + paint(f"   {WALLET_VERSION}", C.BOLD)
            + tr("   хранилище: ") + VAULT_FILE,
            tr("  Seed-фразы генерируются локально и сразу сохраняются в хранилище."),
            "",
            tr("  Сколько кошельков создать? Enter — {n}.", n=BATCH_SIZE),
        ]
        self.ui.page(tr("создание кошельков"), rows)
        raw = ask(tr("количество"), str(BATCH_SIZE))
        if not raw.isdigit() or not (1 <= int(raw) <= 500):
            self.set_flash(tr("нужно число от 1 до 500"), ok=False)
            return
        count = int(raw)
        prefix = ask(tr("префикс имени"), NAME_PREFIX).strip() or NAME_PREFIX
        cls = WALLET_CLASSES[WALLET_VERSION]
        start = self.next_name(prefix)
        created = []
        for n in range(start, start + count):
            wallet, _, _, mnemonic = cls.create(self.client)
            rec = {
                "name": f"{prefix}-{n:02d}",
                "address": wallet.address.to_str(is_bounceable=False),
                "version": WALLET_VERSION,
                "mnemonic": " ".join(mnemonic),
                "created": datetime.now().isoformat(timespec="seconds"),
                "ton": None, "usdt": None, "state": None, "usdt_wallet": None, "checked": None,
            }
            self.wallets.append(rec)
            created.append(rec)
        self.save()
        rows = [tr("  Создано {n} кошельков, всего в хранилище: {m}", n=paint(count, C.BOLD), m=len(self.wallets)), ""]
        rows += [f"  {c['name']:<12} {paint(c['address'], C.CYAN)}" for c in created[:ROWS_PER_PAGE]]
        if count > ROWS_PER_PAGE:
            rows.append(paint(tr("  … и ещё {n} — смотри список [1]", n=count - ROWS_PER_PAGE), C.GREY))
        rows += [
            "",
            paint(tr("  Адреса уже принимают TON и USDT; контракт развернётся при первой отправке."), C.GREY),
            paint(tr("  Чтобы кошелёк мог отправлять USDT, на нём должно быть ~0.1 TON на газ."), C.YEL),
        ]
        self.ui.page(tr("готово"), rows, status=paint(tr("✔ сохранено в {f}", f=VAULT_FILE), C.LIME))
        pause()

    # ---------- балансы ----------
    async def screen_refresh(self):
        if not self.wallets:
            self.set_flash(tr("нечего обновлять"), ok=False)
            return
        self.ui.page(tr("обновление балансов"), [
            tr("  Запрашиваю {n} кошельков через toncenter…", n=len(self.wallets)),
            paint(tr("  без API-ключа это ~3 с на кошелёк; с ключом — доли секунды"), C.GREY),
        ])
        await self.refresh_wallets()

    async def refresh_wallets(self, indices=None):
        idxs = list(indices) if indices is not None else list(range(len(self.wallets)))
        t0, errors = time.time(), 0
        for k, i in enumerate(idxs, 1):
            w = self.wallets[i]
            sys.stdout.write("\r" + paint(f"  {k}/{len(idxs)} · {w['name']}" + " " * 24, C.GREY))
            sys.stdout.flush()
            try:
                info = await self.client.get_info(w["address"])
                w["ton"] = int(info.balance)
                state = getattr(info, "state", None)
                w["state"] = str(getattr(state, "value", state) or "nonexist")
                if not w.get("usdt_wallet"):
                    jw = await get_wallet_address_get_method(
                        client=self.client,
                        address=Address(USDT_MASTER),
                        owner_address=Address(w["address"]),
                    )
                    w["usdt_wallet"] = jw.to_str()
                try:
                    stack = await self.client.run_get_method(
                        address=w["usdt_wallet"], method_name="get_wallet_data", stack=None
                    )
                    w["usdt"] = int(stack[0])
                except Exception:
                    w["usdt"] = 0  # jetton-кошелёк ещё не создан: USDT сюда не приходили
                w["checked"] = datetime.now().isoformat(timespec="seconds")
            except Exception as exc:
                errors += 1
                w["error"] = str(exc)[:80]
        print()
        self.save()
        text = tr("балансы обновлены за {s} с", s=f"{time.time() - t0:.1f}")
        if errors:
            text += tr(" · ошибок: {n} (сеть или лимит запросов — повтори позже)", n=errors)
        self.set_flash(text, ok=errors == 0)

    # ---------- переводы ----------
    async def do_send(self, w, asset, dest_addr, units, comment, guard=None):
        wallet = self.wallet_instance(w)
        sender = guard or wallet
        if asset == "ton":
            kwargs = {"destination": dest_addr}
            if units is None:
                kwargs.update(amount=0, send_mode=128)  # 128 = отправить весь остаток
            else:
                kwargs["amount"] = units
            if comment:
                kwargs["body"] = comment
            return await sender.transfer(**kwargs)
        builder_kwargs = dict(
            destination=dest_addr,
            jetton_amount=units,
            jetton_master_address=Address(USDT_MASTER),
            forward_amount=1,
            amount=to_nano(JETTON_GAS_TON),
        )
        if comment:
            builder_kwargs["forward_payload"] = comment
        return await sender.transfer_message(JettonTransferBuilder(**builder_kwargs))

    async def screen_send(self, asset, idx=None):
        label = "TON" if asset == "ton" else "USDT"
        if idx is None:
            idx = self.pick_wallet(tr("перевод {l} · шаг 1/3 · откуда", l=label),
                                   tr("введи номер кошелька-отправителя"))
            if idx is None:
                self.set_flash(tr("перевод отменён"), ok=False)
                return
        w = self.wallets[idx]
        when = w["checked"][11:19] if w.get("checked") else tr("балансы ещё не запрашивались")
        rows = [
            f"  {tr('Откуда:')}  {paint(w['name'], C.BOLD)}  {paint(w['address'], C.CYAN)}",
            f"  {tr('Баланс:')}  {fmt_ton(w.get('ton'))} TON · {fmt_usdt(w.get('usdt'))} USDT"
            + paint(tr("(обновлён {t})", t=when) if w.get("checked") else f"({when})", C.GREY),
            "",
            tr("  Адрес получателя — любой: UQ…, EQ…, 0Q…/kQ… (testnet) или raw 0:…"),
            tr("  Сумма в {l}; слово ", l=label) + paint("all", C.BOLD) + tr(" — отправить весь баланс."),
        ]
        if asset == "usdt":
            rows.append(paint(tr("  К переводу USDT прикладывается {g} TON на газ, излишек вернётся.", g=JETTON_GAS_TON), C.GREY))
        self.ui.page(tr("перевод {l} · шаг 2/3 · куда и сколько", l=label), rows)
        dest = ask(tr("адрес получателя (Enter — отмена)"))
        if not dest:
            self.set_flash(tr("перевод отменён"), ok=False)
            return
        try:
            dest_addr = Address(dest)
        except Exception:
            self.set_flash(tr("адрес не распознан: {d}", d=dest[:30]), ok=False)
            return
        # Address() уже отклоняет мусорные строки (неверный checksum/формат),
        # но отдельно проверяем testnet/mainnet-флаг адреса — сети разные,
        # и отправка не туда потеряет средства безвозвратно.
        if dest_addr.is_test_only and not IS_TESTNET:
            self.set_flash(tr("это testnet-адрес, а сейчас сеть mainnet — перевод отменён"), ok=False)
            return
        if not dest_addr.is_test_only and IS_TESTNET:
            self.set_flash(tr("это mainnet-адрес, а сейчас сеть testnet — перевод отменён"), ok=False)
            return
        amount_raw = ask(tr("сумма {l}", l=label)).lower()
        comment = ask(tr("комментарий (необязательно)"), "")
        send_all = amount_raw == "all"

        # Обязательная проверка баланса прямо перед отправкой: то, что показано
        # на предыдущем экране, могло устареть. Отправка "весь баланс" по старым
        # цифрам особенно опасна для USDT — используем свежие данные.
        self.ui.page(tr("перевод {l} · проверка баланса", l=label), [
            f"  {tr('Кошелёк:'):<8} {w['name']}  {short(w['address'])}",
            tr("  Запрашиваю актуальный баланс перед отправкой…"),
        ])
        await self.refresh_wallets([idx])
        w = self.wallets[idx]
        balance_failed = bool(w.get("error"))

        try:
            if asset == "ton":
                units = None if send_all else to_units(amount_raw, TON_DECIMALS)
            elif send_all:
                if w.get("usdt") is None:
                    raise ValueError(tr("не удалось получить баланс USDT — попробуй ещё раз"))
                if w["usdt"] <= 0:
                    raise ValueError(tr("баланс USDT нулевой — нечего отправлять"))
                units = int(w["usdt"])
            else:
                units = to_units(amount_raw, USDT_DECIMALS)
        except ValueError as exc:
            self.set_flash(tr("сумма: {e}", e=exc), ok=False)
            return
        if asset == "ton":
            shown = tr("весь баланс") if send_all else fmt_ton(units)
        else:
            shown = fmt_usdt(units)
        dest_str = dest_addr.to_str(is_bounceable=False)
        warn = []
        if balance_failed:
            warn.append(tr("не удалось обновить баланс сейчас — данные ниже могут быть устаревшими"))
        if asset == "ton" and units is not None and w.get("ton") is not None and units + to_nano(0.01) > w["ton"]:
            warn.append(tr("на кошельке может не хватить TON с учётом комиссии (~0.01)"))
        if asset == "usdt":
            if w.get("usdt") is not None and units > w["usdt"]:
                warn.append(tr("сумма больше баланса USDT"))
            if w.get("ton") is not None and w["ton"] < MIN_TON_FOR_JETTON:
                warn.append(tr("мало TON на газ: нужно ≥ {m} TON", m=fmt_ton(MIN_TON_FOR_JETTON)))
        rows = [
            f"  {tr('Откуда:'):<8}  {w['name']}  {short(w['address'])}",
            f"  {tr('Куда:'):<8}  {paint(dest_str, C.CYAN)}",
            f"  {tr('Сумма:'):<8}  {paint(shown + ' ' + label, C.BOLD)}",
            f"  {tr('Баланс:'):<8}  {fmt_ton(w.get('ton'))} TON · {fmt_usdt(w.get('usdt'))} USDT"
            + paint(tr("  (только что проверено)") if not balance_failed else tr("  (проверка не удалась)"), C.GREY),
            f"  {tr('Комментарий:'):<8}  {comment or '—'}",
            f"  {tr('Сеть:'):<8}  {NETWORK}",
            "",
        ]
        rows += [paint("  ! " + t, C.YEL) for t in warn]
        rows += ["", paint(tr("  Транзакции в TON необратимы. Проверь адрес ещё раз."), C.GREY)]
        self.ui.page(tr("перевод {l} · шаг 3/3 · подтверждение", l=label), rows)
        if CONFIRM_SENDS and not confirm(tr("отправить?")):
            self.set_flash(tr("перевод отменён"), ok=False)
            return
        try:
            msg = await self.do_send(w, asset, dest_addr, units, comment)
        except Exception as exc:
            self.set_flash(tr("ошибка отправки: {e}", e=str(exc)[:70]), ok=False)
            return
        rows = [
            tr("  Отправлено {shown} {l} с {name} → {dest}",
               shown=paint(shown, C.BOLD), l=label, name=w['name'], dest=short(dest_str)),
            "",
            "  hash:  " + paint(msg.normalized_hash, C.CYAN),
            "  " + explorer_link("address", w["address"]),
            "",
            paint(tr("  Транзакция появится в обозревателе через 5–15 секунд."), C.GREY),
        ]
        self.ui.page(tr("отправлено"), rows, status=paint(tr("✔ сообщение принято сетью"), C.LIME))
        self.set_flash(tr("{l} отправлены с {name}", l=label, name=w['name']))
        if confirm(tr("обновить баланс кошелька сейчас?")):
            await asyncio.sleep(6)
            await self.refresh_wallets([idx])

    # ---------- сбор средств ----------
    async def screen_sweep(self):
        if not self.wallets:
            self.set_flash(tr("кошельков нет"), ok=False)
            return
        rows = [
            tr("  Соберёт средства со ВСЕХ кошельков хранилища на один адрес."),
            tr("  Резерв TON на каждом кошельке: ") + paint(SWEEP_RESERVE_TON, C.BOLD)
            + paint(tr("  (SWEEP_RESERVE_TON; 0 = забрать всё)"), C.GREY),
            tr("  USDT уходят только с кошельков, где есть ≥ {m} TON на газ.", m=fmt_ton(MIN_SAFE_JETTON_SWEEP)),
            "",
            two_col(key(1) + " " + tr("Только USDT"), key(3) + " " + tr("USDT, затем TON")),
            two_col(key(2) + " " + tr("Только TON"), tr("Enter — отмена")),
        ]
        self.ui.page(tr("сбор средств · шаг 1/3"), rows)
        what = ask(tr("что собираем"))
        if what not in ("1", "2", "3"):
            self.set_flash(tr("сбор отменён"), ok=False)
            return
        try:
            dest_addr = Address(ask(tr("адрес-получатель")))
        except Exception:
            self.set_flash(tr("адрес не распознан"), ok=False)
            return
        if dest_addr.is_test_only and not IS_TESTNET:
            self.set_flash(tr("это testnet-адрес, а сейчас сеть mainnet — сбор отменён"), ok=False)
            return
        if not dest_addr.is_test_only and IS_TESTNET:
            self.set_flash(tr("это mainnet-адрес, а сейчас сеть testnet — сбор отменён"), ok=False)
            return
        dest_str = dest_addr.to_str(is_bounceable=False)
        # Раньше обновление баланса перед сбором было опциональным вопросом
        # с ответом по умолчанию "нет" — план мог строиться по устаревшим
        # цифрам. Сбор списывает средства сразу со всех кошельков, поэтому
        # свежий баланс обязателен, без права отказаться.
        self.ui.page(tr("сбор средств · обновляю балансы"), [
            tr("  Обновляю балансы всех кошельков перед сбором (это обязательный шаг)…"),
        ])
        await self.refresh_wallets()
        reserve, fee = to_nano(SWEEP_RESERVE_TON), to_nano(0.01)
        plan = []
        for i, w in enumerate(self.wallets):
            ton, usdt = w.get("ton") or 0, w.get("usdt") or 0
            if what in ("1", "3") and usdt > 0 and ton >= MIN_SAFE_JETTON_SWEEP:
                plan.append((i, "usdt", usdt))
                ton -= to_nano(JETTON_GAS_TON)  # считаем консервативно, часть газа вернётся
            if what in ("2", "3"):
                if reserve == 0 and ton > fee:
                    plan.append((i, "ton", None))
                elif reserve > 0 and ton - reserve - fee > 0:
                    plan.append((i, "ton", ton - reserve - fee))
        if not plan:
            self.set_flash(tr("нечего собирать: балансы нулевые или мало TON на газ"), ok=False)
            return

        def describe(asset, units):
            if units is None:
                return tr("весь остаток TON")
            return fmt_usdt(units) + " USDT" if asset == "usdt" else fmt_ton(units) + " TON"

        rows = [tr("  Получатель: ") + paint(dest_str, C.CYAN),
                tr("  Операций: ") + paint(len(plan), C.BOLD), ""]
        rows += [f"  {self.wallets[i]['name']:<12} → {describe(a, u)}" for i, a, u in plan[:ROWS_PER_PAGE]]
        if len(plan) > ROWS_PER_PAGE:
            rows.append(paint(tr("  … и ещё {n}", n=len(plan) - ROWS_PER_PAGE), C.GREY))
        uninit = sorted({self.wallets[i]["name"] for i, _, _ in plan if self.wallets[i].get("state") in ("uninit", "nonexist")})
        if uninit:
            rows.append("")
            rows.append(paint(tr("  не развёрнуты: ") + ", ".join(uninit) + tr(" — контракта в сети ещё нет,"), C.YEL))
            rows.append(paint(tr("  отправка с них развернёт кошелёк автоматически (без ожидания seqno)."), C.YEL))
        rows += ["", paint(tr("  Каждая отправка ждёт подтверждения предыдущей (SeqnoGuard) — это небыстро."), C.GREY)]
        self.ui.page(tr("сбор средств · шаг 2/3 · план"), rows)
        if not confirm(tr("выполнить план?")):
            self.set_flash(tr("сбор отменён"), ok=False)
            return
        self.ui.page(tr("сбор средств · шаг 3/3 · выполнение"), [
            tr("  {n} операций → {d}", n=len(plan), d=short(dest_str)),
            paint(tr("  ход выполнения ниже; не закрывай окно"), C.GREY),
        ])
        guards, ok, fail = {}, 0, 0
        for n, (i, asset, units) in enumerate(plan, 1):
            w = self.wallets[i]
            # SeqnoGuard читает get-method seqno, а у НЕразвёрнутого кошелька
            # контракта в сети ещё нет — чтение падает с exit code -13.
            # Для таких кошельков отправляем напрямую: первая исходящая
            # транзакция сама развернёт контракт, а оперировать подтверждением нам не нужно.
            sender = None
            if w.get("state") == "active":
                if i not in guards:
                    guards[i] = SeqnoGuard(self.wallet_instance(w), timeout=90.0, poll_interval=2.0)
                sender = guards[i]
            sys.stdout.write(f"  {n:>3}/{len(plan)}  {w['name']:<12} {describe(asset, units):<22} ")
            sys.stdout.flush()
            try:
                await self.do_send(w, asset, dest_addr, units, "", guard=sender)
                print(paint("✔", C.LIME))
                ok += 1
            except Exception as exc:
                # балансы успели устареть (кошелёк был неактивен) — ретрай напрямую, без guard
                if sender is not None and "-13" in str(exc):
                    try:
                        await self.do_send(w, asset, dest_addr, units, "", guard=None)
                        print(paint(tr("✔ напрямую"), C.LIME))
                        ok += 1
                        continue
                    except Exception as exc2:
                        exc = exc2
                print(paint("✖ " + str(exc)[:46], C.RED))
                fail += 1
        print()
        self.set_flash(tr("сбор завершён: успешно {ok}, ошибок {fail}", ok=ok, fail=fail), ok=fail == 0)
        pause(tr("Enter — в меню (балансы обновятся через пункт 3)"))

    # ---------- seed ----------
    def screen_seed(self, idx=None):
        if idx is None:
            idx = self.pick_wallet(tr("seed-фраза · выбор кошелька"),
                                   tr("seed покажется только после подтверждения"))
            if idx is None:
                return
        w = self.wallets[idx]
        rows = [
            paint(tr("  Seed-фраза даёт полный доступ к средствам."), C.YEL),
            tr("  Убедись, что за экраном никто не наблюдает и запись экрана выключена."),
            "",
            f"  {tr('Кошелёк:')}     {paint(w['name'], C.BOLD)}  {short(w['address'])}",
        ]
        self.ui.page(tr("seed-фраза · подтверждение"), rows)
        if not confirm(tr("показать seed-фразу на экране?")):
            self.set_flash(tr("показ отменён"), ok=False)
            return
        words = w["mnemonic"].split()
        try:
            version = w.get("version", WALLET_VERSION)
            rows = [f"  {paint(w['name'], C.BOLD)}  ·  {version}  ·  {paint(w['address'], C.CYAN)}", ""]
            for r in range(0, len(words), 4):
                chunk = words[r:r + 4]
                rows.append("   " + "".join(
                    f"{paint(str(r + c + 1).rjust(2), C.GREY)} {word:<12}" for c, word in enumerate(chunk)
                ))
            rows += [
                "",
                paint(tr("  При импорте в Tonkeeper / MyTonWallet выбери версию {v}, если адрес не совпал.", v=version), C.GREY),
                paint(tr("  Экран будет очищен после Enter."), C.GREY),
            ]
            self.ui.page(tr("seed-фраза"), rows)
            pause(tr("Enter — скрыть"))
        finally:
            # Best-effort очистка: в CPython строки неизменяемы, поэтому это
            # НЕ гарантия того, что seed-фраза удалена из физической памяти.
            words = None
            rows = None
            import gc
            gc.collect()
            self.ui.clear()

    # ---------- история транзакций ----------
    async def screen_transactions(self, idx):
        w = self.wallets[idx]
        my_raw = _norm_addr(w["address"])
        own = self.own_name_map()

        self.ui.page(tr("история транзакций · {name}", name=w['name']), [
            f"  {w['address']}",
            tr("  Запрашиваю последние события через tonapi.io…"),
        ])
        try:
            raw = await fetch_account_events(my_raw)
        except Exception as exc:
            self.ui.page(tr("история транзакций · ошибка"), [
                paint(tr("  Не удалось получить историю: {e}", e=str(exc)[:70]), C.RED),
                "",
                paint(tr("  Возможные причины: нет сети, лимит запросов tonapi.io,"), C.GREY),
                paint(tr("  либо адрес ещё не разворачивался в сети."), C.GREY),
            ])
            pause()
            return

        txs = parse_events(raw, my_raw)
        if not txs:
            self.ui.page(tr("история транзакций · {name}", name=w['name']), [
                f"  {w['address']}",
                "",
                tr("  Переводов TON/USDT не найдено (либо их пока не было)."),
                paint(tr("  Показаны только последние {n} событий по кошельку.", n=TX_HISTORY_LIMIT), C.GREY),
            ])
            pause()
            return

        per_page = TX_ROWS_PER_PAGE  # запись = 2 строки: сумма + полный адрес
        page_no = 0
        while True:
            total = len(txs)
            pages = max(1, -(-total // per_page))
            page_no = min(page_no, pages - 1)
            chunk = txs[page_no * per_page:(page_no + 1) * per_page]
            rows = [
                f"  {w['name']}  {paint(w['address'], C.CYAN)}",
                paint(tr("  показаны последние {n} событий · адреса — полные", n=min(total, TX_HISTORY_LIMIT)), C.GREY),
                "",
            ]
            for n, tx in enumerate(chunk, page_no * per_page + 1):
                rows.extend(self.tx_row(n, tx, own))
                rows.append("")  # разделитель между записями
            if rows and rows[-1] == "":
                rows.pop()
            rows += ["", paint(tr("  номер — детали · n/p — листать · Enter — назад"), C.GREY)]
            self.ui.page(tr("история транзакций · стр. {a}/{b}", a=page_no + 1, b=pages), rows)
            ch = ask(tr("выбор")).lower()
            if ch == "":
                return
            if ch == "n":
                page_no = min(page_no + 1, pages - 1)
            elif ch == "p":
                page_no = max(page_no - 1, 0)
            elif ch.isdigit() and 1 <= int(ch) <= total:
                self.screen_transaction_detail(txs[int(ch) - 1], w, own)
            else:
                self.set_flash(tr("не понял команду"), ok=False)

    def tx_row(self, n, tx, own):
        """Одна запись истории = ДВЕ строки:
          1) номер, дата/время, сумма со знаком;
          2) «от»/«кому» + (имя своего кошелька, если это он) + ПОЛНЫЙ
             friendly-адрес контрагента — без какого-либо усечения."""
        when = datetime.fromtimestamp(tx["ts"]).strftime("%d.%m %H:%M") if tx.get("ts") else "   ?   "
        sign = "+" if tx["direction"] == "in" else "-"
        color = C.LIME if tx["direction"] == "in" else C.RED
        prec = 4 if tx["asset"] == "TON" else 2
        amount_str = f"{sign}{tx['amount']:.{prec}f} {tx['asset']}"
        head = f"  {n:>3}  {when:<11} " + paint(f"{amount_str:>15}", color)

        cp = tx.get("counterparty") or ""
        label = tr("от") if tx["direction"] == "in" else tr("кому")
        name = own.get(_norm_addr(cp))
        friendly = to_friendly(cp)
        who = ""
        if name:
            who = paint(f"{name[:14]:<14}", C.LIME) + " "
        who += paint(friendly, C.CYAN)
        return [head, f"        {paint(f'{label:<5}', C.GREY)} " + who]

    def screen_transaction_detail(self, tx, w, own):
        color = C.LIME if tx["direction"] == "in" else C.RED
        sign = "+" if tx["direction"] == "in" else "-"
        prec = 4 if tx["asset"] == "TON" else 2
        when = datetime.fromtimestamp(tx["ts"]).strftime("%Y-%m-%d %H:%M:%S") if tx.get("ts") else "—"
        my_norm = _norm_addr(w["address"])

        def party_lines(label, raw_addr):
            """Строка «Отправитель/Получатель» с ПОЛНЫМИ адресами:
            friendly на первой строке, raw 0:… на второй."""
            if not raw_addr:
                return [f"  {label:<12} —"]
            norm = _norm_addr(raw_addr)
            if norm == my_norm:
                tag = paint(tr(" (этот кошелёк)"), C.GREY)
            elif norm in own:
                tag = " " + paint(f"· {own[norm][:14]}", C.LIME)
            else:
                tag = ""
            return [
                f"  {label:<12} " + paint(to_friendly(raw_addr), C.CYAN) + tag,
                "  " + " " * 12 + " " + paint(str(raw_addr), C.GREY),
            ]

        rows = [
            tr("  Кошелёк:     ") + paint(w['name'], C.BOLD) + "  " + paint(w['address'], C.CYAN),
            tr("  Направление: ") + paint(tr("входящая (получено)") if tx["direction"] == "in" else tr("исходящая (отправлено)"), color),
            tr("  Сумма:       ") + paint(f"{sign}{tx['amount']:.{prec}f} {tx['asset']}", color, C.BOLD),
            "",
        ]
        rows += party_lines(tr("Отправитель"), tx.get("from_addr"))
        rows.append("")
        rows += party_lines(tr("Получатель"), tx.get("to_addr"))
        rows += [
            "",
            tr("  Время:") + "       " + when,
        ]
        if tx.get("comment"):
            rows.append(tr("  Комментарий: ") + tx["comment"])
        rows.append("")
        if tx.get("event_id"):
            rows.append(tr("  Событие: ") + paint(str(tx["event_id"]), C.GREY))
            rows.append("  " + explorer_link("tx", tx["event_id"]))
        rows.append(paint(tr("  Адреса показаны целиком — можно скопировать прямо из консоли (выделить мышью)."), C.GREY))
        rows.append(paint(tr("  Свежие детали всегда можно сверить в блок-эксплорере по ссылке выше."), C.GREY))
        self.ui.page(tr("детали транзакции"), rows)
        pause()

    # ---------- экспорт / импорт ----------
    def screen_export(self):
        if not self.wallets:
            self.set_flash(tr("экспортировать нечего"), ok=False)
            return
        self.ui.page(tr("экспорт CSV"), [
            tr("  CSV с колонками: name; address; version; ton; usdt."),
            tr("  По желанию можно добавить seed-фразы — тогда файл становится секретным."),
        ])
        with_seed = confirm(tr("включить seed-фразы в файл? (опасно)"))
        default = f"{NAME_PREFIX}_wallets{'_SECRET' if with_seed else ''}_{datetime.now():%Y%m%d_%H%M}.csv"
        fname = ask(tr("имя файла"), default)
        with open(fname, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(["name", "address", "version", "ton", "usdt"] + (["mnemonic"] if with_seed else []))
            for w in self.wallets:
                row = [w["name"], w["address"], w.get("version", WALLET_VERSION),
                       fmt_ton(w.get("ton")), fmt_usdt(w.get("usdt"))]
                wr.writerow(row + ([w["mnemonic"]] if with_seed else []))
        if with_seed:
            chmod_ok = False
            if os.name != "nt":
                try:
                    os.chmod(fname, 0o600)
                    chmod_ok = True
                except OSError:
                    pass
            rows = [
                tr("  Файл {f} содержит seed-фразы в открытом виде.", f=paint(fname, C.BOLD)),
                "",
            ]
            if os.name == "nt":
                rows.append(paint(tr("  Windows не поддерживает unix-права доступа (chmod) — файл"), C.YEL))
                rows.append(paint(tr("  доступен всем, у кого есть доступ к этой папке/диску."), C.YEL))
            elif not chmod_ok:
                rows.append(paint(tr("  Не удалось ограничить права доступа к файлу (chmod не сработал)."), C.YEL))
            else:
                rows.append(paint(tr("  Права доступа ограничены (chmod 600, только текущий пользователь)."), C.GREY))
            rows += [
                "",
                paint(tr("  Рекомендация: перенеси файл в зашифрованное хранилище (или на офлайн-"), C.GREY),
                paint(tr("  носитель) и удали его отсюда сразу после того, как он больше не нужен."), C.GREY),
            ]
            self.ui.page(tr("экспорт с seed-фразами"), rows)
            pause()
        self.set_flash(tr("экспортировано {n} кошельков → {f}", n=len(self.wallets), f=fname))

    def screen_import(self):
        self.ui.page(tr("импорт seed-фразы"), [
            tr("  Вставь seed-фразу (24 слова TON или 12/18/24 слова BIP-39) через пробел."),
            tr("  Версия кошелька по умолчанию: {v} (v3r2 | v4r2 | v5r1).", v=WALLET_VERSION),
        ])
        words = ask(tr("seed-фраза (Enter — отмена)")).lower().split()
        if len(words) not in (12, 18, 24):
            self.set_flash(tr("импорт отменён: нужно 12/18/24 слова"), ok=False)
            return
        version = ask(tr("версия"), WALLET_VERSION).lower()
        if version not in WALLET_CLASSES:
            self.set_flash(tr("неизвестная версия"), ok=False)
            return
        try:
            wallet, _, _, _ = WALLET_CLASSES[version].from_mnemonic(self.client, words)
        except Exception as exc:
            self.set_flash(tr("seed не принят: {e}", e=str(exc)[:60]), ok=False)
            return
        address = wallet.address.to_str(is_bounceable=False)
        if any(w["address"] == address for w in self.wallets):
            self.set_flash(tr("такой кошелёк уже есть в хранилище"), ok=False)
            return
        name = ask(tr("имя"), f"{NAME_PREFIX}-{self.next_name(NAME_PREFIX):02d}")[:24]
        self.wallets.append({
            "name": name, "address": address, "version": version, "mnemonic": " ".join(words),
            "created": datetime.now().isoformat(timespec="seconds"),
            "ton": None, "usdt": None, "state": None, "usdt_wallet": None, "checked": None,
        })
        self.save()
        self.set_flash(tr("импортирован {name} · {addr}", name=name, addr=short(address)))


# ═══════════════════════ ЗАПУСК ═══════════════════════
def prepare_terminal():
    if os.name == "nt":
        os.system("")  # включает обработку ANSI-кодов в cmd / PowerShell
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
            if policy:
                asyncio.set_event_loop_policy(policy())
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


async def main():
    load_config()  # язык интерфейса из tonforge.config.json
    if WALLET_VERSION not in WALLET_CLASSES:
        sys.exit(tr("WALLET_VERSION должен быть одним из: {v}", v=", ".join(WALLET_CLASSES)))
    kwargs = {
        "network": NetworkGlobalID.TESTNET if IS_TESTNET else NetworkGlobalID.MAINNET,
        "retry_policy": DEFAULT_HTTP_RETRY_POLICY,
    }
    if TONCENTER_API_KEY:
        kwargs.update(api_key=TONCENTER_API_KEY, rps_limit=RPS_LIMIT)
    client = ToncenterClient(**kwargs)
    async with client:
        app = App(client)
        if await app.boot():
            await app.run()
    Screen.clear()
    print(paint(tr("  TONFORGE · хранилище закрыто, до встречи") + "\n", C.GREY))


if __name__ == "__main__":
    prepare_terminal()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(paint("\n" + tr("  прервано (Ctrl+C) — все изменения уже сохранены в хранилище") + "\n", C.GREY))
