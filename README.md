<img width="1960" height="520" alt="tonforge-horizontal (1)" src="https://github.com/user-attachments/assets/020829b6-ba79-4c65-a070-0c3fe9bfe834" />

> [!IMPORTANT] TONFORGE v0.8.0 Beta

**📜 Transaction History**

History entries now use 2 lines per transaction.
Full friendly addresses are displayed.
Transaction details now clearly show Sender and Recipient.
Both friendly and raw 0:... addresses are displayed.
Wallet names are shown when the address belongs to a wallet from the vault.
History now displays 7 transactions per page.
Terminal frame width adapts to the available space (84–110 columns).
USD₮ from TonAPI is normalized to USDT.

**🌐 Language Support**

Added [11] Language / Язык.
The entire interface can now be switched between Russian and English.
Translated 259 user-facing strings, including menus, screens, prompts, warnings, wallet statuses, transaction history, confirmations, and errors.
Translation coverage: 100%.
All 267 translation pairs have matching placeholders such as {n}, {name}, etc.
Selected language is saved in tonforge.config.json and restored automatically on the next launch.

**🖥️ Interface**

Reduced the maximum terminal frame width to 96 columns.
Removed the unnecessary empty line at the bottom of the interface.
The interface is now more compact in both Russian and English modes.

**🔐 Vault Compatibility**

The wallets.vault format has not changed.
Existing wallets and passwords remain fully compatible.
No migration is required.
Startup remains unchanged:
python tonforge.py

**✅ Validation**

py_compile ✓
Typegen ✓
TypeScript check ✓
Build ✓
Server restart ✓
HTTP response 200 ✓
Language option [11] verified ✓

Status: transaction history, bilingual interface, terminal layout, and vault compatibility have been updated and verified.


# TONFORGE v0.7.0 Beta 
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

# TONFORGE v0.5.0 Beta
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
