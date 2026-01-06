# Инструкция по обновлению на сервере

## ⚡ Последние изменения

### Режим алертов позиций (NEW!)

По умолчанию бот присылает алерты **ТОЛЬКО** на важные действия трейдера:
- ✅ **Открытие новой позиции** - трейдер зашел в новый coin
- ✅ **Закрытие позиции** - трейдер полностью вышел из позиции
- ✅ **Разворот позиции** - трейдер перевернул позицию (Long → Short или Short → Long)
- ❌ **НЕ присылает** алерты на промежуточные изменения размера (добавление/уменьшение токенов)

**Почему?** Изменения размера позиции из-за курса монет НЕ отслеживаются. Бот проверяет только изменение количества токенов (`szi`). Но если трейдер часто добавляет/убирает токены - это спамит уведомления.

**Настройка в `.env`:**
```bash
ONLY_MAJOR_POSITION_EVENTS=true  # По умолчанию - только открытие/закрытие/разворот
```

Если нужны **ВСЕ** изменения позиций выше порога:
```bash
ONLY_MAJOR_POSITION_EVENTS=false
MIN_POSITION_CHANGE_PCT=5.0  # Игнорировать изменения < 5%
```

---

## Предыдущие изменения

### Детальное отображение баланса трейдера

Добавлено детальное отображение баланса трейдера с разбивкой как в HyperDash:

```
💰 Total Value (Combined): $25.1M
   • Perp: $17.3M
   • Spot: $7.8M
```

**Исправлено получение Spot балансов:**
- Используем `spot_user_state()` для получения токенов + `all_mids()` для цен
- `spot_user_state()` возвращает **количество токенов**, а не USD!
- Расчет: `token_amount × current_price = USD value`
- Total (Combined) = Perp (marginSummary) + Spot (tokens × prices)

**Исправлено отображение Withdrawable % и Leverage:**
- **Withdrawable %** = `withdrawable / Perp equity` (а не Total!)
- **Leverage** = `total_position_value / Perp equity` (а не Total!)
- Это соответствует HyperDash, т.к. Spot не использует margin/leverage

**Новый UI для позиций:**
- Позиции теперь отображаются как **inline кнопки** с кратким описанием
- Формат кнопки: `BTC 🔴 SHORT | +$65k | $4.8m` (компактные числа!)
- **Компактное форматирование чисел в кнопках:**
  - < 1000: просто число (123)
  - Тысячи: 65k
  - Миллионы: 1.2m
  - Миллиарды: 1.5b
- **Сортировка позиций:**
  - Кнопки над списком: "📊 По PnL" и "💰 По Position Value"
  - Активная сортировка помечена галочкой (✓)
  - Сортировка по убыванию (самые большие сверху)
  - По умолчанию: сортировка по Position Value
- При нажатии на позицию - детальный просмотр со всеми метриками:
  - **Position Value / Size** - объединенная секция как на HyperDash:
    - Первая строка: Position Value в USD
    - Вторая строка: Размер с тикером (например, "1,577.76 ETH")
  - **Цены**: Входная, Текущая, Ликвидации
  - **Плечо и маржа**: Leverage и использованная маржа
  - **PnL**: Unrealized PnL и ROE в %
  - **Исторические сделки (fills)** - последние 5 сделок по позиции
- Кнопка "Назад к трейдеру" для возврата к общему обзору

**Commits:** 
- `beedee0` - Fix: Use spot_user_state API to get Spot balances
- `85052e0` - Fix: Calculate Spot balance in USD using token prices
- `3072f83` - Fix: Calculate Withdrawable % and Leverage from Perp equity
- `6cec96b` - feat: Redesign position display with inline buttons and detailed view
- `0623bda` - feat: Add compact number formatting for position buttons
- `1bb7286` - feat: Add position sorting by PnL and Position Value
- `405ffdc` - feat: Improve position detail layout - combine Position Value and Size
- `4424a5f` - fix: Fix IndentationError in user.py
- `a127a7b` - fix: Show negative sign for negative PnL in position buttons

## Обновление на сервере

### 1. Подключитесь к серверу
```bash
ssh root@faded-taste
cd ~/hyperliquid-trader-watcher
```

### 2. Получите последние изменения
```bash
git pull origin master
```

### 3. Пересоберите и запустите
```bash
# Обычный деплой (с кэшем библиотек - быстро!)
./deploy.sh

# ИЛИ форсированная пересборка (если нужно обновить зависимости)
./deploy.sh --force
```

**Примечание:** Обычный `./deploy.sh` использует Docker cache - библиотеки не переустанавливаются, только код обновляется. Это быстро! ⚡

Используйте `./deploy.sh --force` только если:
- Обновили `requirements.txt`
- Нужна чистая пересборка
- Есть проблемы с кэшем

### 4. Проверьте логи
```bash
# Смотрите логи в реальном времени
docker logs -f hyperliquid-trader-watcher

# Или проверьте файл лога
cat data/logs/app_latest.log
```

## Проверка работы

1. Откройте бота в Telegram
2. Перейдите в список трейдеров
3. Выберите трейдера
4. Проверьте общий обзор:
   - **Total Value (Combined)** - общий баланс
   - **• Perp** - баланс на деривативах
   - **• Spot** - баланс spot токенов
   - Withdrawable (USD + %)
   - Leverage (кратность + USD)
   - **Кнопки сортировки**: "📊 По PnL" и "💰 По Position Value"
   - **Список позиций как кнопки** с компактным форматом ($65k, $4.8m)
5. Попробуйте сортировку:
   - Нажмите "📊 По PnL" - позиции отсортируются по прибыли
   - Нажмите "💰 По Position Value" - по размеру позиции
   - Активная сортировка отмечена галочкой ✓
6. Нажмите на любую позицию:
   - Проверьте детальную информацию
   - Убедитесь что показываются исторические сделки (fills)
   - Нажмите "Назад к трейдеру" для возврата

## Отладка

Если баланс отображается некорректно, проверьте логи:

```bash
grep "Spot balance" data/logs/app_latest.log
grep "Total (Combined)" data/logs/app_latest.log
```

Логи покажут какой метод определения Spot используется:
- `Method 1` - spotMarginSummary.accountValue (предпочтительно)
- `Method 2` - Рассчитано из assetPositions
- `Method 3-5` - Другие методы

## Важно

⚠️ **Обычный деплой:** Просто запустите `./deploy.sh` - Docker кэширует слой с библиотеками, обновляется только код. Быстро! ⚡

⚠️ **Форсированная пересборка:** Используйте `./deploy.sh --force` только если обновили зависимости в `requirements.txt` или есть проблемы с кэшем.

⚠️ После каждого деплоя проверяйте логи чтобы убедиться что запущена правильная версия.

