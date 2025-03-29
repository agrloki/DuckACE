import serial
import serial.tools.list_ports
import threading
import time
import logging
import json
import struct
import queue
import traceback
import re
from serial import SerialException
from typing import Optional, Dict, List, Callable, Any

# Настройка логирования в файл и консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/bunnyace.log'),
        logging.StreamHandler()
    ]
)

class BunnyAce:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        self.event = threading.Event()
        self.lock = False
        self.send_time = None
        self.read_buffer = bytearray()
        
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        
        self.variables = self.printer.lookup_object('save_variables').allVariables

        # Автопоиск порта, если не указан явно
        default_serial = self._find_ace_device()
        self.serial_name = config.get('serial', default_serial or '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)
        
        # Настройки устройства
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 100)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        
        # Состояние устройства
        self._info = self._get_default_info()
        self._callback_map = {}
        self._request_id = 0
        self._connected = False
        self._connection_attempts = 0
        self._max_connection_attempts = 5
        
        # Очереди и таймеры
        self._queue = queue.Queue()
        self._main_queue = queue.Queue()
        
        # Регистрация обработчиков и команд
        self._register_handlers()
        self._register_gcode_commands()

    def _find_ace_device(self) -> Optional[str]:
        """Поиск устройства ACE по VID/PID или описанию"""
        ACE_IDS = {
            'VID:PID': [(0x0483, 0x5740)],  # Пример для STM32, замените на реальные
            'DESCRIPTION': ['ACE', 'BunnyAce']
        }
        
        for port in serial.tools.list_ports.comports():
            # Поиск по VID/PID
            if hasattr(port, 'vid') and hasattr(port, 'pid'):
                if (port.vid, port.pid) in ACE_IDS['VID:PID']:
                    return port.device
            
            # Поиск по описанию
            if any(name in (port.description or '') for name in ACE_IDS['DESCRIPTION']):
                return port.device
        
        return None

    def _get_default_info(self) -> Dict[str, Any]:
        """Возвращает дефолтное состояние устройства"""
        return {
            'status': 'disconnected',
            'dryer': {
                'status': 'stop',
                'target_temp': 0,
                'duration': 0,
                'remain_time': 0
            },
            'temp': 0,
            'enable_rfid': 1,
            'fan_speed': 7000,
            'slots': [{'index': i, 'status': 'empty', 'sku': '', 'type': '', 'color': [0, 0, 0]} 
                     for i in range(4)]
        }

    def _register_handlers(self):
        """Регистрация системных обработчиков"""
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

    def _register_gcode_commands(self):
        """Регистрация команд G-Code"""
        commands = [
            ('ACE_DEBUG', self.cmd_ACE_DEBUG, "Debug connection"),
            ('ACE_START_DRYING', self.cmd_ACE_START_DRYING, "Start drying"),
            ('ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING, "Stop drying"),
            ('ACE_STATUS', self.cmd_ACE_STATUS, "Get device status"),
        ]
        
        for name, func, desc in commands:
            self.gcode.register_command(name, func, desc=desc)

    def _connect(self):
        """Попытка подключения к устройству"""
        if self._connected:
            return True
            
        for attempt in range(self._max_connection_attempts):
            try:
                self._serial = serial.Serial(
                    port=self.serial_name,
                    baudrate=self.baud,
                    timeout=0.1,
                    write_timeout=0.1)
                
                if self._serial.isOpen():
                    self._connected = True
                    self._info['status'] = 'ready'
                    logging.info(f"Connected to ACE at {self.serial_name}")
                    
                    # Запуск таймеров для чтения/записи
                    self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                    self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)
                    return True
                    
            except SerialException as e:
                logging.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                time.sleep(1)
        
        logging.error("Failed to connect to ACE device")
        return False

    def _reconnect(self):
        """Переподключение при потере связи"""
        if self._connected:
            self._serial.close()
            self._connected = False
        
        logging.info("Attempting to reconnect...")
        if self._connect():
            self._info = self._get_default_info()
            self._info['status'] = 'ready'
            return True
        return False

    def _send_request(self, request: Dict[str, Any]):
        """Отправка запроса с CRC проверкой"""
        if not self._connected and not self._reconnect():
            raise SerialException("Device not connected")

        if 'id' not in request:
            request['id'] = self._request_id
            self._request_id += 1

        payload = json.dumps(request).encode('utf-8')
        crc = self._calc_crc(payload)
        
        packet = (
            bytes([0xFF, 0xAA]) +
            struct.pack('<H', len(payload)) +
            payload +
            struct.pack('<H', crc) +
            bytes([0xFE])
        
        try:
            self._serial.write(packet)
            self.send_time = time.time()
            return True
        except SerialException:
            logging.error("Write error, attempting reconnect")
            self._reconnect()
            return False

    # ... (остальные методы остаются аналогичными, но с улучшенной обработкой ошибок)

    def cmd_ACE_STATUS(self, gcmd):
        """Возвращает текущий статус устройства"""
        status = json.dumps(self._info, indent=2)
        gcmd.respond_info(f"ACE Status:\n{status}")

    def cmd_ACE_DEBUG(self, gcmd):
        """Тестирование подключения"""
        if self._connect():
            gcmd.respond_info(f"ACE connected at {self.serial_name}")
        else:
            gcmd.respond_info("ACE connection failed")

    def cmd_ACE_START_DRYING(self, gcmd):
        """Запуск сушки с параметрами"""
        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')")
            else:
                gcmd.respond_info(f"Drying started at {temperature}°C for {duration} minutes")

        self.send_request({
            "method": "drying",
            "params": {
                "temp": temperature,
                "fan_speed": 7000,
                "duration": duration * 60  # конвертация в секунды
            }
        }, callback)

    def cmd_ACE_STOP_DRYING(self, gcmd):
        """Остановка сушки"""
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')")
            else:
                gcmd.respond_info("Drying stopped")

        self.send_request({"method": "drying_stop"}, callback)

def load_config(config):
    return BunnyAce(config)