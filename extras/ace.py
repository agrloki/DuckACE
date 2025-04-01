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
from typing import Optional, Dict, List, Callable, Any, Tuple

# Настройка логирования
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
        
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        
        self.variables = self.printer.lookup_object('save_variables').allVariables

        # Автопоиск устройства
        default_serial = self._find_ace_device()
        self.serial_name = config.get('serial', default_serial or '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)
        
        # Параметры конфигурации
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 100)
        self.park_hit_count = config.getint('park_hit_count', 5)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.disable_assist_after_toolchange = config.getboolean('disable_assist_after_toolchange', True)

        # Состояние устройства
        self._info = self._get_default_info()
        self._callback_map = {}
        self._request_id = 0
        self._connected = False
        self._connection_attempts = 0
        self._max_connection_attempts = 5
        
        # Параметры работы
        self._feed_assist_index = -1
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        
        # Очереди и потоки
        self._queue = queue.Queue()
        self._main_queue = queue.Queue()
        
        # Инициализация
        self._register_handlers()
        self._register_gcode_commands()

    def _find_ace_device(self) -> Optional[str]:
        """Поиск устройства ACE по VID/PID или описанию"""
        ACE_IDS = {
            'VID:PID': [(0x0483, 0x5740)],  # Пример для STM32, замените на реальные
            'DESCRIPTION': ['ACE', 'BunnyAce', 'DuckAce']
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
            'feed_assist_count': 0,
            'cont_assist_time': 0.0,
            'slots': [{
                'index': i,
                'status': 'empty',
                'sku': '',
                'type': '',
                'color': [0, 0, 0]
            } for i in range(4)]
        }

    def _register_handlers(self):
        """Регистрация системных обработчиков"""
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

    def _register_gcode_commands(self):
        """Регистрация всех команд G-Code"""
        commands = [
            ('ACE_DEBUG', self.cmd_ACE_DEBUG, "Debug connection"),
            ('ACE_STATUS', self.cmd_ACE_STATUS, "Get device status"),
            ('ACE_START_DRYING', self.cmd_ACE_START_DRYING, "Start drying"),
            ('ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING, "Stop drying"),
            ('ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST, "Enable feed assist"),
            ('ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST, "Disable feed assist"),
            ('ACE_PARK_TO_TOOLHEAD', self.cmd_ACE_PARK_TO_TOOLHEAD, "Park filament to toolhead"),
            ('ACE_FEED', self.cmd_ACE_FEED, "Feed filament"),
            ('ACE_RETRACT', self.cmd_ACE_RETRACT, "Retract filament"),
            ('ACE_CHANGE_TOOL', self.cmd_ACE_CHANGE_TOOL, "Change tool"),
        ]
        
        for name, func, desc in commands:
            self.gcode.register_command(name, func, desc=desc)

    def _connect(self) -> bool:
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
                    
                    # Запуск потоков для чтения/записи
                    self._writer_thread = threading.Thread(target=self._writer_loop)
                    self._writer_thread.daemon = True
                    self._writer_thread.start()

                    self._reader_thread = threading.Thread(target=self._reader_loop)
                    self._reader_thread.daemon = True
                    self._reader_thread.start()

                    self.main_timer = self.reactor.register_timer(self._main_eval, self.reactor.NOW)
                    
                    # Запрос информации об устройстве
                    def info_callback(response):
                        res = response['result']
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                    self.send_request({"method": "get_info"}, info_callback)
                    
                    return True
                    
            except SerialException as e:
                logging.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                time.sleep(1)
        
        logging.error("Failed to connect to ACE device")
        return False

    def _reconnect(self) -> bool:
        """Переподключение при потере связи"""
        if self._connected:
            self._disconnect()
        
        logging.info("Attempting to reconnect...")
        if self._connect():
            self._info = self._get_default_info()
            self._info['status'] = 'ready'
            return True
        return False

    def _disconnect(self):
        """Корректное отключение устройства"""
        if not self._connected:
            return
            
        logging.info("Disconnecting from ACE...")
        self._connected = False
        
        if hasattr(self, '_writer_thread'):
            self._writer_thread.join(timeout=1)
        
        if hasattr(self, '_reader_thread'):
            self._reader_thread.join(timeout=1)
        
        if hasattr(self, '_serial'):
            self._serial.close()
        
        if hasattr(self, 'main_timer'):
            self.reactor.unregister_timer(self.main_timer)

    def _send_request(self, request: Dict[str, Any]) -> bool:
        """Отправка запроса с CRC проверкой"""
        if not self._connected and not self._reconnect():
            raise SerialException("Device not connected")

        if 'id' not in request:
            request['id'] = self._request_id
            self._request_id += 1
            if self._request_id >= 300000:
                self._request_id = 0

        payload = json.dumps(request).encode('utf-8')
        crc = self._calc_crc(payload)
        
        packet = (
            bytes([0xFF, 0xAA]) +
            struct.pack('<H', len(payload)) +
            payload +
            struct.pack('<H', crc) +
            bytes([0xFE]))
        
        try:
            self._serial.write(packet)
            self.send_time = time.time()
            return True
        except SerialException:
            logging.error("Write error, attempting reconnect")
            self._reconnect()
            return False

    def _calc_crc(self, buffer: bytes) -> int:
        """Вычисление CRC для пакета"""
        crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= crc & 0xff
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc
    
def _reader_loop(self):
    """Цикл чтения данных от устройства"""
    buffer = bytearray()
    terminator = bytes([0xFE])
    header = bytes([0xFF, 0xAA])
    
    while self._connected:
        try:
            # Чтение доступных данных
            data = self._serial.read(4096)
            if not data:
                time.sleep(0.01)
                continue
            
            buffer.extend(data)
            
            # Поиск пакетов в буфере
            while True:
                # Ищем заголовок пакета
                start_idx = buffer.find(header)
                if start_idx < 0:
                    # Нет полного заголовка, оставляем в буфере последние 1 байт
                    if len(buffer) > 1:
                        buffer = buffer[-1:]
                    break
                
                # Удаляем мусор перед заголовком
                if start_idx > 0:
                    logging.warning(f"Dropping {start_idx} bytes before header: {buffer[:start_idx].hex()}")
                    buffer = buffer[start_idx:]
                
                # Проверяем, что есть достаточно данных для заголовка
                if len(buffer) < 4:
                    break  # Ждем больше данных
                
                # Получаем длину полезной нагрузки
                payload_len = struct.unpack('<H', buffer[2:4])[0]
                total_packet_len = 4 + payload_len + 2 + 1  # заголовок + данные + CRC + терминатор
                
                # Проверяем, есть ли полный пакет
                if len(buffer) < total_packet_len:
                    break  # Ждем больше данных
                
                # Проверяем терминатор
                if buffer[total_packet_len-1:total_packet_len] != terminator:
                    logging.warning(f"Invalid terminator, dropping packet: {buffer[:total_packet_len].hex()}")
                    buffer = buffer[total_packet_len:]
                    continue
                
                # Извлекаем полный пакет
                packet = buffer[:total_packet_len]
                buffer = buffer[total_packet_len:]
                
                # Проверяем CRC
                payload = packet[4:4+payload_len]
                crc_data = packet[4+payload_len:4+payload_len+2]
                
                if self._calc_crc(payload) != struct.unpack('<H', crc_data)[0]:
                    logging.warning(f'CRC mismatch in packet: {packet.hex()}')
                    continue
                
                try:
                    response = json.loads(payload.decode('utf-8'))
                    if 'id' in response and response['id'] in self._callback_map:
                        callback = self._callback_map.pop(response['id'])
                        callback(response)
                except Exception as e:
                    logging.error(f"Error processing packet: {e}\nPacket: {packet.hex()}")

        except SerialException:
            logging.error("Serial communication error")
            self.printer.invoke_shutdown("Lost communication with ACE")
            break
        except Exception as e:
            logging.error(f"Reader error: {traceback.format_exc()}")
            time.sleep(0.1)
            
    # def _reader_loop(self):
    #     """Цикл чтения данных от устройства"""
    #     while self._connected:
    #         try:
    #             ret = self._serial.read_until(expected=bytes([0xFE]), size=4096)
    #             if not ret:
    #                 continue

    #             if not (ret[0] == 0xFF and ret[1] == 0xAA and ret[-1] == 0xFE):
    #                 logging.warning(f'Invalid data received: {ret.hex()}')
    #                 continue
                
    #             rlen = struct.unpack('<H', ret[2:4])[0]
    #             payload = ret[4:4+rlen]
    #             crc_data = ret[4+rlen:4+rlen+2]
                
    #             if self._calc_crc(payload) != struct.unpack('<H', crc_data)[0]:
    #                 logging.warning(f'CRC mismatch in packet: {ret.hex()}')
    #                 continue

    #             response = json.loads(payload.decode('utf-8'))
    #             if 'id' in response and response['id'] in self._callback_map:
    #                 callback = self._callback_map.pop(response['id'])
    #                 callback(response)

    #         except SerialException:
    #             logging.error("Serial communication error")
    #             self.printer.invoke_shutdown("Lost communication with ACE")
    #             break
    #         except Exception as e:
    #             logging.error(f"Reader error: {traceback.format_exc()}")
    #             time.sleep(0.1)

    def _writer_loop(self):
        """Цикл отправки данных на устройство"""
        while self._connected:
            try:
                if not self._queue.empty():
                    task = self._queue.get_nowait()
                    if task:
                        request, callback = task
                        self._callback_map[request['id']] = callback
                        self._send_request(request)
                else:
                    # Периодический запрос статуса
                    def status_callback(response):
                        if 'result' in response:
                            self._info = response['result']
                    
                    self.send_request({
                        "id": self._request_id,
                        "method": "get_status"
                    }, status_callback)
                    
                    # Обработка парковки
                    if self._park_in_progress:
                        time.sleep(0.68)
                    else:
                        time.sleep(0.25)

            except SerialException:
                logging.error("Serial communication error")
                self.printer.invoke_shutdown("Lost communication with ACE")
                break
            except Exception as e:
                logging.error(f"Writer error: {traceback.format_exc()}")
                time.sleep(0.1)

    def _main_eval(self, eventtime):
        """Обработка задач в основном потоке"""
        while not self._main_queue.empty():
            task = self._main_queue.get_nowait()
            if task:
                task()
        return eventtime + 0.25

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        if not self._connect():
            logging.error("Failed to connect to ACE on startup")
            return

    def _handle_disconnect(self):
        """Обработчик отключения Klipper"""
        self._disconnect()

    def send_request(self, request: Dict[str, Any], callback: Callable):
        """Добавление запроса в очередь"""
        if not self._connected and not self._reconnect():
            raise SerialException("Device not connected")
        
        if 'id' not in request:
            request['id'] = self._request_id
            self._request_id += 1
        
        self._queue.put((request, callback))

    def dwell(self, delay: float = 1.0, on_main: bool = False):
        """Пауза с возможностью выполнения в основном потоке"""
        toolhead = self.printer.lookup_object('toolhead')
        def main_callback():
            toolhead.dwell(delay)
        
        if on_main:
            self._main_queue.put(main_callback)
        else:
            main_callback()

    # ==================== G-CODE COMMANDS ====================

    cmd_ACE_STATUS_help = "Get current device status"
    def cmd_ACE_STATUS(self, gcmd):
        """Обработчик команды ACE_STATUS"""
        status = json.dumps(self._info, indent=2)
        gcmd.respond_info(f"ACE Status:\n{status}")

    cmd_ACE_DEBUG_help = "Debug ACE connection"
    def cmd_ACE_DEBUG(self, gcmd):
        """Обработчик команды ACE_DEBUG"""
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')
        
        try:
            def callback(response):
                gcmd.respond_info(json.dumps(response, indent=2))
            
            self.send_request({
                "method": method,
                "params": json.loads(params)
            }, callback)
        except Exception as e:
            gcmd.respond_error(f"Error: {str(e)}")

    cmd_ACE_START_DRYING_help = "Start filament drying"
    def cmd_ACE_START_DRYING(self, gcmd):
        """Обработчик команды ACE_START_DRYING"""
        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info(f"Drying started at {temperature}°C for {duration} minutes")

        self.send_request({
            "method": "drying",
            "params": {
                "temp": temperature,
                "fan_speed": 7000,
                "duration": duration * 60
            }
        }, callback)

    cmd_ACE_STOP_DRYING_help = "Stop filament drying"
    def cmd_ACE_STOP_DRYING(self, gcmd):
        """Обработчик команды ACE_STOP_DRYING"""
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Drying stopped")

        self.send_request({"method": "drying_stop"}, callback)

    cmd_ACE_ENABLE_FEED_ASSIST_help = "Enable feed assist"
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_ENABLE_FEED_ASSIST"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_info(f"Feed assist enabled for slot {index}")
                self.dwell(0.3)

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)

    cmd_ACE_DISABLE_FEED_ASSIST_help = "Disable feed assist"
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_DISABLE_FEED_ASSIST"""
        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_info(f"Feed assist disabled for slot {index}")
                self.dwell(0.3)

        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": index}
        }, callback)

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        def callback(response):
            if response.get('code', 0) != 0:
                raise ValueError(f"ACE Error: {response.get('msg', 'Unknown error')}")
            
            self._assist_hit_count = 0
            self._last_assist_count = 0
            self._park_in_progress = True
            self._park_index = index
            self.dwell(0.3)

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)

    cmd_ACE_PARK_TO_TOOLHEAD_help = "Park filament to toolhead"
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        """Обработчик команды ACE_PARK_TO_TOOLHEAD"""
        if self._park_in_progress:
            gcmd.respond_error("Already parking to toolhead")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        if self._info['slots'][index]['status'] != 'ready':
            gcmd.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={index}")
            return

        self._park_to_toolhead(index)

    cmd_ACE_FEED_help = "Feed filament"
    def cmd_ACE_FEED(self, gcmd):
        """Обработчик команды ACE_FEED"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "feed_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        self.dwell((length / speed) + 0.1)

    cmd_ACE_RETRACT_help = "Retract filament"
    def cmd_ACE_RETRACT(self, gcmd):
        """Обработчик команды ACE_RETRACT"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "unwind_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        self.dwell((length / speed) + 0.1)

    cmd_ACE_CHANGE_TOOL_help = "Change tool"
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        """Обработчик команды ACE_CHANGE_TOOL"""
        tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
        was = self.variables.get('ace_current_index', -1)
        
        if was == tool:
            gcmd.respond_info(f"Tool already set to {tool}")
            return
        
        if tool != -1 and self._info['slots'][tool]['status'] != 'ready':
            gcmd.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={tool}")
            return

        gcmd.run_script_from_command(f"_ACE_PRE_TOOLCHANGE FROM={was} TO={tool}")
        self._park_is_toolchange = True
        self._park_previous_tool = was
        self.variables['ace_current_index'] = tool
        gcmd.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")

        if was != -1:
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": was,
                    "length": self.toolchange_retract_length,
                    "speed": self.retract_speed
                }
            }, callback)
            self.dwell((self.toolchange_retract_length / self.retract_speed) + 0.1)

            while self._info['status'] != 'ready':
                self.dwell(1.0)
            
            self.dwell(0.25)

            if tool != -1:
                gcmd.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
            else:
                gcmd.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
        else:
            self._park_to_toolhead(tool)

def load_config(config):
    return BunnyAce(config)