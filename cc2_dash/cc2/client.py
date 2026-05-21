from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

from cc2_dash.cc2.state import deep_merge, normalize_status
from cc2_dash.config import PrinterConfig


@dataclass
class Cc2Client:
    config: PrinterConfig
    connected: bool = False
    registered: bool = False
    last_message_ts: float = 0.0
    last_pong_ts: float = 0.0
    full_status: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._request_id = 0
        self._pending: dict[int, dict] = {}
        self._pending_events: dict[int, threading.Event] = {}
        self.client_id = self._make_client_id()
        self.register_request_id = f"{self.client_id}_req"
        self._mqtt = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311)
        self._mqtt.username_pw_set("elegoo", self.config.access_code)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.on_disconnect = self._on_disconnect

    def _make_client_id(self) -> str:
        now = hex(int(time.time() * 1000))[-5:]
        rnd = hex(random.randint(0, 4095))[2:]
        return ("0cli" + now + rnd)[:10]

    def _topic(self, suffix: str) -> str:
        return f"elegoo/{self.config.serial}/{suffix}"

    def start(self) -> None:
        self._stop.clear()
        self._mqtt.connect_async(self.config.host, self.config.port, 30)
        self._mqtt.loop_start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        finally:
            self.connected = False
            self.registered = False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        _ = (client, userdata, flags, properties)
        self.connected = rc == 0
        if rc != 0:
            return
        self._mqtt.subscribe(self._topic(f"{self.register_request_id}/register_response"))
        time.sleep(0.15)
        self._mqtt.publish(self._topic("api_register"), json.dumps({"client_id": self.client_id, "request_id": self.register_request_id}))

    def _on_disconnect(self, client, userdata, rc, properties=None):
        _ = (client, userdata, rc, properties)
        self.connected = False
        self.registered = False

    def _on_message(self, client, userdata, msg):
        _ = (client, userdata)
        self.last_message_ts = time.time()
        payload = {}
        try:
            payload = json.loads(msg.payload.decode(errors="ignore"))
        except Exception:
            return
        if msg.topic.endswith("register_response"):
            if payload.get("error") == "ok":
                self.registered = True
                self._mqtt.subscribe(self._topic("api_status"))
                self._mqtt.subscribe(self._topic(f"{self.client_id}/api_response"))
                self.send_command(1001, {}, wait=False)
                self.send_command(1002, {}, wait=False)
                self.send_command(2005, {}, wait=False)
            return
        if payload.get("type") == "PONG":
            self.last_pong_ts = time.time()
            return
        if payload.get("method") == 6000 and isinstance(payload.get("result"), dict):
            with self._lock:
                self.full_status = deep_merge(self.full_status, payload["result"])
        req_id = payload.get("id")
        if isinstance(req_id, int) and req_id in self._pending:
            self._pending[req_id] = payload
            self._pending_events[req_id].set()

    def send_command(self, method: int, params: dict | None = None, wait: bool = True, timeout: float = 10.0) -> dict:
        if not self.connected:
            return {"error": "mqtt_not_connected"}
        with self._lock:
            self._request_id += 1
            rid = self._request_id
        body = {"id": rid, "method": method, "params": params or {}}
        topic = self._topic(f"{self.client_id}/api_request")
        if wait:
            ev = threading.Event()
            self._pending[rid] = {"queued": True, "id": rid}
            self._pending_events[rid] = ev
        self._mqtt.publish(topic, json.dumps(body))
        if not wait:
            return {"queued": True, "id": rid}
        if ev.wait(timeout=timeout):
            out = self._pending.get(rid, {"id": rid, "error": "missing_response"})
        else:
            out = {"id": rid, "error": "timeout"}
        self._pending.pop(rid, None)
        self._pending_events.pop(rid, None)
        return out

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            normalized = normalize_status(self.full_status)
            full_status = dict(self.full_status)
        return {
            "printer_id": self.config.id,
            "connected": self.connected,
            "registered": self.registered,
            "client_id": self.client_id,
            "last_message_age_sec": (now - self.last_message_ts) if self.last_message_ts else None,
            "last_pong_age_sec": (now - self.last_pong_ts) if self.last_pong_ts else None,
            "normalized": normalized,
            "full_status": full_status,
        }
