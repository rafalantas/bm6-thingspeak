import asyncio
from Crypto.Cipher import AES
from bleak import BleakClient
import aiohttp
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

THINGSPEAK_API_KEY = "NVL7261EIYU8P66S"
THINGSPEAK_URL = f"https://api.thingspeak.com/update?api_key={THINGSPEAK_API_KEY}"

BM6_ADDRESSES = [
    "50:54:7B:24:36:A2",
    "50:54:7B:24:3A:1C",
    "50:54:7B:24:A8:C8",
    "3C:AB:72:EA:BD:A2"
]

KEY = bytearray([108, 101, 97, 103, 101, 110, 100, 255, 254, 48, 49, 48, 48, 48, 48, 57])
INTERVAL_SECONDS = 15 * 60
BLE_TIMEOUT = 45   # timeout na całe połączenie BLE
HTTP_TIMEOUT = 20

_running_task = None  # blokada przed nakładaniem się tasków

def decrypt(crypted):
    cipher = AES.new(KEY, AES.MODE_CBC, 16 * b'\0')
    return cipher.decrypt(crypted).hex()

def encrypt(plaintext):
    cipher = AES.new(KEY, AES.MODE_CBC, 16 * b'\0')
    return cipher.encrypt(plaintext)

async def get_bm6_data(address):
    bm6_data = {"voltage": None, "soc": None}

    def notification_handler(sender, data):
        message = decrypt(data)
        if message[0:6] == "d15507":
            try:
                bm6_data["voltage"] = int(message[15:18], 16) / 100
            except Exception:
                pass
            try:
                bm6_data["soc"] = int(message[12:14], 16)
            except Exception:
                pass

    try:
        async with asyncio.timeout(BLE_TIMEOUT):
            async with BleakClient(address, timeout=30) as client:
                await client.write_gatt_char(
                    "FFF3",
                    encrypt(bytearray.fromhex("d1550700000000000000000000000000")),
                    response=True
                )
                await client.start_notify("FFF4", notification_handler)
                for _ in range(50):
                    if bm6_data["voltage"] is not None and bm6_data["soc"] is not None:
                        break
                    await asyncio.sleep(0.1)
                await client.stop_notify("FFF4")
    except asyncio.TimeoutError:
        logging.warning(f"Timeout BLE dla {address}")
        return None
    except Exception as e:
        logging.error(f"Błąd BLE {address}: {e}")
        return None

    return bm6_data

async def send_to_thingspeak(payload):
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(THINGSPEAK_URL, params=payload) as response:
                if response.status == 200:
                    logging.info("Dane wysłane do ThingSpeak")
                else:
                    logging.warning(f"ThingSpeak zwrócił status {response.status}")
    except Exception as e:
        logging.error(f"Błąd HTTP: {e}")

async def collect_and_send_data():
    global _running_task
    if _running_task is not None and not _running_task.done():
        logging.warning("Poprzedni cykl jeszcze trwa – pomijam tę iterację")
        return
    _running_task = asyncio.current_task()

    logging.info("Rozpoczynam odczyt modułów BM6...")
    all_data = []
    for i, address in enumerate(BM6_ADDRESSES, start=1):
        data = await get_bm6_data(address)
        if data:
            logging.info(f"Moduł {i}: {data}")
            all_data.append(data)
        else:
            logging.warning(f"Moduł {i}: brak danych")
        await asyncio.sleep(1)  # chwila przerwy między połączeniami BLE

    if all_data:
        payload = {}
        for i, d in enumerate(all_data[:4], start=1):
            if d["voltage"] is not None:
                payload[f"field{i}"] = d["voltage"]
            if d["soc"] is not None:
                payload[f"field{i+4}"] = d["soc"]
        await send_to_thingspeak(payload)

async def main():
    logging.info("Serwis BM6→ThingSpeak uruchomiony (interwał: 15 min)")
    while True:
        try:
            await asyncio.wait_for(collect_and_send_data(), timeout=BLE_TIMEOUT * 4 + HTTP_TIMEOUT)
        except asyncio.TimeoutError:
            logging.error("Cały cykl przekroczył limit czasu – resetuję pętlę")
        except Exception as e:
            logging.error(f"Nieoczekiwany błąd w pętli głównej: {e}")
        await asyncio.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(main())
