import json
import time
from datetime import datetime
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Функция для конвертации timestamp в человеческий формат
def format_timestamp(timestamp_ms):
    """Конвертирует timestamp в миллисекундах в читаемый формат"""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')

def get_amount_from_delta(delta):
    """Извлекает сумму из объекта delta"""
    if not delta:
        return "N/A", "N/A"
    # Пытаемся найти сумму в разных возможных полях
    amount = delta.get('amount') or delta.get('usdc') or delta.get('token') or delta.get('value')
    # Получаем информацию о токене/валюте
    token = delta.get('token') or delta.get('coin') or "USDC"  # По умолчанию USDC
    
    # Если amount это словарь с токеном внутри
    if isinstance(amount, dict):
        token = list(amount.keys())[0] if amount else "USDC"
        amount = amount.get(token, "N/A")
    
    return (amount if amount else "N/A"), token

#address = input("Адрес: ").strip()
address = "0x9eec98d048d06d9cd75318fffa3f3960e081daab"
info = Info(constants.MAINNET_API_URL, skip_ws=True)

# Временные метки для запросов (за все время)
end_time = int(time.time() * 1000)
start_time = 0  # Начало эпохи - получим все данные за все время

print("=== USER STATE ===")
print(json.dumps(info.user_state(address), indent=2))

print("\n=== DEPOSITS, WITHDRAWALS AND OTHER LEDGER UPDATES ===")
ledger_updates = info.user_non_funding_ledger_updates(address, start_time, end_time)
print(json.dumps(ledger_updates, indent=2))

# Анализируем все типы транзакций
if ledger_updates:
    type_counts = {}
    for item in ledger_updates:
        delta_type = item.get("delta", {}).get("type")
        if delta_type:
            type_counts[delta_type] = type_counts.get(delta_type, 0) + 1
    
    print("\n=== FOUND TRANSACTION TYPES ===")
    print(f"Total transactions: {len(ledger_updates)}")
    print("\nBreakdown by type:")
    for tx_type, count in sorted(type_counts.items()):
        print(f"  - {tx_type}: {count}")
    print()

# Фильтруем только депозиты и выводы для удобства
if ledger_updates:
    print("\n=== DEPOSITS ONLY ===")
    deposits = [item for item in ledger_updates if item.get("delta", {}).get("type") == "deposit"]
    if deposits:
        print(f"Total deposits found: {len(deposits)}\n")
        total_amount = 0
        for deposit in deposits:
            timestamp = deposit.get('time')
            delta = deposit.get('delta', {})
            amount, token = get_amount_from_delta(delta)
            hash_val = deposit.get('hash')
            date_str = format_timestamp(timestamp)
            print(f"Date: {date_str} | Amount: {amount} {token} | Hash: {hash_val}")
            # Пытаемся добавить к общей сумме (если это число)
            try:
                total_amount += float(amount)
            except (ValueError, TypeError):
                pass
        print(f"\n💰 Total deposited: {total_amount:,.2f} USDC")
    else:
        print("No deposits found")
    
    print("\n=== WITHDRAWALS ONLY ===")
    withdrawals = [item for item in ledger_updates if item.get("delta", {}).get("type") == "withdraw"]
    if withdrawals:
        print(f"Total withdrawals found: {len(withdrawals)}\n")
        total_amount = 0
        for withdrawal in withdrawals:
            timestamp = withdrawal.get('time')
            delta = withdrawal.get('delta', {})
            amount, token = get_amount_from_delta(delta)
            hash_val = withdrawal.get('hash')
            date_str = format_timestamp(timestamp)
            print(f"Date: {date_str} | Amount: {amount} {token} | Hash: {hash_val}")
            # Пытаемся добавить к общей сумме (если это число)
            try:
                total_amount += float(amount)
            except (ValueError, TypeError):
                pass
        print(f"\n💸 Total withdrawn: {total_amount:,.2f} USDC")
    else:
        print("No withdrawals found")