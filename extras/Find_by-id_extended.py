def _find_ace_device(self) -> Optional[str]:
    """Поиск устройства ACE по нескольким критериям с учётом вариаций ID"""
    # 1. Поиск по шаблону ID устройства (usb-ANYCUBIC_ACE_1-if*)
    ace_devices = glob.glob('/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if*')
    if ace_devices:
        # Сортируем найденные устройства и берём первый (если их несколько)
        ace_devices.sort()
        logging.info(f"Found ACE devices by ID: {ace_devices}")
        return os.path.realpath(ace_devices[0])
    
    # 2. Альтернативный поиск по имени производителя
    ace_devices = glob.glob('/dev/serial/by-id/usb-ANYCUBIC*')
    if ace_devices:
        ace_devices.sort()
        logging.info(f"Found ANYCUBIC devices: {ace_devices}")
        return os.path.realpath(ace_devices[0])
    
    # 3. Поиск по VID/PID (если известны)
    ACE_IDS = {
        'VID:PID': [
            (0x0483, 0x5740),  # STM32 (пример)
            (0x1a86, 0x7523)    # CH340 (ещё пример)
        ],
        'DESCRIPTION': ['ANYCUBIC ACE', 'BunnyAce', 'DuckAce']
    }
    
    for port in serial.tools.list_ports.comports():
        # Поиск по VID/PID
        if hasattr(port, 'vid') and hasattr(port, 'pid'):
            if (port.vid, port.pid) in ACE_IDS['VID:PID']:
                logging.info(f"Found device by VID/PID: {port.device}")
                return port.device
        
        # Поиск по описанию
        port_description = (port.description or '').upper()
        if any(name.upper() in port_description for name in ACE_IDS['DESCRIPTION']):
            logging.info(f"Found device by description: {port.device}")
            return port.device
    
    # 4. Резервный поиск по стандартным путям
    for pattern in ['/dev/ttyACM*', '/dev/ttyUSB*']:
        devices = sorted(glob.glob(pattern))
        if devices:
            logging.info(f"Found potential device by path: {devices[0]}")
            return devices[0]
    
    logging.warning("No ACE device found!")
    return None