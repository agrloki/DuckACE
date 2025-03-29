import serial, threading, time, logging, json, struct, queue, traceback

class DuckAce:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        self.variables = self.printer.lookup_object('save_variables').allVariables
        
        self.serial_name = config.get('serial', '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 100)
        self.park_hit_count = config.getint('park_hit_count', 5)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.disable_assist_after_toolchange = config.getboolean('disable_assist_after_toolchange', True)

        self._callback_map = {}
        self._feed_assist_index = -1
        self._request_id = 0
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1

        # Default data to prevent exceptions
        self._info = {
            'status': 'ready',
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
            'slots': [
                {
                    'index': 0,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 1,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 2,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 3,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                }
            ]
        }

        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

        self.gcode.register_command(
            'ACE_START_DRYING', self.cmd_ACE_START_DRYING,
            desc=self.cmd_ACE_START_DRYING_help)
        self.gcode.register_command(
            'ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING,
            desc=self.cmd_ACE_STOP_DRYING_help)
        self.gcode.register_command(
            'ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST,
            desc=self.cmd_ACE_ENABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST,
            desc=self.cmd_ACE_DISABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_PARK_TO_TOOLHEAD', self.cmd_ACE_PARK_TO_TOOLHEAD,
            desc=self.cmd_ACE_PARK_TO_TOOLHEAD_help)
        self.gcode.register_command(
            'ACE_FEED', self.cmd_ACE_FEED,
            desc=self.cmd_ACE_FEED_help)
        self.gcode.register_command(
            'ACE_RETRACT', self.cmd_ACE_RETRACT,
            desc=self.cmd_ACE_RETRACT_help)
        self.gcode.register_command(
            'ACE_CHANGE_TOOL', self.cmd_ACE_CHANGE_TOOL,
            desc=self.cmd_ACE_CHANGE_TOOL_help)
        self.gcode.register_command(
            'ACE_DEBUG', self.cmd_ACE_DEBUG,
            desc=self.cmd_ACE_DEBUG_help)


    def _calc_crc(self, buffer):
        _crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= _crc & 0xff
            data ^= (data & 0x0f) << 4
            _crc = ((data << 8) | (_crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return _crc


    def _send_request(self, request):
        if not 'id' in request:
            request['id'] = self._request_id
            self._request_id += 1
            if self._request_id >= 300000:
                self._request_id = 0

        payload = json.dumps(request)
        payload = bytes(payload, 'utf-8')
        
        data = bytes([0xFF, 0xAA])
        data += struct.pack('@H', len(payload))
        data += payload
        data += struct.pack('@H', self._calc_crc(payload))
        data += bytes([0xFE])
        
        self._serial.write(data)


    def _main_eval(self, eventtime):
        while not self._main_queue.empty():
            task = self._main_queue.get_nowait()
            if task is not None:
                task()
        
        return eventtime + 0.25


    def _reader(self):
        while self._connected:
            try:
                ret = self._serial.read_until(expected=bytes([0xFE]), size=4096)

                if not (ret[0] == 0xFF and ret[1] == 0xAA and ret[len(ret) - 1] == 0xFE):
                    logging.warning('ACE: Invalid data recieved: ' + str(ret))
                    continue
                
                rlen = struct.unpack('@H', ret[2:4])[0]
                crc_data = None
                crc_offset = 0
                if rlen > len(ret) - 7 or rlen <= 0:
                    if rlen == len(ret) - 6:
                        crc_data = self._serial.read_until(expected=bytes([0xFE]), size=1)
                        crc_data = bytes([ret[len(ret) - 2], crc_data[0]])
                        ret = ret[0:len(ret) - 2] + bytes([ret[len(ret) - 1]])
                        crc_offset = 2
                    elif rlen == len(ret) - 5:
                        crc_data = self._serial.read_until(expected=bytes([0xFE]), size=2)
                        crc_data = bytes([crc_data[1], crc_data[0]])
                        crc_offset = 2
                    else:
                        logging.info('ACE: Invalid data length recieved: ' + str(rlen) + ' | ' + str(len(ret)) + ', ' + str(ret))
                        continue
                
                if crc_data is None:
                    crc_data = ret[len(ret) - 3:len(ret) - 1]

                rpayload = ret[4:(len(ret) - 3 + crc_offset)]
                crc = struct.pack('@H', self._calc_crc(rpayload))
                if crc[0] != crc_data[0] or crc[1] != crc_data[1]:
                    logging.info('ACE: Invalid data CRC recieved: ' + str(ret) + ', should be: ' + str(crc))
                    continue

                ret = json.loads(rpayload.decode('utf-8'))
                id = ret['id']
                if id in self._callback_map:
                    callback = self._callback_map.pop(id)
                    callback(self = self, response = ret)
            except serial.serialutil.SerialException:
                self._printer.invoke_shutdown("Lost communication with ACE '%s'" % (self._name,))
                return
            except Exception as e:
                logging.info('ACE: Read error ' + traceback.format_exc(e))
            

    def _writer(self):
        while self._connected:
            try:
                while not self._queue.empty():
                    task = self._queue.get()
                    if task is not None:
                        id = self._request_id
                        self._request_id += 1
                        self._callback_map[id] = task[1]
                        task[0]['id'] = id

                        self._send_request(task[0])

                def callback(self, response):
                    if response is not None:
                        self._info = response['result']
                        # logging.info('ACE: Update status ' + str(self._request_id))
                        
                        if self._park_in_progress and self._info['status'] == 'ready':
                            new_assist_count = self._info['feed_assist_count']
                            if new_assist_count > self._last_assist_count:
                                self._last_assist_count = new_assist_count
                                self.dwell(0.7, True) # 0.68 + small room 0.02 for response
                                self._assist_hit_count = 0
                            elif self._assist_hit_count < self.park_hit_count:
                                self._assist_hit_count += 1
                                self.dwell(0.7, True)
                            else:
                                self._assist_hit_count = 0
                                self._park_in_progress = False
                                logging.info('ACE: Parked to toolhead with assist count: ' + str(self._last_assist_count))

                                if self._park_is_toolchange:
                                    self._park_is_toolchange = False
                                    def main_callback():
                                        self.gcode.run_script_from_command('_ACE_POST_TOOLCHANGE FROM=' + str(self._park_previous_tool) + ' TO=' + str(self._park_index))
                                    self._main_queue.put(main_callback)
                                    if self.disable_assist_after_toolchange:
                                        self._send_request({"method": "stop_feed_assist", "params": {"index": self._park_index}})
                                else:
                                    self._send_request({"method": "stop_feed_assist", "params": {"index": self._park_index}})

                id = self._request_id
                self._request_id += 1
                self._callback_map[id] = callback

                self._send_request({"id": id, "method": "get_status"})
                if self._park_in_progress:
                    time.sleep(0.68)
                else:
                    time.sleep(0.25)
            except serial.serialutil.SerialException:
                self._printer.invoke_shutdown("Lost communication with ACE '%s'" % (self._name,))
                return
            except Exception as e:
                logging.info('ACE: Write error ' + str(e))


    def _handle_ready(self):
        logging.info('ACE: Connecting to ' + self.serial_name)

        # We? can catch timing where ACE reboots itself when no data is available from host. We're avoiding it with this hack
        self._connected = False
        for i in range(0, 10):
            try:
                self._serial = serial.Serial(
                    port          = self.serial_name,
                    baudrate      = self.baud)
                
                if self._serial.isOpen():
                    self._connected = True
                    break
            except serial.serialutil.SerialException:
                time.sleep(0.5)
                continue
        
        if not self._connected:
            raise ValueError('ACE: Failed to connect to ' + self.serial_name)

        logging.info('ACE: Connected to ' + self.serial_name)

        self._queue = queue.Queue()
        self._main_queue = queue.Queue()
        
        self._writer_thread = threading.Thread(target=self._writer)
        self._writer_thread.setDaemon(True)
        self._writer_thread.start()

        self._reader_thread = threading.Thread(target=self._reader)
        self._reader_thread.setDaemon(True)
        self._reader_thread.start()

        self.main_timer = self.reactor.register_timer(self._main_eval, self.reactor.NOW)

        def info_callback(self, response):
            res = response['result']
            self.gcode.respond_info('Connected ' + res['model'] + ' ' + res['firmware'])
        self.send_request(request = {"method": "get_info"}, callback = info_callback)


    def _handle_disconnect(self):
        logging.info('ACE: Closing connection to ' + self.serial_name)
        self._serial.close()
        self._connected = False
        self._reader_thread.join()
        self._writer_thread.join()
        self.reactor.unregister_timer(self.main_timer)

        self._queue = None
        self._main_queue = None


    def send_request(self, request, callback):
        self._queue.put([request, callback])

    
    def dwell(self, delay = 1., on_main = False):
        toolhead = self.printer.lookup_object('toolhead')
        def main_callback():
            toolhead.dwell(delay)
        
        if on_main:
            self._main_queue.put(main_callback)
        else:
            main_callback()
    

    cmd_ACE_START_DRYING_help = 'Starts ACE Pro dryer'
    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMPERATURE')
        duration = gcmd.get_int('DURATION', 240)

        if duration <= 0:
            raise gcmd.error('Wrong duration')
        if temperature <= 0 or temperature > self.max_dryer_temperature:
            raise gcmd.error('Wrong temperature')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
            
            self.gcode.respond_info('Started ACE drying')
        
        self.send_request(request = {"method": "drying", "params": {"temp":temperature, "fan_speed": 7000, "duration": duration}}, callback = callback)


    cmd_ACE_STOP_DRYING_help = 'Stops ACE Pro dryer'
    def cmd_ACE_STOP_DRYING(self, gcmd):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
            
            self.gcode.respond_info('Stopped ACE drying')
        
        self.send_request(request = {"method":"drying_stop"}, callback = callback)


    cmd_ACE_ENABLE_FEED_ASSIST_help = 'Enables ACE feed assist'
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
            
            self._feed_assist_index = index
            self.gcode.respond_info('Enabled ACE feed assist')
        
        self.send_request(request = {"method": "start_feed_assist", "params": {"index": index}}, callback = callback)
        self.dwell(delay = 0.3)


    cmd_ACE_DISABLE_FEED_ASSIST_help = 'Disables ACE feed assist'
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        if self._feed_assist_index != -1:
            index = gcmd.get_int('INDEX', self._feed_assist_index)
        else:
            index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
            
            self._feed_assist_index = -1
            self.gcode.respond_info('Disabled ACE feed assist')
        
        self.send_request(request = {"method": "stop_feed_assist", "params": {"index": index}}, callback = callback)
        self.dwell(0.3)


    def _park_to_toolhead(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE Error: " + response['msg'])
            
            self._assist_hit_count = 0
            self._last_assist_count = 0
            self._park_in_progress = True
            self._park_index = index
        
        self.send_request(request = {"method": "start_feed_assist", "params": {"index": index}}, callback = callback)
        self.dwell(delay = 0.3)


    cmd_ACE_PARK_TO_TOOLHEAD_help = 'Parks filament from ACE to the toolhead'
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        index = gcmd.get_int('INDEX')

        if self._park_in_progress:
            raise gcmd.error('Already parking to the toolhead')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        
        status = self._info['slots'][index]['status']
        if status != 'ready':
            self.gcode.run_script_from_command('_ACE_ON_EMPTY_ERROR INDEX=' + str(index))
            return

        self._park_to_toolhead(index)


    cmd_ACE_FEED_help = 'Feeds filament from ACE'
    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.feed_speed)

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
        
        self.send_request(request = {"method": "feed_filament", "params": {"index": index, "length": length, "speed": speed}}, callback = callback)
        self.dwell(delay = (length / speed) + 0.1)


    cmd_ACE_RETRACT_help = 'Retracts filament back to ACE'
    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.retract_speed)

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
        
        self.send_request(request = {"method": "unwind_filament", "params": {"index": index, "length": length, "speed": speed}}, callback = callback)
        self.dwell(delay = (length / speed) + 0.1)


    cmd_ACE_CHANGE_TOOL_help = 'Changes tool'
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL')

        if tool < -1 or tool >= 4:
            raise gcmd.error('Wrong tool')

        was = self.variables.get('ace_current_index', -1)
        if was == tool:
            logging.info('ACE: Not changing tool, current index already ' + str(tool))
            return
        
        if tool != -1:
            status = self._info['slots'][tool]['status']
            if status != 'ready':
                self.gcode.run_script_from_command('_ACE_ON_EMPTY_ERROR INDEX=' + str(tool))
                return
        
        self.gcode.run_script_from_command('_ACE_PRE_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        self._park_is_toolchange = True
        self._park_previous_tool = was
        self.variables['ace_current_index'] = tool
        # Force save to disk
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(tool))

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE Error: " + response['msg'])
        
        logging.info('ACE: Toolchange ' + str(was) + ' => ' + str(tool))
        if was != -1:
            self.send_request(request = {"method": "unwind_filament", "params": {"index": was, "length": self.toolchange_retract_length, "speed": self.retract_speed}}, callback = callback)
            self.dwell(delay = (self.toolchange_retract_length / self.retract_speed) + 0.1)

            while self._info['status'] != 'ready':
                self.dwell(delay = 1.0)
            
            self.dwell(delay = 0.25)

            if tool != -1:
                self.gcode.run_script_from_command('ACE_PARK_TO_TOOLHEAD INDEX=' + str(tool))
            else:
                self.gcode.run_script_from_command('_ACE_POST_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        else:
            self._park_to_toolhead(tool)


    cmd_ACE_DEBUG_help = 'ACE Debug'
    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')

        try:
            def callback(self, response):
                self.gcode.respond_info(str(response))

            self.send_request(request = {"method": method, "params": json.loads(params)}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))


def load_config(config):
    return DuckAce(config)
