# TONFORGE

Установка - pip install tonutils cryptography

Запуск - python tonforge.py

🇷🇺 Русский
TONFORGE — консольный менеджер кошельков TON и USDT
TONFORGE — это генератор и библиотека-инструмент на Python, которая превращает набор ползунков в веб-конфигураторе в готовый консольный скрипт для массовой работы с криптокошельками в сети TON (The Open Network). Проект решает задачу управления пулами кошельков для выплат, фарминга, тестирования и собственной автоматизации — без единой строчки собственного кода.

Что умеет скрипт:
![Uploading IMG_4009.JPG…]()

Пакетное создание кошельков — генерация пачек по 10+ кошельков (V3R2 / V4R2 / W5) с именами worker-01… за доли секунды; seed-фразы создаются локально.
Зашифрованное хранилище — все seed-фразы хранятся в одном JSON-файле, зашифрованном Fernet (AES) с ключом из PBKDF2-SHA256 (390 000 итераций); пароль спрашивается при старте.
Мониторинг балансов — баланс TON и USDT по каждому кошельку, статус контракта, кэширование адресов jetton-кошельков.
Переводы куда угодно — отправка TON и USDT с любого кошелька на любой адрес в три шага с подтверждением, режим all выводит весь баланс (send mode 128).
Сбор средств — автоматическое сведение USDT и/или TON со всех кошельков на один адрес с подтверждением каждой транзакции через SeqnoGuard и поддержкой неразвёрнутых кошельков.
Страничный интерфейс консоли — каждый шаг очищает экран и рисует новую «страницу» с рамкой и статусной строкой: никакого спама, только результат.
Сервисное — экспорт адресов в CSV, импорт существующих seed-фраз, переименование, пагинация, ссылки на Tonviewer/Tonscan, Mainnet и Testnet.
Веб-конфигуратор (React + TypeScript + Tailwind) собирает скрипт под задачу: сеть, размер пачки, версия кошелька, газ для USDT, шифрование, RPS-лимиты — и содержит интерактивное демо консоли, где можно «потыкать» логику до скачивания. Бонусом в проект включён CL!CKFORGE — второй генератор, создающий автокликер для мыши и клавиатуры с джиттером, сериями, хоткеями и встроенным симулятором.

Стек: Python 3.9+, tonutils (официальный SDK для TON), cryptography · React 19, TypeScript, Tailwind CSS 4, Framer Motion, Vite.

🇬🇧 English
TONFORGE — a console wallet manager for TON & USDT
TONFORGE is a Python generator toolkit that turns a set of sliders in a web configurator into a ready-to-run console script for managing crypto wallets at scale on the TON blockchain (The Open Network). It covers payout pools, farming setups, testing environments, and personal automation — without writing a single line of your own code.

What the script does:

Batch wallet creation — generate batches of 10+ wallets (V3R2 / V4R2 / W5) named worker-01… in a fraction of a second; seed phrases are created locally.
Encrypted vault — all seed phrases live in a single JSON file encrypted with Fernet (AES) under a PBKDF2-SHA256-derived key (390,000 iterations); the password is asked at startup.
Balance monitoring — TON and USDT balances per wallet, contract state tracking, cached jetton-wallet addresses.
Send anywhere — transfer TON or USDT from any wallet to any address in three confirmation-aware steps; the all keyword sweeps the entire balance (send mode 128).
Fund sweeping — automatically consolidate USDT and/or TON from every wallet onto a single address, with per-transaction confirmation via SeqnoGuard and support for undeployed wallets.
Paged console UI — every step clears the screen and renders a new framed "page" with a status bar: no scrolling spam, only results.
Utilities — CSV export, existing seed-phrase import, renaming, pagination, Tonviewer/Tonscan links, Mainnet and Testnet.
The web configurator (React + TypeScript + Tailwind) tailors the script to your needs — network, batch size, wallet version, USDT gas amount, encryption, RPS limits — and ships an interactive console demo so you can try the workflow before downloading. The project also bundles CL!CKFORGE, a companion generator that produces a mouse-and-keyboard autoclicker with jitter, burst mode, hotkeys, and a built-in simulator.

Stack: Python 3.9+, tonutils (official TON SDK), cryptography · React 19, TypeScript, Tailwind CSS 4, Framer Motion, Vite.
