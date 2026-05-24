from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt

from cc2_dash.config import PrinterConfig
from cc2_dash.cc2.state import deep_merge, normalize_status

LOG = logging.getLogger("cc2_dash.cc2.client")


class CommandError(RuntimeError):
    pass


class PendingRequest:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None


def make_client_id() -> str:
    # Web-interface-compatible format: 0cli + last 5 hex millis + random hex, 10 chars.
    timestamp_hex = format(int(time.time() * 1000), "x")[-5:]
    random_hex = format(random.randint(0, 4095), "x")
    return f"0cli{timestamp_hex}{random_hex}"[:10]


def make_register_request_id(client_id: str) -> str:
    # The official SDK style is simple and works well: <client_id>_req.
    return f"{client_id}_req"


def new_mqtt_client(client_id: str) -> mqtt.Client:
    # Paho 2.x and 1.x have different constructor/callback APIs. This keeps both happy.
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311)  # type: ignore[attr-defined]
    except Exception:
        return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)  # type: ignore[call-arg]


class Cc2Client:
    def __init__(self, cfg: PrinterConfig, on_update: Optional[Callable[[str], None]] = None) -> None:
        self.cfg = cfg
        self.on_update = on_update
        self.client_id = make_client_id()
        self.register_request_id = make_register_request_id(self.client_id)
        self.client: Optional[mqtt.Client] = None
        self.thread: Optional[threading.Thread] = None
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.pending: Dict[int, PendingRequest] = {}
        self.next_request_id = random.randint(1000, 99999)

        self.connected = False
        self.registered = False
        self.registration_error: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_pong = 0.0
        self.last_message = 0.0
        self.last_status_id: Optional[int] = None
        self.missed_status_count = 0
        self.full_status: Dict[str, Any] = {}
        self.attributes: Dict[str, Any] = {}

    @property
    def request_topic(self) -> str:
        return f"elegoo/{self.cfg.serial}/{self.client_id}/api_request"

    @property
    def response_topic(self) -> str:
        return f"elegoo/{self.cfg.serial}/{self.client_id}/api_response"

    @property
    def status_topic(self) -> str:
        return f"elegoo/{self.cfg.serial}/api_status"

    @property
    def register_request_topic(self) -> str:
        return f"elegoo/{self.cfg.serial}/api_register"

    @property
    def register_response_topic(self) -> str:
        return f"elegoo/{self.cfg.serial}/{self.register_request_id}/register_response"

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name=f"cc2-{self.cfg.id}", daemon=True)
        self.thread.start()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name=f"cc2-heartbeat-{self.cfg.id}", daemon=True)
        self.heartbeat_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        client = self.client
        if client:
            try:
                client.disconnect()
            except Exception:
                pass

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.client_id = make_client_id()
            self.register_request_id = make_register_request_id(self.client_id)
            with self.lock:
                self.connected = False
                self.registered = False
                self.registration_error = None
                self.last_error = None
                self.last_pong = 0.0
                self.last_status_id = None
                self.missed_status_count = 0
                self.pending.clear()

            client = new_mqtt_client(self.client_id)
            self.client = client
            client.username_pw_set("elegoo", self.cfg.access_code or "")
            client.on_connect = self._on_connect  # type: ignore[assignment]
            client.on_message = self._on_message  # type: ignore[assignment]
            client.on_disconnect = self._on_disconnect  # type: ignore[assignment]

            try:
                LOG.info("Connecting to %s at %s:%s", self.cfg.name, self.cfg.host, self.cfg.port)
                client.connect(self.cfg.host, int(self.cfg.port), keepalive=60)
                client.loop_forever(retry_first_connection=False)
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                    self.connected = False
                    self.registered = False
                LOG.warning("CC2 connection failed for %s: %s", self.cfg.name, exc)
            finally:
                self._fail_pending("MQTT connection closed")

            if not self.stop_event.wait(5.0):
                continue

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        try:
            failed = False
            if hasattr(reason_code, "is_failure"):
                failed = bool(reason_code.is_failure)
            elif isinstance(reason_code, int):
                failed = reason_code != 0
            if failed:
                with self.lock:
                    self.last_error = f"MQTT connect failed: {reason_code}"
                return

            with self.lock:
                self.connected = True
                self.last_message = time.time()

            client.subscribe(self.register_response_topic)
            # Tiny delay helps ensure the subscription exists before the printer replies.
            time.sleep(0.15)
            client.publish(
                self.register_request_topic,
                json.dumps({"client_id": self.client_id, "request_id": self.register_request_id}),
            )
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
            LOG.exception("Connect handler failed")

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, *args: Any) -> None:
        with self.lock:
            self.connected = False
            self.registered = False
        self._fail_pending("MQTT disconnected")

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except Exception as exc:
            LOG.warning("Bad MQTT JSON on %s: %s", msg.topic, exc)
            return

        with self.lock:
            self.last_message = time.time()

        if msg.topic == self.register_response_topic:
            self._handle_register_response(client, payload)
        elif msg.topic == self.status_topic:
            self._handle_status(payload)
        elif msg.topic == self.response_topic:
            self._handle_response(payload)

    def _handle_register_response(self, client: mqtt.Client, payload: Dict[str, Any]) -> None:
        err = str(payload.get("error", "fail")).lower()
        if err != "ok":
            with self.lock:
                self.registration_error = err
                self.last_error = f"Registration failed: {err}"
                self.registered = False
            client.disconnect()
            return

        client.subscribe(self.status_topic)
        client.subscribe(self.response_topic)
        with self.lock:
            self.registered = True
            self.last_pong = time.time()
            self.registration_error = None

        self.send_request(1001, wait=False)
        self.send_request(1002, wait=False)
        self.send_request(2005, wait=False)  # Canvas/AMS status, if present.
        self._notify()

    def _handle_response(self, payload: Dict[str, Any]) -> None:
        if payload.get("type") == "PONG":
            with self.lock:
                self.last_pong = time.time()
            return

        method = payload.get("method")
        result = payload.get("result")
        if isinstance(result, dict):
            if method == 1001:
                with self.lock:
                    self.attributes.update(result)
            elif method == 1002:
                with self.lock:
                    deep_merge(self.full_status, result)
            elif method == 2005:
                with self.lock:
                    self.full_status["canvas"] = result

        request_id = payload.get("id")
        if request_id is None:
            self._notify()
            return
        try:
            request_id = int(request_id)
        except Exception:
            self._notify()
            return

        pending = None
        with self.lock:
            pending = self.pending.get(request_id)
        if pending:
            error = payload.get("error")
            if isinstance(error, dict):
                pending.error = json.dumps(error)
            elif isinstance(result, dict) and int(result.get("error_code", 0) or 0) != 0:
                pending.error = str(result.get("error_msg") or result)
            else:
                pending.result = result if isinstance(result, dict) else {}
            pending.event.set()
        self._notify()

    def _handle_status(self, payload: Dict[str, Any]) -> None:
        if payload.get("method") != 6000:
            return
        status_id = payload.get("status_id", payload.get("id"))
        try:
            status_id_int = int(status_id) if status_id is not None else None
        except Exception:
            status_id_int = None

        with self.lock:
            if status_id_int is not None:
                if self.last_status_id is not None and status_id_int != self.last_status_id + 1:
                    self.missed_status_count += 1
                else:
                    self.missed_status_count = 0
                self.last_status_id = status_id_int

            result = payload.get("result")
            if isinstance(result, dict):
                deep_merge(self.full_status, result)

            if self.missed_status_count >= 5:
                self.missed_status_count = 0
                # Ask for a full sync, but don't block message processing.
                threading.Thread(target=lambda: self.send_request(1002, wait=False), daemon=True).start()
        self._notify()

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(10.0):
            with self.lock:
                should_ping = self.registered and self.client is not None
                last_pong_age = time.time() - self.last_pong if self.last_pong else 0
            if not should_ping:
                continue
            try:
                assert self.client is not None
                self.client.publish(self.request_topic, json.dumps({"type": "PING"}))
                if last_pong_age > 65:
                    with self.lock:
                        self.last_error = "Heartbeat timed out"
                    self.client.disconnect()
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)

    def _fail_pending(self, error: str) -> None:
        with self.lock:
            items = list(self.pending.values())
            self.pending.clear()
        for pending in items:
            pending.error = error
            pending.event.set()

    def _next_id(self) -> int:
        with self.lock:
            self.next_request_id += 1
            return self.next_request_id

    def send_request(self, method: int, params: Optional[Dict[str, Any]] = None, wait: bool = True, timeout: float = 10.0) -> Dict[str, Any]:
        if params is None:
            params = {}
        with self.lock:
            if not self.registered or self.client is None:
                raise CommandError("Printer is not connected/registered yet")
            request_id = self._next_id()
            pending = PendingRequest() if wait else None
            if pending:
                self.pending[request_id] = pending

        payload = {"id": request_id, "method": method, "params": params}
        try:
            assert self.client is not None
            info = self.client.publish(self.request_topic, json.dumps(payload))
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise CommandError(f"MQTT publish failed: rc={info.rc}")
            if not wait:
                return {"queued": True, "id": request_id}
            assert pending is not None
            pending.event.wait(timeout)
            if pending.error:
                raise CommandError(pending.error)
            if pending.result is None:
                raise CommandError("Timeout waiting for printer response")
            return pending.result
        finally:
            if wait:
                with self.lock:
                    self.pending.pop(request_id, None)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            full = json.loads(json.dumps(self.full_status))
            attrs = json.loads(json.dumps(self.attributes))
            normalized = normalize_status(full, attrs)
            return {
                "id": self.cfg.id,
                "name": self.cfg.name,
                "host": self.cfg.host,
                "serial": self.cfg.serial,
                "connected": self.connected,
                "registered": self.registered,
                "registration_error": self.registration_error,
                "last_error": self.last_error,
                "last_message_age_sec": round(time.time() - self.last_message, 1) if self.last_message else None,
                "last_pong_age_sec": round(time.time() - self.last_pong, 1) if self.last_pong else None,
                "missed_status_count": self.missed_status_count,
                "allow_commands": self.cfg.allow_commands,
                "allow_dangerous_commands": self.cfg.allow_dangerous_commands,
                "normalized": normalized,
                "attributes": attrs,
                "raw_status": full,
            }

    def _notify(self) -> None:
        if self.on_update:
            try:
                self.on_update(self.cfg.id)
            except Exception:
                LOG.exception("on_update callback failed")
