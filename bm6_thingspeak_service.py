import asyncio
from Crypto.Cipher import AES
from bleak import BleakClient
import aiohttp
import schedule

# ThingSpeak configuration
THINGSPEAK_CHANNEL_ID = "2894310"
THINGSPEAK_API_KEY = "NVL7261EIYU8P66S"
THINGSPEAK_URL = f"https://api.thingspeak.com/update?api_key={THINGSPEAK_API_KEY}"

# BM6 module addresses
BM6_ADDRESSES = [
    "50:54:7B:24:36:A2",  # Module 1
    "50:54:7B:24:3A:1C",  # Module 2
    "50:54:7B:24:A8:C8"   # Module 3
]

# BM6 encryption key
KEY = bytearray([108, 101, 97, 103, 101, 110, 100, 255, 254, 48, 49, 48, 48, 48, 48, 57])

def decrypt(crypted):
    cipher = AES.new(KEY, AES.MODE_CBC, 16 * b'\0')
    decrypted = cipher.decrypt(crypted).hex()
    return decrypted

def encrypt(plaintext):
    cipher = AES.new(KEY, AES.MODE_CBC, 16 * b'\0')
    encrypted = cipher.encrypt(plaintext)
    return encrypted

async def get_bm6_data(address):
    bm6_data = {"voltage": None, "temperature": None, "soc": None}

    async def notification_handler(sender, data):
        message = decrypt(data)
        if message[0:6] == "d15507":
            bm6_data["voltage"] = int(message[15:18], 16) / 100
            bm6_data["soc"] = int(message[12:14], 16)
            bm6_data["temperature"] = int(message[8:10], 16) if message[6:8] != "01" else -int(message[8:10], 16)

    try:
        async with BleakClient(address, timeout=30) as client:
            await client.write_gatt_char("FFF3", encrypt(bytearray.fromhex("d1550700000000000000000000000000")), response=True)
            await client.start_notify("FFF4", notification_handler)

            # Wait for all data to be populated or timeout after a reasonable period
            for _ in range(50):  # Retry up to ~5 seconds (50 * 0.1s)
                if all(bm6_data.values()):
                    break
                await asyncio.sleep(0.1)

            await client.stop_notify("FFF4")
    except Exception as e:
        print(f"Error with device {address}: {e}")
        return None

    return bm6_data

async def send_to_thingspeak(payload):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(THINGSPEAK_URL, params=payload) as response:
                if response.status == 200:
                    print("Data sent to ThingSpeak successfully")
                else:
                    print(f"Failed to send data to ThingSpeak. Status code: {response.status}")
        except Exception as e:
            print(f"Error sending data to ThingSpeak: {str(e)}")

async def collect_and_send_data():
    all_data = []
    for i, address in enumerate(BM6_ADDRESSES, start=1):
        try:
            data = await get_bm6_data(address)
            if data:
                all_data.append(data)
                print(f"Module {i} data: {data}")
            else:
                print(f"Module {i} returned no data.")
        except Exception as e:
            print(f"Error reading Module {i}: {str(e)}")

    if all_data:
        payload = {
            "field1": all_data[0]["voltage"] if len(all_data) > 0 else None,
            "field2": all_data[1]["voltage"] if len(all_data) > 1 else None,
            "field3": all_data[2]["voltage"] if len(all_data) > 2 else None,
            "field4": all_data[0]["soc"] if len(all_data) > 0 else None,
            "field5": all_data[1]["soc"] if len(all_data) > 1 else None,
            "field6": all_data[2]["soc"] if len(all_data) > 2 else None,
            "field7": all_data[0]["temperature"] if len(all_data) > 0 else None,
            "field8": all_data[1]["temperature"] if len(all_data) > 1 else None,
        }
        
        # Remove None values from payload
        payload = {k: v for k, v in payload.items() if v is not None}
        
        await send_to_thingspeak(payload)

async def run_schedule():
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

def main():
    schedule.every(15).minutes.do(lambda: asyncio.create_task(collect_and_send_data()))
    
    print("Service started. Running every 15 minutes.")
    asyncio.run(run_schedule())

if __name__ == "__main__":
    main()
