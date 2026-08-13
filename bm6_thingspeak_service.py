# -*- coding: utf-8 -*-
import asyncio
from Crypto.Cipher import AES
from bleak import BleakClient, BleakScanner
import aiohttp
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

# ThingSpeak configuration
THINGSPEAK_API_KEY = "NVL7261EIYU8P66S"
THINGSPEAK_URL = f"https://api.thingspeak.com/update?api_key={THINGSPEAK_API_KEY}"

# BM6 module addresses
BM6_ADDRESSES = [
    "50:54:7B:24:36:A2",  # Module 1
    "50:54:7B:24:3A:1C",  # Module 2
    "50:54:7B:24:A8:C8",  # Module 3
    "3C:AB:72:EA:BD:A2"   # Module 4
]

# BM6 encryption key
KEY = bytearray([108, 101, 97, 103, 101, 110, 100, 255, 254, 48, 49, 48, 48, 48, 48, 57])

INTERVAL_SECONDS = 15 * 60  # every 15 minutes
BLE_TIMEOUT = 45             # timeout for single BLE connection
HTTP_TIMEOUT = 20            # timeout for ThingSpeak upload
SCAN_TIMEOUT = 10            # timeout for BLE scan
_running_task = None

# -- Encryption ----------------------------------------------------------------

def decrypt(crypted):
    cipher = AES.new(KEY, AES.MODE_CBC, 16 * b'\0')
    return cipher.decrypt(crypted).hex()

def encrypt(plaintext):
    cipher = AES.new(KEY, AES.MODE_CBC, 16 * b'\0')
    return cipher.encrypt(plaintext)


# -- BLE -----------------------------------------------------------------------

async def is_device_in_range(address):
    """Check if BLE device is visible before attempting connection.
    Prevents phantom connections in BlueZ when device is out of range."""
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=SCAN_TIMEOUT)
        return device is not None
    except Exception as e:
        logging.warning(f"Scan error {address}: {e}")
        return False


async def get_bm6_data(address):
    """Read voltage and SoC from BM6 module.
    Returns dict with voltage and soc, or None on failure."""
    bm6_data = {"voltage": None, "soc": None}

    if not await is_device_in_range(address):
        logging.info(f"Device {address} out of range, skipping")
        return None

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

                for _ in range(50):  # wait max ~5 seconds for data
                    if bm6_data["voltage"] is not None and bm6_data["soc"] is not None:
                        break
                    await asyncio.sleep(0.1)

                await client.stop_notify("FFF4")

        return bm6_data

    except asyncio.TimeoutError:
        logging.warning(f"BLE timeout for {address}")
    except Exception as e:
        logging.error(f"BLE error {address}: {e}")

    return None


# -- ThingSpeak ----------------------------------------------------------------

async def send_to_thingspeak(payload):
    """Send data to ThingSpeak via HTTP GET."""
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(THINGSPEAK_URL, params=payload) as response:
                if response.status == 200:
                    logging.info(f"Data sent to ThingSpeak: {payload}")
                else:
                    logging.warning(f"ThingSpeak returned status {response.status}")
    except Exception as e:
        logging.error(f"ThingSpeak send error: {e}")


# -- Main logic ----------------------------------------------------------------

async def collect_and_send_data():
    global _running_task
    if _running_task is not None and not _running_task.done():
        logging.warning("Previous cycle still running – skipping this iteration")
        return
    _running_task = asyncio.current_task()

    logging.info("Starting BM6 module readout...")
    all_data = []
    for i, address in enumerate(BM6_ADDRESSES, start=1):
        data = await get_bm6_data(address)
        if data:
            logging.info(f"Module {i} ({address}): {data}")
        else:
            logging.warning(f"Module {i} ({address}): no data")
        all_data.append(data)  # None jeśli brak — pozycja zachowana!
        await asyncio.sleep(1)

    if any(d is not None for d in all_data):
        payload = {}
        for i, d in enumerate(all_data[:4], start=1):
            if d is not None:
                if d["voltage"] is not None:
                    payload[f"field{i}"] = d["voltage"]
                if d["soc"] is not None:
                    payload[f"field{i+4}"] = d["soc"]
        await send_to_thingspeak(payload)


async def main():
    logging.info("BM6 -> ThingSpeak service started (interval: 15 min)")

    while True:
        try:
            cycle_timeout = BLE_TIMEOUT * len(BM6_ADDRESSES) + HTTP_TIMEOUT
            await asyncio.wait_for(collect_and_send_data(), timeout=cycle_timeout)
        except asyncio.TimeoutError:
            logging.error("Full cycle exceeded time limit, waiting for next run")
        except Exception as e:
            logging.error(f"Unexpected error in main loop: {e}")

        await asyncio.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
