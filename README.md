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