# DuckACE

A Work-In-Progress driver for Anycubic Color Engine Pro for Klipper

## Pinout

![Molex](/.github/img/molex.png)

- 1 - None (VCC, not required to work, ACE provides it's own power)
- 2 - Ground
- 3 - D-
- 4 - D+

Connect them to a regular USB, no dark magic is required.

Добавьте в printer.cfg:
[bunnyace]
serial: /dev/ttyACM0  # (опционально, если не указать - будет автопоиск)
baud: 115200
max_dryer_temperature: 55

Доступные команды:
ACE_STATUS              # Получить статус
ACE_START_DRYING TEMP=50 DURATION=120  # Сушить 2 часа при 50°C
ACE_STOP_DRYING        # Остановить сушку
ACE_DEBUG              # Проверить подключение

ACE-compared readme
Полная интеграция всех команд из второй версии:

Управление сушкой (START_DRYING, STOP_DRYING)

Управление подачей филамента (FEED, RETRACT)

Управление инструментами (CHANGE_TOOL)

Ассистент подачи (ENABLE_FEED_ASSIST, DISABLE_FEED_ASSIST)

Парковка (PARK_TO_TOOLHEAD)

Улучшенная система подключения:

Автопоиск устройства по VID/PID и описанию

Механизм переподключения при обрыве связи

Таймауты и ограничение попыток подключения

Более надежная работа с очередями:

Раздельные потоки для чтения и записи

Обработка задач в основном потоке Klipper

Корректное завершение потоков при отключении

Расширенная обработка ошибок:

Проверка CRC для всех пакетов

Валидация входных параметров команд

Подробное логирование всех операций

Оптимизированная работа с состоянием:

Автоматическое обновление статуса устройства

Обработка парковки и смены инструментов

Сохранение текущего состояния между перезагрузками

Улучшенный код:

Типизация (type hints)

Разделение на логические методы

Комментарии и документация

Как использовать:
Добавьте конфигурацию в printer.cfg:

ini
Copy
[bunnyace]
serial: /dev/ttyACM0  # (опционально)
baud: 115200
feed_speed: 50
retract_speed: 50
toolchange_retract_length: 100
max_dryer_temperature: 55
Доступные команды:

gcode
Copy
ACE_STATUS                # Получить статус
ACE_START_DRYING TEMP=50 DURATION=120  # Сушка 50°C 2 часа
ACE_FEED INDEX=0 LENGTH=50 SPEED=30    # Подать 50мм со скоростью 30
ACE_CHANGE_TOOL TOOL=1    # Сменить на инструмент 1
Логи будут записываться в /tmp/bunnyace.log.

