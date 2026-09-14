"""BlueZ D-Bus transport for the Spigen headphone GATT protocol."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import dbus
from gi.repository import GLib

from . import protocol

BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
BATTERY = "org.bluez.Battery1"


class BluetoothService:
    """Serializes GATT operations and publishes results on GTK's main thread."""

    def __init__(self) -> None:
        self.bus = dbus.SystemBus()
        self.manager = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spigen-ble")
        self.device_path: str | None = None
        self.write_path: str | None = None
        self.notify_paths: set[str] = set()
        self.device_name = "Spigen SA-HP P10"
        self.battery_level: int | None = None
        self.connected = False
        self._connecting = False
        self._sequence = 1
        self._manual_disconnect = False
        self._reconnect_source: int | None = None
        self._write_lock = threading.Lock()

        self.on_connection: Callable[[bool, str], None] | None = None
        self.on_battery: Callable[[int], None] | None = None
        self.on_hardware_info: Callable[[protocol.HardwareInfo], None] | None = None
        self.on_log: Callable[[str, str], None] | None = None
        self.bus.add_signal_receiver(
            self._properties_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPERTIES,
            path_keyword="path",
        )

    def _emit(self, callback, *args) -> None:
        if callback:
            GLib.idle_add(callback, *args)

    def _log(self, category: str, message: str) -> None:
        print(f"[{category}] {message}")
        self._emit(self.on_log, category, message)

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % 255
        if self._sequence == 0:
            self._sequence = 1
        return self._sequence

    def connect(self) -> None:
        if self.connected or self._connecting:
            return
        self._manual_disconnect = False
        self._connecting = True
        self._emit(self.on_connection, False, "Connecting…")
        self.executor.submit(self._connect_worker)

    def _managed_objects(self):
        return self.manager.GetManagedObjects()

    @staticmethod
    def _is_spigen(properties) -> bool:
        name = str(properties.get("Name") or properties.get("Alias") or "").upper()
        return "SPIGEN" in name or "SA-HP" in name or "P10" in name

    def _find_devices(self, objects) -> list[tuple[str, dict]]:
        """Return every plausible endpoint, preferring one with known GATT control data."""
        matches: list[tuple[str, dict]] = []
        for path, interfaces in objects.items():
            props = interfaces.get(DEVICE)
            if props and self._is_spigen(props):
                matches.append((str(path), props))

        def rank(candidate: tuple[str, dict]) -> tuple[bool, bool]:
            path, props = candidate
            has_control = any(
                str(child).startswith(path + "/")
                and (characteristic := child_interfaces.get(CHARACTERISTIC))
                and (
                    self._uuid_matches(str(characteristic.get("UUID", "")), "ae01")
                    or self._uuid_matches(str(characteristic.get("UUID", "")), "0001")
                )
                for child, child_interfaces in objects.items()
            )
            return has_control, bool(props.get("Connected"))

        return sorted(matches, key=rank, reverse=True)

    def _find_adapter(self, objects) -> str | None:
        return next((str(path) for path, interfaces in objects.items() if ADAPTER in interfaces), None)

    def _connect_worker(self) -> None:
        try:
            objects = self._managed_objects()
            adapter_path = self._find_adapter(objects)
            if not adapter_path:
                raise RuntimeError("No Bluetooth adapter found. Start bluetooth.service and unblock the adapter.")
            adapter_props = dbus.Interface(self.bus.get_object(BLUEZ, adapter_path), PROPERTIES)
            if not bool(adapter_props.Get(ADAPTER, "Powered")):
                adapter_props.Set(ADAPTER, "Powered", dbus.Boolean(True))

            attempted: set[str] = set()
            failures: list[str] = []
            if not self._try_candidates(objects, attempted, failures, connected_timeout=4):
                # A connected A2DP endpoint can share the P10 name while its BLE
                # controller is a second BlueZ object. Scan even when A2DP exists.
                self._log("BLE", "Scanning for the P10 control interface…")
                objects = self._scan_for_devices(adapter_path)
                # An LE advertisement also makes the same dual-mode Device1
                # connectable over its second bearer, so retry existing paths.
                attempted.clear()
                if not self._try_candidates(objects, attempted, failures, connected_timeout=15):
                    if not self._find_devices(objects):
                        raise RuntimeError("P10 control interface not found. Keep the headphones on and nearby.")
                    detail = failures[-1] if failures else "no compatible GATT endpoint"
                    raise RuntimeError(f"P10 audio is available, but BLE controls are not: {detail}")

            self.connected = True
            self._connecting = False
            self._emit(self.on_connection, True, self.device_name)
            self._log("BLE", "Hardware engine attached and ready")
            time.sleep(0.1)
            self._query_hardware_worker()
        except Exception as error:
            self.connected = False
            self._connecting = False
            self._log("ERR", str(error))
            self._emit(self.on_connection, False, f"Error: {error}")

    def _try_candidates(
        self,
        objects,
        attempted: set[str],
        failures: list[str],
        connected_timeout: float,
    ) -> bool:
        """Try all matching Device1 objects without disturbing an existing audio link."""
        for device_path, device_props in self._find_devices(objects):
            if device_path in attempted:
                continue
            attempted.add(device_path)
            self.device_path = device_path
            self.device_name = str(device_props.get("Name") or device_props.get("Alias") or "Spigen SA-HP P10")
            try:
                device = dbus.Interface(self.bus.get_object(BLUEZ, device_path), DEVICE)
                props = dbus.Interface(self.bus.get_object(BLUEZ, device_path), PROPERTIES)
                was_connected = bool(props.Get(DEVICE, "Connected"))
                if not was_connected:
                    self._log("BLE", f"Connecting to {self.device_name}…")
                else:
                    self._log("BLE", f"Attaching the control profile for {self.device_name}…")
                try:
                    # Device1.Connected can represent A2DP alone. Connect() is
                    # intentionally called again so BlueZ adds missing profiles.
                    device.Connect()
                except dbus.DBusException as error:
                    if "AlreadyConnected" not in error.get_dbus_name():
                        raise

                deadline = time.monotonic() + (connected_timeout if was_connected else 15)
                while time.monotonic() < deadline:
                    if bool(props.Get(DEVICE, "ServicesResolved")):
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("GATT services did not resolve")

                self._discover_characteristics()
                if self.write_path:
                    return True
                raise RuntimeError("RCSP write characteristic is missing")
            except Exception as error:
                failures.append(str(error))
                self._log("BLE", f"Skipping {self.device_name}: {error}")
                self.write_path = None
                self.notify_paths.clear()
        return False

    def _scan_for_devices(self, adapter_path: str):
        adapter = dbus.Interface(self.bus.get_object(BLUEZ, adapter_path), ADAPTER)
        started_discovery = False
        try:
            adapter.SetDiscoveryFilter({"Transport": dbus.String("le")})
            try:
                adapter.StartDiscovery()
                started_discovery = True
            except dbus.DBusException as error:
                if "InProgress" not in error.get_dbus_name():
                    raise
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                time.sleep(0.25)
                objects = self._managed_objects()
                if any(path != self.device_path for path, _props in self._find_devices(objects)):
                    return objects
            return self._managed_objects()
        finally:
            if started_discovery:
                try:
                    adapter.StopDiscovery()
                except dbus.DBusException:
                    pass

    @staticmethod
    def _uuid_matches(uuid: str, short: str) -> bool:
        uuid = uuid.lower()
        return uuid.startswith(f"0000{short}-") or uuid == short or short in uuid.split("-")[0]

    def _discover_characteristics(self) -> None:
        self.write_path = None
        self.notify_paths.clear()
        objects = self._managed_objects()
        notify_candidates: list[str] = []
        battery_candidates: list[tuple[str, set[str]]] = []
        for path, interfaces in objects.items():
            path_string = str(path)
            if not self.device_path or not path_string.startswith(self.device_path + "/"):
                continue
            char = interfaces.get(CHARACTERISTIC)
            if not char:
                continue
            uuid = str(char.get("UUID", "")).lower()
            flags = {str(flag) for flag in char.get("Flags", [])}
            if (self._uuid_matches(uuid, "ae01") or self._uuid_matches(uuid, "0001")) and flags & {"write", "write-without-response"}:
                self.write_path = path_string
            if (self._uuid_matches(uuid, "ae02") or self._uuid_matches(uuid, "0002")) and "notify" in flags:
                notify_candidates.append(path_string)
            if self._uuid_matches(uuid, "2a19"):
                battery_candidates.append((path_string, flags))

        # Do not subscribe on a name-matching A2DP-only endpoint that we are
        # about to skip. This also keeps the idle D-Bus traffic to a minimum.
        if not self.write_path:
            return
        for path_string in notify_candidates:
            self._start_notify(path_string)
        for path_string, flags in battery_candidates:
            try:
                value = dbus.Interface(self.bus.get_object(BLUEZ, path_string), CHARACTERISTIC).ReadValue({})
                if value:
                    self._set_battery(int(value[0]))
            except dbus.DBusException as error:
                self._log("BATTERY", f"Battery characteristic read skipped: {error}")
            if "notify" in flags:
                self._start_notify(path_string)
        device_interfaces = objects.get(dbus.ObjectPath(self.device_path), {}) if self.device_path else {}
        if BATTERY in device_interfaces:
            percentage = device_interfaces[BATTERY].get("Percentage")
            if percentage is not None:
                self._set_battery(int(percentage))

    def _start_notify(self, path: str) -> None:
        try:
            dbus.Interface(self.bus.get_object(BLUEZ, path), CHARACTERISTIC).StartNotify()
            self.notify_paths.add(path)
        except dbus.DBusException as error:
            if "AlreadyNotifying" in error.get_dbus_name():
                self.notify_paths.add(path)
            else:
                self._log("BLE", f"Could not subscribe to {path.rsplit('/', 1)[-1]}: {error}")

    def _properties_changed(self, interface: str, changed, _invalidated, path: str) -> None:
        path = str(path)
        if interface == DEVICE and path == self.device_path:
            if "Connected" in changed:
                is_connected = bool(changed["Connected"])
                self.connected = is_connected and self.write_path is not None
                if not is_connected:
                    self.write_path = None
                    self._emit(self.on_connection, False, "Disconnected")
                    if not self._manual_disconnect and self._reconnect_source is None:
                        self._reconnect_source = GLib.timeout_add_seconds(3, self._auto_reconnect)
            return
        if interface == BATTERY and path == self.device_path and "Percentage" in changed:
            self._set_battery(int(changed["Percentage"]))
            return
        if interface != CHARACTERISTIC or path not in self.notify_paths or "Value" not in changed:
            return
        data = bytes(changed["Value"])
        self._log("RX", data.hex().upper())
        if self._uuid_for_path(path) == "2a19" and data:
            self._set_battery(data[0])
        battery = protocol.parse_target_battery(data)
        if battery is not None:
            self._set_battery(battery)
        if len(data) > 4 and data[:3] == b"\xfe\xdc\xba" and data[4] == 0xC1:
            self._emit(self.on_hardware_info, protocol.parse_hardware_info(data))

    def _uuid_for_path(self, path: str) -> str:
        try:
            value = dbus.Interface(self.bus.get_object(BLUEZ, path), PROPERTIES).Get(CHARACTERISTIC, "UUID")
            uuid = str(value).lower()
            return "2a19" if self._uuid_matches(uuid, "2a19") else uuid
        except dbus.DBusException:
            return ""

    def _auto_reconnect(self) -> bool:
        self._reconnect_source = None
        if not self._manual_disconnect and not self.connected:
            self.connect()
        return GLib.SOURCE_REMOVE

    def _set_battery(self, level: int) -> None:
        if 0 <= level <= 100:
            self.battery_level = level
            self._emit(self.on_battery, level)

    def send(self, packet: bytes) -> None:
        self.executor.submit(self._write_packet, packet)

    def _write_packet(self, packet: bytes) -> bool:
        if not self.connected or not self.write_path:
            self._log("ERR", "Headphones are not connected")
            return False
        try:
            with self._write_lock:
                props = dbus.Interface(self.bus.get_object(BLUEZ, self.write_path), PROPERTIES)
                flags = {str(value) for value in props.Get(CHARACTERISTIC, "Flags")}
                write_type = "command" if "write-without-response" in flags else "request"
                characteristic = dbus.Interface(self.bus.get_object(BLUEZ, self.write_path), CHARACTERISTIC)
                characteristic.WriteValue(dbus.ByteArray(packet), {"type": dbus.String(write_type)})
            self._log("TX", packet.hex().upper())
            return True
        except Exception as error:
            self._log("ERR", f"Write failed: {error}")
            return False

    def set_anc_mode(self, anc_id: int) -> None:
        self.send(protocol.encode_anc(anc_id, self._next_sequence()))

    def set_equalizer(self, gains: list[float]) -> None:
        self.send(protocol.encode_equalizer(gains, sequence=self._next_sequence()))

    def set_gaming_mode(self, enabled: bool) -> None:
        self.send(protocol.encode_gaming_mode(enabled, self._next_sequence()))

    def set_key_mapping(self, key_number: int, action: int, function: int) -> None:
        self.send(protocol.encode_key_mapping(key_number, action, function, self._next_sequence()))

    def query_hardware(self) -> None:
        self.executor.submit(self._query_hardware_worker)

    def _query_hardware_worker(self) -> None:
        self._write_packet(protocol.pack_rcsp(0x03, b"\xff\xff\xff\xff\x00", self._next_sequence()))
        time.sleep(0.08)
        self._write_packet(protocol.pack_rcsp(0xC1, b"\xff\xff\xff\xff", self._next_sequence()))

    def disconnect(self) -> None:
        self._manual_disconnect = True
        self._connecting = False
        if self._reconnect_source is not None:
            GLib.source_remove(self._reconnect_source)
            self._reconnect_source = None
        self.executor.submit(self._disconnect_worker)

    def _disconnect_worker(self) -> None:
        for path in tuple(self.notify_paths):
            try:
                dbus.Interface(self.bus.get_object(BLUEZ, path), CHARACTERISTIC).StopNotify()
            except dbus.DBusException:
                pass
        self.notify_paths.clear()
        if self.device_path:
            try:
                dbus.Interface(self.bus.get_object(BLUEZ, self.device_path), DEVICE).Disconnect()
            except dbus.DBusException:
                pass
        self.connected = False
        self._connecting = False
        self.write_path = None
        self.battery_level = None
        self._emit(self.on_connection, False, "Disconnected")

    def close(self) -> None:
        self.disconnect()
        self.executor.shutdown(wait=False, cancel_futures=False)
