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
import sys
import time
import warnings
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

if sys.version_info < (3, 9):
    sys.exit("Нужен Python 3.9 или новее")

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
ENCRYPT_VAULT     = True       # шифровать хранилище паролем (нужен пакет cryptography)
JETTON_GAS_TON    = 0.00       # TON, прикладываемые к переводу USDT (излишек вернётся)
SWEEP_RESERVE_TON = 0       # сколько TON оставлять при сборе (0 = забрать всё)
CONFIRM_SENDS     = True       # спрашивать подтверждение перед каждой отправкой
EXPLORER          = "tonviewer"  # tonviewer | tonscan
ROWS_PER_PAGE     = 15          # строк списка на одной странице

# ═══════════════════════ КОНСТАНТЫ ═══════════════════════
IS_TESTNET = NETWORK == "testnet"
USDT_MASTER = (
    "kQB0ZYUL5M3KfrW0tSnwdFO1nC-BQHC2gcZl-WaF2on_USDT"
    if IS_TESTNET
    else "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
)
USDT_DECIMALS = 6
TON_DECIMALS = 9
MIN_TON_FOR_JETTON = to_nano(JETTON_GAS_TON) + to_nano(0.01)
WALLET_CLASSES = {"v3r2": WalletV3R2, "v4r2": WalletV4R2, "v5r1": WalletV5R1}
STATE_RU = {
    "nonexist": "новый",
    "uninit": "не развёрнут",
    "active": "активен",
    "frozen": "заморожен",
}
W = 80  # ширина «страницы» в символах


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


def to_units(text, decimals):
    """'12.5' -> 12500000 при decimals=6. Бросает ValueError."""
    try:
        d = Decimal(str(text).replace(",", ".").strip())
    except InvalidOperation:
        raise ValueError("это не число")
    if d <= 0:
        raise ValueError("сумма должна быть больше нуля")
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


