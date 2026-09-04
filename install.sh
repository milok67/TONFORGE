#!/bin/bash
# TONFORGE Installation Script
# Поддержка: macOS, Linux, Windows (WSL2)

set -e
set -u

echo "╭─ TONFORGE INSTALLER ──────────────────────╮"
echo "│ Менеджер кошельков TON + USDT              │"
echo "╰────────────────────────────────────────────╯"
echo ""

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 не установлен"
    echo "   Установи Python 3.9+ и повтори: sh install.sh"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✓ Python $PY_VERSION найден"

# Проверка версии Python
MIN_VERSION="3.9"
if [ "$(printf '%s\n' "$MIN_VERSION" "$PY_VERSION" | sort -V | head -n1)" != "$MIN_VERSION" ]; then
    echo "❌ Нужен Python 3.9 или новее (найден $PY_VERSION)"
    exit 1
fi

# Установка зависимостей
echo ""
echo "📦 Установка зависимостей..."
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt

echo ""
echo "╭─ УСТАНОВКА ЗАВЕРШЕНА ─────────────────────╮"
echo "│ Запуск:                                    │"
echo "│   python3 tonforge.py                      │"
echo "╰────────────────────────────────────────────╯"
