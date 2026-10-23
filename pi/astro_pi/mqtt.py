"""Status und letzte Aufnahme an Home Assistant (MQTT-Discovery) – ueber mosquitto_pub, ohne Python-Abhaengigkeit.

Home Assistant bekommt nur Anzeigen. Es gibt bewusst KEINE Befehle von HA an den Pi:
gestartet wird ausschliesslich in der Web-App.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil

log = logging.getLogger("astro_pi.mqtt")


class Mqtt:
    def __init__(self, host: str, port: int = 1883, user: str = "", password: str = "", topic: str = "astro_pi",
                 name: str = "Astro-Pi", url: str = ""):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.topic, self.name, self.url = topic.rstrip("/"), name, url
        self.enabled = bool(host) and shutil.which("mosquitto_pub") is not None
        if host and not self.enabled:
            log.warning("mosquitto_pub fehlt – MQTT aus (apt install mosquitto-clients)")

    async def pub(self, sub: str, payload: bytes | str, retain: bool = True, absolute: bool = False):
        if not self.enabled:
            return
        topic = sub if absolute else f"{self.topic}/{sub}"
        args = ["mosquitto_pub", "-h", self.host, "-p", str(self.port), "-t", topic, "-s"]
        if self.user:
            args += ["-u", self.user, "-P", self.password]
        if retain:
            args.append("-r")
        data = payload.encode() if isinstance(payload, str) else payload
        try:
            p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(p.communicate(data), 10)
            if p.returncode:
                log.warning("MQTT-Publish fehlgeschlagen: %s", err.decode(errors="replace").strip())
        except (OSError, asyncio.TimeoutError) as e:
            log.warning("MQTT-Publish fehlgeschlagen: %s", e)

    async def discovery(self):
        if not self.enabled:
            return
        dev = {"identifiers": ["astro_pi"], "name": self.name, "manufacturer": "seestar-astro-pipeline",
               "model": "INDI-Steuerung"}
        if self.url:
            dev["configuration_url"] = self.url
        st = f"{self.topic}/state"
        sensors = {
            "mount": ("Montierung", "{{ 'Schwenkt' if value_json.mount.slewing else 'Geparkt' if value_json.mount.parked "
                      "else 'Führt nach' if value_json.mount.tracking else ('Bereit' if value_json.mount.connected else 'Getrennt') }}",
                      "mdi:telescope", None),
            "target": ("Ziel", "{{ value_json.mount.target or '–' }}", "mdi:target", None),
            "altitude": ("Zielhöhe", "{{ value_json.mount.alt if value_json.mount.alt is not none else 'unknown' }}",
                         "mdi:angle-acute", "°"),
            "series": ("Serie", "{{ value_json.series.done }}/{{ value_json.series.count if value_json.series.count > 0 else '∞' }}"
                       "{{ ' läuft' if value_json.series.running else '' }}", "mdi:camera-burst", None),
            "hfr": ("HFR", "{{ value_json.preview.hfr | round(2) if value_json.preview.hfr is defined and value_json.preview.hfr else 'unknown' }}",
                    "mdi:blur", "px"),
            "disk": ("Speicher frei", "{{ value_json.disk_free_gb if value_json.disk_free_gb is not none else 'unknown' }}",
                     "mdi:sd", "GB"),
        }
        for key, (name, tpl, icon, unit) in sensors.items():
            cfg = {"name": name, "unique_id": f"astro_pi_{key}", "state_topic": st, "value_template": tpl,
                   "icon": icon, "device": dev, "availability_topic": f"{self.topic}/online"}
            if unit:
                cfg["unit_of_measurement"] = unit
            if key == "series":
                cfg["json_attributes_topic"] = st
                cfg["json_attributes_template"] = "{{ value_json.series | tojson }}"
            await self.pub(f"homeassistant/sensor/astro_pi_{key}/config", json.dumps(cfg), absolute=True)
        await self.pub("homeassistant/image/astro_pi_preview/config", absolute=True, payload=json.dumps({
            "name": "Letzte Aufnahme", "unique_id": "astro_pi_preview", "image_topic": f"{self.topic}/preview",
            "content_type": "image/jpeg", "device": dev, "availability_topic": f"{self.topic}/online"}))
        await self.pub("online", "online")