def pause(msg="Enter — назад"):
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
        right = paint(f"{NETWORK} · кошельков: {len(self.app.wallets)}", C.GREY)
        gap = " " * max(1, W - 4 - vis_len(left) - vis_len(right))
        self.line(left + gap + right)
        print("├" + "─" * (W - 2) + "┤")
        self.line()
        for row in body:
            self.line(row)
        self.line()
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
                return bool(json.load(f).get("encrypted"))
        except Exception:
            return False

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
                raise VaultLocked("неверный пароль")
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
            print(paint("\n  Для шифрования нужен пакет cryptography:  pip install cryptography", C.RED))
            print("  Либо поставь ENCRYPT_VAULT = False (хранилище будет открытым текстом).\n")
            sys.exit(1)

    def wallet_row(self, i, w):
        state = STATE_RU.get(w.get("state") or "", "?") if w.get("checked") else "не проверен"
        return (
            f"  {i:>3}  {w['name'][:12]:<12} {short(w['address']):<15} "
            f"{fmt_ton(w.get('ton')):>10} {fmt_usdt(w.get('usdt')):>10}  " + paint(state, C.GREY)
        )

    def table_head(self):
        return paint(f"  {'№':>3}  {'имя':<12} {'адрес':<15} {'TON':>10} {'USDT':>10}  состояние", C.GREY)

    # ---------- вход ----------
    async def boot(self):
        if ENCRYPT_VAULT or (self.vault.exists() and self.vault.file_encrypted()):
            self.ensure_crypto()
        rows = [
            f"  Файл хранилища: {paint(VAULT_FILE, C.BOLD)}",
            f"  Сеть: {paint(NETWORK, C.BOLD)}   версия кошельков: {paint(WALLET_VERSION, C.BOLD)}",
            "",
        ]
        if self.vault.exists():
            enc = self.vault.file_encrypted()
            rows.append("  Хранилище найдено. " + ("Введи пароль, чтобы расшифровать." if enc else "Открываю…"))
            self.ui.page("вход", rows)
            for attempt in range(3):
                pwd = secret("пароль хранилища") if enc else ""
                try:
                    self.wallets, vault_net = self.vault.open(pwd)
                    break
                except VaultLocked:
                    print(paint(f"   ✖ неверный пароль ({attempt + 1}/3)", C.RED))
            else:
                print(paint("   доступ закрыт\n", C.RED))
                return False
            if vault_net != NETWORK:
                self.set_flash(f"внимание: хранилище создано для сети {vault_net}, а сейчас {NETWORK}", ok=False)
            else:
                self.set_flash(f"хранилище открыто · кошельков: {len(self.wallets)}")
            return True

        if ENCRYPT_VAULT:
            rows += [
                "  Хранилища ещё нет — создаём новое.",
                "  Придумай пароль: он шифрует все seed-фразы. Потеряешь пароль —",
                "  потеряешь доступ к файлу (восстановить кошельки можно только по seed).",
            ]
            self.ui.page("новое хранилище", rows)
            while True:
                p1 = secret("новый пароль")
                if len(p1) < 6:
                    print(paint("   минимум 6 символов", C.YEL))
                    continue
                if p1 != secret("повтори пароль"):
                    print(paint("   пароли не совпадают", C.YEL))
                    continue
                break
            self.vault.create(p1)
        else:
            rows.append("  Хранилища ещё нет — создаю новое " + paint("(без шифрования!)", C.YEL))
            self.ui.page("новое хранилище", rows)
            self.vault.create("")
        self.wallets = []
        self.set_flash(f"создано хранилище {VAULT_FILE}")
        return True

    # ---------- главный цикл ----------
    async def run(self):
        while True:
            self.ui.page("главное меню", self.menu_rows())
            ch = ask("выбор").lower()
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
            elif ch in ("0", "q", "exit", "выход"):
                return
            else:
                self.set_flash("нет такого пункта", ok=False)

    def menu_rows(self):
        tot_ton = sum(w.get("ton") or 0 for w in self.wallets)
        tot_usdt = sum(w.get("usdt") or 0 for w in self.wallets)
        checked = [w["checked"] for w in self.wallets if w.get("checked")]
        stamp = ("обновлено " + max(checked)[11:19]) if checked else "балансы ещё не запрашивались"
        return [
            "  " + paint("ИТОГО", C.GREY) + "   " + paint(fmt_ton(tot_ton), C.BOLD) + " TON   "
            + paint(fmt_usdt(tot_usdt), C.BOLD) + " USDT   " + paint(stamp, C.GREY),
            "",
            two_col(key(1) + " Список кошельков", key(6) + " Собрать всё на один адрес"),
            two_col(key(2) + f" Создать пачку ({BATCH_SIZE} шт)", key(7) + " Показать seed-фразу"),
            two_col(key(3) + " Обновить балансы", key(8) + " Экспорт адресов в CSV"),
            two_col(key(4) + " Перевод TON", key(9) + " Импорт seed-фразы"),
            two_col(key(5) + " Перевод USDT", key(0) + " Выход"),
        ]

    # ---------- список и карточка ----------
    def pick_wallet(self, title, hint):
        if not self.wallets:
            self.set_flash("кошельков пока нет — создай пачку в пункте 2", ok=False)
            return None
        limit = ROWS_PER_PAGE * 2
        rows = [self.table_head()]
        rows += [self.wallet_row(i, w) for i, w in enumerate(self.wallets[:limit], 1)]
        if len(self.wallets) > limit:
            rows.append(paint(f"  … ещё {len(self.wallets) - limit} — номер можно ввести вручную", C.GREY))
        rows += ["", paint("  " + hint, C.GREY)]
        self.ui.page(title, rows)
        ch = ask("номер кошелька (Enter — отмена)")
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
                rows.append("  пусто — создай пачку в пункте [2]")
            rows += ["", paint("  номер — карточка кошелька · n/p — листать · Enter — назад", C.GREY)]
            self.ui.page(f"кошельки · стр. {page + 1}/{pages}", rows)
            ch = ask("выбор").lower()
            if ch == "":
                return
            if ch == "n":
                page = min(page + 1, pages - 1)
            elif ch == "p":
                page = max(page - 1, 0)
            elif ch.isdigit() and 1 <= int(ch) <= total:
                await self.screen_wallet(int(ch) - 1)
            else:
                self.set_flash("не понял команду", ok=False)

    async def screen_wallet(self, idx):
        while idx < len(self.wallets):
            w = self.wallets[idx]
            checked = paint(f"   проверено {w['checked'][11:19]}", C.GREY) if w.get("checked") \
                else paint("   балансы не запрашивались", C.GREY)
            rows = [
                "  " + paint(w["name"], C.BOLD)
                + paint(f"   {w.get('version', WALLET_VERSION)} · создан {w.get('created', '?')[:10]}", C.GREY),
                "  " + paint(w["address"], C.CYAN),
                "  " + explorer_link("address", w["address"]),
                "",
                f"  Баланс:  {paint(fmt_ton(w.get('ton')), C.BOLD)} TON     {paint(fmt_usdt(w.get('usdt')), C.BOLD)} USDT",
                f"  Статус:  {STATE_RU.get(w.get('state') or '', '—')}" + checked,
                "",
                two_col(key(1) + " Перевести TON", key(4) + " Показать seed-фразу"),
                two_col(key(2) + " Перевести USDT", key(5) + " Переименовать"),
                two_col(key(3) + " Обновить баланс", key(6) + " Удалить из хранилища"),
                "",
                paint("  Enter — назад к списку", C.GREY),
            ]
            self.ui.page(f"кошелёк {w['name']}", rows)
            ch = ask("выбор")
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
                w["name"] = ask("новое имя", w["name"])[:24]
                self.save()
                self.set_flash("переименован")
            elif ch == "6":
                if self.delete_wallet(idx):
                    return
            else:
                self.set_flash("не понял команду", ok=False)

    def delete_wallet(self, idx):
        w = self.wallets[idx]
        rows = [
            "  " + paint("ВНИМАНИЕ", C.RED, C.BOLD) + f": запись {w['name']} будет удалена из хранилища.",
            "  Если seed-фраза не сохранена отдельно — доступ к средствам будет потерян.",
            f"  Баланс сейчас: {fmt_ton(w.get('ton'))} TON / {fmt_usdt(w.get('usdt'))} USDT",
            "",
            f"  Для подтверждения введи имя кошелька: {paint(w['name'], C.BOLD)}",
        ]
        self.ui.page("удаление", rows)
        if ask("имя") != w["name"]:
            self.set_flash("удаление отменено", ok=False)
            return False
        self.wallets.pop(idx)
        self.save()
        self.set_flash(f"{w['name']} удалён из хранилища")
        return True

    # ---------- создание ----------
    async def screen_create(self):
        rows = [
            f"  Сеть: {paint(NETWORK, C.BOLD)}   версия: {paint(WALLET_VERSION, C.BOLD)}   хранилище: {VAULT_FILE}",
            "  Seed-фразы генерируются локально и сразу сохраняются в хранилище.",
            "",
            f"  Сколько кошельков создать? Enter — {BATCH_SIZE}.",
        ]
        self.ui.page("создание кошельков", rows)
        raw = ask("количество", str(BATCH_SIZE))
        if not raw.isdigit() or not (1 <= int(raw) <= 500):
            self.set_flash("нужно число от 1 до 500", ok=False)
            return
        count = int(raw)
        prefix = ask("префикс имени", NAME_PREFIX).strip() or NAME_PREFIX
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
        rows = [f"  Создано {paint(count, C.BOLD)} кошельков, всего в хранилище: {len(self.wallets)}", ""]
        rows += [f"  {c['name']:<12} {paint(c['address'], C.CYAN)}" for c in created[:ROWS_PER_PAGE]]
        if count > ROWS_PER_PAGE:
            rows.append(paint(f"  … и ещё {count - ROWS_PER_PAGE} — смотри список [1]", C.GREY))
        rows += [
            "",
            paint("  Адреса уже принимают TON и USDT; контракт развернётся при первой отправке.", C.GREY),
            paint("  Чтобы кошелёк мог отправлять USDT, на нём должно быть ~0.1 TON на газ.", C.YEL),
        ]
        self.ui.page("готово", rows, status=paint(f"✔ сохранено в {VAULT_FILE}", C.LIME))
        pause()

    # ---------- балансы ----------
    async def screen_refresh(self):
        if not self.wallets:
            self.set_flash("нечего обновлять", ok=False)
            return
        self.ui.page("обновление балансов", [
            f"  Запрашиваю {len(self.wallets)} кошельков через toncenter…",
            paint("  без API-ключа это ~3 с на кошелёк; с ключом — доли секунды", C.GREY),
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
        text = f"балансы обновлены за {time.time() - t0:.1f} с"
        if errors:
            text += f" · ошибок: {errors} (сеть или лимит запросов — повтори позже)"
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
            idx = self.pick_wallet(f"перевод {label} · шаг 1/3 · откуда", "введи номер кошелька-отправителя")
            if idx is None:
                self.set_flash("перевод отменён", ok=False)
                return
        w = self.wallets[idx]
        when = w["checked"][11:19] if w.get("checked") else "никогда"
        rows = [
            f"  Откуда:  {paint(w['name'], C.BOLD)}  {paint(w['address'], C.CYAN)}",
            f"  Баланс:  {fmt_ton(w.get('ton'))} TON · {fmt_usdt(w.get('usdt'))} USDT" + paint(f"  (обновлён {when})", C.GREY),
            "",
            "  Адрес получателя — любой: UQ…, EQ…, 0Q…/kQ… (testnet) или raw 0:…",
            f"  Сумма в {label}; слово " + paint("all", C.BOLD) + " — отправить весь баланс.",
        ]
        if asset == "usdt":
            rows.append(paint(f"  К переводу USDT прикладывается {JETTON_GAS_TON} TON на газ, излишек вернётся.", C.GREY))
        self.ui.page(f"перевод {label} · шаг 2/3 · куда и сколько", rows)
        dest = ask("адрес получателя (Enter — отмена)")
        if not dest:
            self.set_flash("перевод отменён", ok=False)
            return
        try:
            dest_addr = Address(dest)
        except Exception:
            self.set_flash(f"адрес не распознан: {dest[:30]}", ok=False)
            return
        amount_raw = ask(f"сумма {label}").lower()
        comment = ask("комментарий (необязательно)", "")
        send_all = amount_raw == "all"
        try:
            if asset == "ton":
                units = None if send_all else to_units(amount_raw, TON_DECIMALS)
            elif send_all:
                if not w.get("usdt"):
                    raise ValueError("баланс USDT неизвестен или нулевой — сначала обнови баланс")
                units = int(w["usdt"])
            else:
                units = to_units(amount_raw, USDT_DECIMALS)
        except ValueError as exc:
            self.set_flash(f"сумма: {exc}", ok=False)
            return
        if asset == "ton":
            shown = "весь баланс" if send_all else fmt_ton(units)
        else:
            shown = fmt_usdt(units)
        dest_str = dest_addr.to_str(is_bounceable=False)
        warn = []
        if asset == "ton" and units is not None and w.get("ton") is not None and units + to_nano(0.01) > w["ton"]:
            warn.append("на кошельке может не хватить TON с учётом комиссии (~0.01)")
        if asset == "usdt":
            if w.get("usdt") is not None and units > w["usdt"]:
                warn.append("сумма больше известного баланса USDT")
            if w.get("ton") is not None and w["ton"] < MIN_TON_FOR_JETTON:
                warn.append(f"мало TON на газ: нужно ≥ {fmt_ton(MIN_TON_FOR_JETTON)} TON")
        if w.get("checked") is None:
            warn.append("балансы этого кошелька ещё не запрашивались")
        rows = [
            f"  Откуда:      {w['name']}  {short(w['address'])}",
            f"  Куда:        {paint(dest_str, C.CYAN)}",
            f"  Сумма:       {paint(shown + ' ' + label, C.BOLD)}",
            f"  Комментарий: {comment or '—'}",
            f"  Сеть:        {NETWORK}",
            "",
        ]
        rows += [paint("  ! " + t, C.YEL) for t in warn]
        rows += ["", paint("  Транзакции в TON необратимы. Проверь адрес ещё раз.", C.GREY)]
        self.ui.page(f"перевод {label} · шаг 3/3 · подтверждение", rows)
        if CONFIRM_SENDS and not confirm("отправить?"):
            self.set_flash("перевод отменён", ok=False)
            return
        try:
            msg = await self.do_send(w, asset, dest_addr, units, comment)
        except Exception as exc:
            self.set_flash(f"ошибка отправки: {str(exc)[:70]}", ok=False)
            return
        rows = [
            f"  Отправлено {paint(shown + ' ' + label, C.BOLD)} с {w['name']} → {short(dest_str)}",
            "",
            "  hash:  " + paint(msg.normalized_hash, C.CYAN),
            "  " + explorer_link("address", w["address"]),
            "",
            paint("  Транзакция появится в обозревателе через 5–15 секунд.", C.GREY),
        ]
        self.ui.page("отправлено", rows, status=paint("✔ сообщение принято сетью", C.LIME))
        self.set_flash(f"{label} отправлены с {w['name']}")
        if confirm("обновить баланс кошелька сейчас?"):
            await asyncio.sleep(6)
            await self.refresh_wallets([idx])

    # ---------- сбор средств ----------
    async def screen_sweep(self):
        if not self.wallets:
            self.set_flash("кошельков нет", ok=False)
            return
        rows = [
            "  Соберёт средства со ВСЕХ кошельков хранилища на один адрес.",
            f"  Резерв TON на каждом кошельке: {paint(SWEEP_RESERVE_TON, C.BOLD)}  (SWEEP_RESERVE_TON; 0 = забрать всё)",
            f"  USDT уходят только с кошельков, где есть ≥ {fmt_ton(MIN_TON_FOR_JETTON)} TON на газ.",
            "",
            two_col(key(1) + " Только USDT", key(3) + " USDT, затем TON"),
            two_col(key(2) + " Только TON", "Enter — отмена"),
        ]
        self.ui.page("сбор средств · шаг 1/3", rows)
        what = ask("что собираем")
        if what not in ("1", "2", "3"):
            self.set_flash("сбор отменён", ok=False)
            return
        try:
            dest_addr = Address(ask("адрес-получатель"))
        except Exception:
            self.set_flash("адрес не распознан", ok=False)
            return
        dest_str = dest_addr.to_str(is_bounceable=False)
        need = any(w.get("checked") is None for w in self.wallets)
        if need or confirm("обновить балансы перед сбором? (рекомендуется)"):
            self.ui.page("сбор средств · обновляю балансы", ["  Секунду, запрашиваю актуальные балансы…"])
            await self.refresh_wallets()
        reserve, fee = to_nano(SWEEP_RESERVE_TON), to_nano(0.01)
        plan = []
        for i, w in enumerate(self.wallets):
            ton, usdt = w.get("ton") or 0, w.get("usdt") or 0
            if what in ("1", "3") and usdt > 0 and ton >= MIN_TON_FOR_JETTON:
                plan.append((i, "usdt", usdt))
                ton -= to_nano(JETTON_GAS_TON)  # считаем консервативно, часть газа вернётся
            if what in ("2", "3"):
                if reserve == 0 and ton > fee:
                    plan.append((i, "ton", None))
                elif reserve > 0 and ton - reserve - fee > 0:
                    plan.append((i, "ton", ton - reserve - fee))
        if not plan:
            self.set_flash("нечего собирать: балансы нулевые или мало TON на газ", ok=False)
            return

        def describe(asset, units):
            if units is None:
                return "весь остаток TON"
            return fmt_usdt(units) + " USDT" if asset == "usdt" else fmt_ton(units) + " TON"

        rows = [f"  Получатель: {paint(dest_str, C.CYAN)}", f"  Операций: {paint(len(plan), C.BOLD)}", ""]
        rows += [f"  {self.wallets[i]['name']:<12} → {describe(a, u)}" for i, a, u in plan[:ROWS_PER_PAGE]]
        if len(plan) > ROWS_PER_PAGE:
            rows.append(paint(f"  … и ещё {len(plan) - ROWS_PER_PAGE}", C.GREY))
        uninit = sorted({self.wallets[i]["name"] for i, _, _ in plan if self.wallets[i].get("state") in ("uninit", "nonexist")})
        if uninit:
            rows.append("")
            rows.append(paint("  не развёрнуты: " + ", ".join(uninit) + " — контракта в сети ещё нет,", C.YEL))
            rows.append(paint("  отправка с них развернёт кошелёк автоматически (без ожидания seqno).", C.YEL))
        rows += ["", paint("  Каждая отправка ждёт подтверждения предыдущей (SeqnoGuard) — это небыстро.", C.GREY)]
        self.ui.page("сбор средств · шаг 2/3 · план", rows)
        if not confirm("выполнить план?"):
            self.set_flash("сбор отменён", ok=False)
            return
        self.ui.page("сбор средств · шаг 3/3 · выполнение", [
            f"  {len(plan)} операций → {short(dest_str)}",
            paint("  ход выполнения ниже; не закрывай окно", C.GREY),
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
                        print(paint("✔ напрямую", C.LIME))
                        ok += 1
                        continue
                    except Exception as exc2:
                        exc = exc2
                print(paint("✖ " + str(exc)[:46], C.RED))
                fail += 1
        print()
        self.set_flash(f"сбор завершён: успешно {ok}, ошибок {fail}", ok=fail == 0)
        pause("Enter — в меню (балансы обновятся через пункт 3)")

    # ---------- seed ----------
    def screen_seed(self, idx=None):
        if idx is None:
            idx = self.pick_wallet("seed-фраза · выбор кошелька", "seed покажется только после подтверждения")
            if idx is None:
                return
        w = self.wallets[idx]
        rows = [
            "  " + paint("Seed-фраза даёт полный доступ к средствам.", C.YEL),
            "  Убедись, что за экраном никто не наблюдает и запись экрана выключена.",
            "",
            f"  Кошелёк: {paint(w['name'], C.BOLD)}  {short(w['address'])}",
        ]
        self.ui.page("seed-фраза · подтверждение", rows)
        if not confirm("показать seed-фразу на экране?"):
            self.set_flash("показ отменён", ok=False)
            return
        words = w["mnemonic"].split()
        version = w.get("version", WALLET_VERSION)
        rows = [f"  {paint(w['name'], C.BOLD)}  ·  {version}  ·  {paint(w['address'], C.CYAN)}", ""]
        for r in range(0, len(words), 4):
            chunk = words[r:r + 4]
            rows.append("   " + "".join(
                f"{paint(str(r + c + 1).rjust(2), C.GREY)} {word:<12}" for c, word in enumerate(chunk)
            ))
        rows += [
            "",
            paint(f"  При импорте в Tonkeeper / MyTonWallet выбери версию {version}, если адрес не совпал.", C.GREY),
            paint("  Экран будет очищен после Enter.", C.GREY),
        ]
        self.ui.page("seed-фраза", rows)
        pause("Enter — скрыть")
        self.ui.clear()

    # ---------- экспорт / импорт ----------
    def screen_export(self):
        if not self.wallets:
            self.set_flash("экспортировать нечего", ok=False)
            return
        self.ui.page("экспорт CSV", [
            "  CSV с колонками: name; address; version; ton; usdt.",
            "  По желанию можно добавить seed-фразы — тогда файл становится секретным.",
        ])
        with_seed = confirm("включить seed-фразы в файл? (опасно)")
        default = f"{NAME_PREFIX}_wallets{'_SECRET' if with_seed else ''}_{datetime.now():%Y%m%d_%H%M}.csv"
        fname = ask("имя файла", default)
        with open(fname, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(["name", "address", "version", "ton", "usdt"] + (["mnemonic"] if with_seed else []))
            for w in self.wallets:
                row = [w["name"], w["address"], w.get("version", WALLET_VERSION),
                       fmt_ton(w.get("ton")), fmt_usdt(w.get("usdt"))]
                wr.writerow(row + ([w["mnemonic"]] if with_seed else []))
        if with_seed:
            try:
                os.chmod(fname, 0o600)
            except OSError:
                pass
        self.set_flash(f"экспортировано {len(self.wallets)} кошельков → {fname}")

    def screen_import(self):
        self.ui.page("импорт seed-фразы", [
            "  Вставь seed-фразу (24 слова TON или 12/18/24 слова BIP-39) через пробел.",
            f"  Версия кошелька по умолчанию: {WALLET_VERSION} (v3r2 | v4r2 | v5r1).",
        ])
        words = ask("seed-фраза (Enter — отмена)").lower().split()
        if len(words) not in (12, 18, 24):
            self.set_flash("импорт отменён: нужно 12/18/24 слова", ok=False)
            return
        version = ask("версия", WALLET_VERSION).lower()
        if version not in WALLET_CLASSES:
            self.set_flash("неизвестная версия", ok=False)
            return
        try:
            wallet, _, _, _ = WALLET_CLASSES[version].from_mnemonic(self.client, words)
        except Exception as exc:
            self.set_flash(f"seed не принят: {str(exc)[:60]}", ok=False)
            return
        address = wallet.address.to_str(is_bounceable=False)
        if any(w["address"] == address for w in self.wallets):
            self.set_flash("такой кошелёк уже есть в хранилище", ok=False)
            return
        name = ask("имя", f"{NAME_PREFIX}-{self.next_name(NAME_PREFIX):02d}")[:24]
        self.wallets.append({
            "name": name, "address": address, "version": version, "mnemonic": " ".join(words),
            "created": datetime.now().isoformat(timespec="seconds"),
            "ton": None, "usdt": None, "state": None, "usdt_wallet": None, "checked": None,
        })
        self.save()
        self.set_flash(f"импортирован {name} · {short(address)}")


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
    if WALLET_VERSION not in WALLET_CLASSES:
        sys.exit(f"WALLET_VERSION должен быть одним из: {', '.join(WALLET_CLASSES)}")
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
    print(paint("  TONFORGE · хранилище закрыто, до встречи\n", C.GREY))


if __name__ == "__main__":
    prepare_terminal()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(paint("\n  прервано (Ctrl+C) — все изменения уже сохранены в хранилище\n", C.GREY))
