# BM6 to ThingSpeak service

This service reads BM6 battery monitors over Bluetooth Low Energy and writes
their measurements to a ThingSpeak channel every 15 minutes.

## Configure the ThingSpeak write key

Keep the write key out of this repository. On the Raspberry Pi, create
`/etc/default/bm6-thingspeak` with the following line, using the channel's
current **write** API key:

```sh
THINGSPEAK_API_KEY=replace-with-your-write-key
```

Restrict the file to the service administrator:

```sh
sudo chown root:root /etc/default/bm6-thingspeak
sudo chmod 600 /etc/default/bm6-thingspeak
```

The systemd unit loads this file when it starts. After changing the key, reload
and restart the service:

```sh
sudo systemctl daemon-reload
sudo systemctl restart bm6_thingspeak
```

The service treats a successful HTTP status as insufficient by itself:
ThingSpeak's update endpoint must also return a positive entry ID. A response
of `0` or a non-numeric response is logged as a rejected update.

## Diagnose and recover from hangs

The service sends systemd watchdog notifications at BLE scan, readout, and
upload progress points. If the Python process stops making progress for 90
seconds, systemd terminates and restarts the service. This recovers a hung
service process; it cannot recover a Raspberry Pi whose kernel or power supply
has stopped responding.

After installing the updated unit, apply it with:

```sh
sudo systemctl daemon-reload
sudo systemctl restart bm6_thingspeak
```

When the problem happens again, collect the previous boot's logs and system
health before unplugging the Pi, if SSH or a local terminal still works. The
previous-boot journal is available only if journald is configured to keep logs
across boots:

```sh
sudo journalctl -b -1 -k --no-pager > ~/kernel-previous-boot.log
sudo journalctl -b -1 -u bm6_thingspeak --no-pager > ~/service-previous-boot.log
last -x | head -30
sudo vcgencmd get_throttled
free -h
df -h
```

If the service log shows watchdog restarts while the Pi remains reachable, the
hang is in the service/Bluetooth path. If SSH and local console both stop
responding, inspect the kernel log, undervoltage/throttling status, power
supply, and SD card; a service watchdog cannot prove or fix a whole-system
freeze. The Pi's hardware watchdog is a separate system-level recovery option
and should only be enabled after confirming that this Pi OS image exposes and
supports `/dev/watchdog`.
