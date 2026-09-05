<img width="1960" height="520" alt="tonforge-horizontal (1)" src="https://github.com/user-attachments/assets/020829b6-ba79-4c65-a070-0c3fe9bfe834" />

> [!IMPORTANT]
> ## TONFORGE v0.9.0-rc1
<img width="774" height="312" alt="image" src="https://github.com/user-attachments/assets/45fe13d8-60a8-4c56-9c6f-de463ff06165" />

**🔄 What's New**
TON ↔ USDT swaps via STON.fi
Available in [12] Main Menu and [8] Wallet
Supports both directions + all amount
Detailed confirmation with rate, output, slippage, pool fee & gas

**🔐 Security**
Private keys never leave the device
Swapped tokens are received on the same wallet
Quotes fetched from api.ston.fi
Swap transaction built locally according to STON.fi v1
Transaction structure verified against the official ston-fi/sdk


> [!IMPORTANT]
> ## TONFORGE v0.8.5 Beta
<img width="775" height="232" alt="image" src="https://github.com/user-attachments/assets/9542dced-fd90-4f99-b637-04375bd0edc3" />

**🐛 Bug Fixes** 
Fixed text overflowing outside the terminal frame.
Fixed incorrect rendering of long colored (ANSI) lines.
Improved raw address and TonViewer link display on narrow terminals.
Fixed potential terminal issues caused by untrusted transaction comments.

**⚡ Improvements**
Added fit_ansi() for safe text truncation while preserving ANSI colors.
Increased PBKDF2 from 390,000 → 600,000 iterations for new vaults.
Added compatibility with existing vaults using 390,000 iterations.
Improved vault file protection with 0600 permissions.
Improved CSV formula escaping and export overwrite protection.

**🔐 Security**
Added protection against ANSI injection through transaction comments.
Existing wallets, passwords, and wallets.vault files remain compatible.



> [!IMPORTANT]
> ## TONFORGE v0.8.0 Beta
<img width="771" height="264" alt="image" src="https://github.com/user-attachments/assets/661fa170-516c-42fb-841f-b60fe42ccb06" />
<img width="779" height="375" alt="image" src="https://github.com/user-attachments/assets/1812373b-1a28-4af3-bf24-9098c5ea3214" />


**📜 Transaction History**

Transactions are now displayed in 2 lines with full addresses.
Transaction details clearly show sender and recipient.
Friendly and raw 0:... addresses are displayed.
7 transactions per page with pagination.
USD₮ is automatically displayed as USDT.

**🌐 Language**

Added [11] Language / Язык.
Full interface is available in Russian and English.
259 strings translated with 100% coverage.
Language preference is saved in tonforge.config.json.

**🖥️ Interface**

Frame width limited to 96 columns.
Removed unnecessary empty space for a more compact interface.

**🔐 Vault**

wallets.vault format has not changed.
Existing wallets and passwords remain fully compatible.
Startup remains unchanged:
python tonforge.py

**✅ Validation**

py_compile ✓ · Typegen ✓ · TSC ✓ · Build ✓ · HTTP 200 ✓



> [!IMPORTANT]
> ## TONFORGE v0.7.0 Beta
<img width="644" height="232" alt="image" src="https://github.com/user-attachments/assets/a54070a5-a89d-41c6-8e3a-969ddc87977a" />
<img width="644" height="266" alt="image" src="https://github.com/user-attachments/assets/0af9d698-57f2-4b92-bef1-d2d33cfae5e9" />

**What’s Changed**
* **🔐 Vault:** improved password handling and corrupted JSON error handling.
* **💰 Balances:** balances are now automatically refreshed before sending TON/USDT.
* **📤 Transfers:** sending operations use fresh balance data immediately before confirmation.
* **🔄 Mass Sweep:** all wallet balances are now automatically refreshed before building the sweep plan.
* **⛽ Gas:** JETTON_GAS_TON = 0.05 TON; a higher safety threshold of 0.1 TON was added for mass USDT sweeps.
* **🌐 Network:** uses Address.is_test_only instead of detecting the network by address prefix.
* **🧹 Seed Cleanup:** added the maximum practical memory cleanup available in Python with gc.collect(), with the limitations explicitly documented.
* **📜 Transaction History:** added TON and USDT transaction history using TonAPI.
* **🟢🔴 Direction:** incoming and outgoing transactions are displayed with signs and color indicators.
* **📄 History Details:** pagination, amount, direction, counterparty address, timestamp, comment, and blockchain explorer link.
* **🛡️ Verification:** tonutils APIs and implemented methods were checked against the actual installed library; fixes that did not match the real API were not applied.
* **Status:** TON/USDT transfers, balance refresh, mass sweep, and transaction history have been updated and verified.

> [!IMPORTANT]
> ## TONFORGE v0.5.0 Beta
<img width="642" height="230" alt="image" src="https://github.com/user-attachments/assets/0f331dcf-2f6e-4fa6-abcd-91b5fa438e5a" />
<img width="646" height="402" alt="image" src="https://github.com/user-attachments/assets/c7059bdf-bffb-4587-9461-f1e9f24b02aa" />


# 🇷🇺 Русский
# TONFORGE

**TONFORGE** — консольный менеджер криптовалютных кошельков для сети **TON** с поддержкой **TON и USDT**. Проект предназначен для удобного создания, хранения, мониторинга и управления несколькими кошельками через единый интерфейс.

### Возможности

* 🔐 Зашифрованное хранилище кошельков и seed-фраз
* 👛 Создание одного или нескольких кошельков пакетно
* 💰 Просмотр балансов TON и USDT
* 📤 Отправка TON и USDT
* 🔄 Массовый перевод средств с нескольких кошельков
* 📋 Экспорт и импорт данных в CSV
* 🪪 Просмотр подробной информации о каждом кошельке
* 🌐 Поддержка Mainnet и Testnet
* ⚙️ Поддержка нескольких версий TON-кошельков
* 🔑 Защита хранилища паролем с использованием PBKDF2-SHA256 и Fernet

TONFORGE ориентирован на разработчиков и пользователей, которым необходимо работать с несколькими TON-кошельками из одного консольного приложения.

> Используйте проект только с собственными кошельками и средствами либо в среде, где у вас есть соответствующее разрешение.


# 🇬🇧 English
# TONFORGE

**TONFORGE** is a console-based cryptocurrency wallet manager for the **TON blockchain**, with support for **TON and USDT**. It provides a convenient way to create, store, monitor, and manage multiple wallets through a single interface.

### Features

* 🔐 Encrypted wallet and seed phrase storage
* 👛 Single and batch wallet generation
* 💰 TON and USDT balance monitoring
* 📤 TON and USDT transfers
* 🔄 Bulk fund transfers between wallets
* 📋 CSV import and export
* 🪪 Detailed wallet information and wallet cards
* 🌐 Mainnet and Testnet support
* ⚙️ Support for multiple TON wallet versions
* 🔑 Password-protected vault using PBKDF2-SHA256 and Fernet encryption

TONFORGE is designed for developers and users who need to manage multiple TON wallets from a single command-line application.

> Use this project only with wallets and funds you own or in environments where you have proper authorization.

1) pip install tonutils cryptography
2) python tonforge.py
