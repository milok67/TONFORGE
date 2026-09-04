#!/bin/sh

echo "=== TONFORGE INSTALLER ==="

echo "[1/3] Обновляем пакеты..."
apk update

echo "[2/3] Устанавливаем Python и pip..."
apk add python3 py3-pip

echo "[3/3] Устанавливаем зависимости TONFORGE..."
python3 -m pip install -r requirements.txt

echo
echo "=== УСТАНОВКА ЗАВЕРШЕНА ==="
echo "Запуск TONFORGE:"
echo "python3 tonforge.py"
Тогда после клонирования проекта тебе достаточно будет:
git clone https://github.com/milok67/TONFORGE.git
cd TONFORGE
sh install.sh
python3 tonforge.py