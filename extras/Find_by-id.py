import os
import glob
from typing import Optional

class BunnyAce:
    # ... (остальной код класса остаётся без изменений)

    def _find_ace_device(self) -> Optional[str]:
        """Поиск устройства ACE по нескольким критериям"""
        # 1. Поиск по ID устройства (usb-ANYCUBIC_ACE_1-if*)
        ace_devices = glob.glob('/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if*')
        if ace_devices:
            # Возвращаем первый найденный девайс с разрешением симлинка
            return os.path.realpath(ace_devices[0])
        
        # 2. Поиск по VID/PID (пример для STM32)
        ACE_IDS = {
            'VID:PID': [(0x0483, 0x5740)],  # Замените на реальные VID/PID
            'DESCRIPTION': ['ANYCUBIC ACE', 'BunnyAce', 'DuckAce']
        }
        
        for port in serial.tools.list_ports.comports():
            # Поиск по VID/PID
            if hasattr(port, 'vid') and hasattr(port, 'pid'):
                if (port.vid, port.pid) in ACE_IDS['VID:PID']:
                    return port.device
            
            # Поиск по описанию
            if any(name in (port.description or '') for name in ACE_IDS['DESCRIPTION']):
                return port.device
        
        # 3. Попробуем найти по стандартному имени
        for pattern in ['/dev/ttyACM*', '/dev/ttyUSB*']:
            devices = glob.glob(pattern)
            if devices:
                return sorted(devices)[0]  # Возвращаем первый доступный
        
        return None