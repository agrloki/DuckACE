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

Find_by-id
Приоритетный поиск по ID устройства:

Теперь сначала проверяется путь /dev/serial/by-id/usb-ANYCUBIC_ACE_1-if*

Если находится - возвращается реальный путь к устройству через os.path.realpath()

Многоуровневая система поиска:

mermaid
Copy
graph TD
  A[Поиск по ID usb-ANYCUBIC_ACE_1-if*] -->|Найдено| B[Возврат устройства]
  A -->|Не найдено| C[Поиск по VID/PID]
  C -->|Найдено| B
  C -->|Не найдено| D[Поиск по описанию]
  D -->|Найдено| B
  D -->|Не найдено| E[Поиск по стандартным путям]
  E -->|Найдено| B
  E -->|Не найдено| F[Возврат None]
Дополнительные улучшения:

Добавлен поиск по стандартным путям (/dev/ttyACM*, /dev/ttyUSB*)

Сохранена обратная совместимость с предыдущими методами поиска

Как проверить работу:
Подключите устройство и выполните в терминале:

bash
Copy
ls /dev/serial/by-id/
Убедитесь, что ваше устройство отображается с нужным ID.

Проверьте работу поиска:

python
Copy
ace = BunnyAce(config)
print(f"Found device: {ace._find_ace_device()}")
В конфигурации можно явно указать порт или оставить автоопределение:

ini
Copy
[bunnyace]
serial: /dev/ttyACM0  # явное указание
# или
serial: auto         # автоопределение

Find_by-id_extended
Гибкий поиск по ID:

usb-ANYCUBIC_ACE_1-if* - ловит любые окончания (if00, if01 и т.д.)

Дополнительно ищет usb-ANYCUBIC* на случай других вариантов именования

Подробное логирование:

python
Copy
logging.info(f"Found ACE devices by ID: {ace_devices}")
Помогает при отладке подключения.

Расширенные критерии поиска:

Проверка VID/PID для популярных чипов (STM32, CH340)

Поиск по описанию порта в верхнем регистре (ANYCUBIC ACE → anycubic ace)

Резервные варианты:

python
Copy
['/dev/ttyACM*', '/dev/ttyUSB*']  # Стандартные пути
Пример работы:
При подключении устройства:

Copy
[INFO] Found ACE devices by ID: ['/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00']
Если изменился ID:

Copy
[INFO] Found ACE devices by ID: ['/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if01']
Дополнительная страховка:
Можно добавить udev-правила для создания стабильного симлинка:

Создайте файл /etc/udev/rules.d/99-anycubic-ace.rules:

bash
Copy
SUBSYSTEM=="tty", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", SYMLINK+="anycubic_ace"
(замените VID/PID на реальные)

Перезагрузите udev:

bash
Copy
sudo udevadm control --reload-rules
sudo udevadm trigger
Теперь можно использовать /dev/anycubic_ace в конфигурации:

ini
Copy
[ace]
serial: /dev/anycubic_ace