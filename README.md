# WORK Stopped! This repo is deprecated!! Actual repo https://github.com/agrloki/ValgACE
# Работы по данному драйверу прекращены. Актуальный драйвер в репо https://github.com/agrloki/ValgACE
Причина прекращения работ, переработанный код фактически не содержит в себе исходного кода от проекта DuckACE.

В новой версии полностью переписано взаимодействие с serial, а также внесены значительные изменения в код функций.

Но заложенная автором философия проекта по возможности была сохранена.

## English

The refactored code no longer contains any of the original source code from the DuckACE project. The new version features:

A completely rewritten serial communication interface

Significant modifications to core functionality and command handling

As a result, this implementation has diverged substantially from its original foundation.

However, the original philosophical approach of the project's author has been preserved to the greatest extent possible.

# DuckACE

A Work-In-Progress driver for Anycubic Color Engine Pro for Klipper

## Pinout

![Molex](/.github/img/molex.png)

- 1 - None (VCC, not required to work, ACE provides it's own power)
- 2 - Ground
- 3 - D-
- 4 - D+

Connect them to a regular USB, no dark magic is required.


## Доступные команды:
- ACE_STATUS                               Получить статус
- ACE_START_DRYING TEMP=50 DURATION=120    Сушить 2 часа при 50°C
- ACE_STOP_DRYING                          Остановить сушку
- ACE_DEBUG                                Проверить подключение
- ACE_ENABLE_FEED_ASSIST INDEX=0 - 3       Включить помощь подачи филамента для конкретного порта
- ACE_DISABLE_FEED_ASSIST INDEX=0 - 3      Выключить помощь подачи филамента для конкретного порта
- ACE_PARK_TO_TOOLHEAD INDEX=0 - 3         Припарковать филамент к голове индекс указывает какой порт будет припаркован
- ACE_FEED INDEX=0-3 LENGTH=<длина подачи> SPEED=<Скорость подачи>     Подача филамента
- ACE_RETRACT INDEX=0-3 LENGTH=<длина подачи> SPEED=<Скорость подачи>  Откат филамента
- ACE_CHANGE_TOOL TOOL=-1 - 0 - 3          Смена инструмента. (Не работает пока не настроены макросы в ace.cfg)
- ACE_FILAMENT_INFO                        Информация о филаменте если есть rfid метка
