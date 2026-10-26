"""BlueZ D-Bus transport for Spigen headphones and TWS earbuds.

The SA-HP P10 uses the JL RCSP GATT protocol.  The SA-TW P10 uses the QCY
``A001`` GATT service.  Both share the same BlueZ connection management; the
active protocol is detected from the resolved GATT table.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import dbus
from gi.repository import GLib

from . import protocol, qcy_protocol

BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
SERVICE = "org.bluez.GattService1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
BATTERY = "org.bluez.Battery1"

PROFILE_HEADPHONES = "headphones"
PROFILE_TWS = "tws"


class BluetoothService:
    """Serializes GATT operations and publishes results on GTK's main thread."""

    def __init__(self) -> None:
        self.bus = dbus.SystemBus()
        self.manager = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spigen-ble")
        self.device_path: str | None = None
        self.write_path: str | None = None
        self.notify_paths: set[str] = set()
        self.char_uuids: dict[str, str] = {}
        self.eq_path: str | None = None
        self.button_path: str | None = None
        self.battery_path: str | None = None
        self.version_path: str | None = None
        self.settings_path: str | None = None
        self.profile = "unknown"
        self.device_name = "Spigen P10"
        self.battery_level: int | None = None
        self.battery_state = qcy_protocol.BatteryState()
        self.key_map: dict[int, int] = {}
        self.noise_mode: int | None = None
        self.gaming_mode: bool | None = None
        self.sleep_mode: bool | None = None
        self.connected = False
        self._connecting = False
        self._sequence = 1
        self._manual_disconnect = False
        self._reconnect_source: int | None = None
        self._write_lock = threading.Lock()
        self._announced_profile: str | None = None
        self._advertisement: qcy_protocol.Advertisement | None = None
        self._profile_hint: str | None = None

        self.on_connection: Callable[[bool, str], None] | None = None
        self.on_battery: Callable[[int], None] | None = None
        self.on_battery_state: Callable[[qcy_protocol.BatteryState], None] | None = None
        self.on_profile: Callable[[str], None] | None = None
        self.on_hardware_info: Callable[[protocol.HardwareInfo], None] | None = None
        self.on_noise_mode: Callable[[int], None] | None = None
        self.on_gaming_state: Callable[[bool], None] | None = None
        self.on_sleep_state: Callable[[bool], None] | None = None
        self.on_equalizer: Callable[[list[float]], None] | None = None
        self.on_key_map: Callable[[dict[int, int]], None] | None = None
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
        if any(token in name for token in ("SPIGEN", "SA-HP", "SA-TW", "P10")):
            return True
        # The LE control advertisement carries Spigen's company id even when
        # BlueZ has not received a friendly name for the random LE address.
        return qcy_protocol.parse_advertisement(properties.get("ManufacturerData") or {}) is not None

    def _find_devices(self, objects) -> list[tuple[str, dict]]:
        """Return every plausible endpoint, preferring one with a live GATT control table."""
        matches: list[tuple[str, dict]] = []
        for path, interfaces in objects.items():
            props = interfaces.get(DEVICE)
            if props and self._is_spigen(props):
                matches.append((str(path), props))

        def has_control(path: str) -> bool:
            for child, child_interfaces in objects.items():
                if not str(child).startswith(path + "/"):
                    continue
                characteristic = child_interfaces.get(CHARACTERISTIC)
                if not characteristic:
                    continue
                short = self._short_uuid(str(characteristic.get("UUID", "")))
                if short in (qcy_protocol.SHORT_COMMAND, "ae01", "0001"):
                    return True
            return False

        def rank(candidate: tuple[str, dict]) -> tuple[bool, bool, bool, bool]:
            path, props = candidate
            advertisement = qcy_protocol.parse_advertisement(props.get("ManufacturerData") or {})
            is_classic = props.get("Class") is not None
            return (
                has_control(path),
                advertisement is not None,
                not is_classic,
                bool(props.get("Connected")),
            )

        ranked = sorted(matches, key=rank, reverse=True)
        for _path, props in ranked:
            advertisement = qcy_protocol.parse_advertisement(props.get("ManufacturerData") or {})
            if advertisement is not None:
                self._advertisement = advertisement
                self._profile_hint = (
                    qcy_protocol.family_for_vendor_id(advertisement.vendor_id)
                    or self._profile_hint
                )
                break
        return ranked

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
                # controller is a second BlueZ object (or a random LE address).
                # Scan even when A2DP is available.
                self._log("BLE", "Scanning for the P10 control interface…")
                objects = self._scan_for_devices(adapter_path)
                # An LE advertisement also makes the same dual-mode Device1
                # connectable over its second bearer, so retry existing paths.
                attempted.clear()
                if not self._try_candidates(objects, attempted, failures, connected_timeout=15):
                    if not self._find_devices(objects):
                        if self._profile_hint == PROFILE_TWS or "SA-TW" in self.device_name.upper():
                            raise RuntimeError(
                                "SA-TW P10 control channel not found. Open the charging case "
                                "with the earbuds inside, or quit the phone app, so the "
                                "earbuds advertise Bluetooth LE."
                            )
                        raise RuntimeError("P10 control interface not found. Keep the headphones on and nearby.")
                    detail = failures[-1] if failures else "no compatible GATT endpoint"
                    if self._profile_hint == PROFILE_TWS or "SA-TW" in self.device_name.upper():
                        raise RuntimeError(
                            f"Earbuds are connected for audio, but the LE control channel is "
                            f"unavailable: {detail}. Open the charging case or quit the phone app."
                        )
                    raise RuntimeError(f"P10 audio is available, but BLE controls are not: {detail}")

            self.connected = True
            self._connecting = False
            self._announce_profile()
            self._emit(self.on_connection, True, self.device_name)
            self._publish_advertised_battery()
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
            name = str(device_props.get("Name") or device_props.get("Alias") or "").strip()
            if not name:
                advertisement = qcy_protocol.parse_advertisement(
                    device_props.get("ManufacturerData") or {}
                )
                model = advertisement.model if advertisement else None
                name = f"Spigen {model}" if model else "Spigen P10"
            self.device_name = name
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
                raise RuntimeError("control characteristic is missing")
            except Exception as error:
                failures.append(str(error))
                self._log("BLE", f"Skipping {self.device_name}: {error}")
                self._clear_characteristics()
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
                # A stale desktop scan may still be running without the LE
                # transport filter. Restart it once so the control advert is
                # actually collected.
                try:
                    adapter.StopDiscovery()
                    time.sleep(0.3)
                    adapter.StartDiscovery()
                    started_discovery = True
                except dbus.DBusException as retry_error:
                    if "InProgress" not in retry_error.get_dbus_name():
                        raise
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                time.sleep(0.25)
                objects = self._managed_objects()
                for path, props in self._find_devices(objects):
                    if path != self.device_path:
                        return objects
                    if qcy_protocol.parse_advertisement(props.get("ManufacturerData") or {}):
                        return objects
            return self._managed_objects()
        finally:
            if started_discovery:
                try:
                    adapter.StopDiscovery()
                except dbus.DBusException:
                    pass

    @staticmethod
    def _short_uuid(uuid: str) -> str:
        value = str(uuid).lower().strip("{}")
        if len(value) == 4:
            return value
        if value.startswith("0000") and value.endswith("-0000-1000-8000-00805f9b34fb"):
            return value[4:8]
        return value

    @classmethod
    def _uuid_matches(cls, uuid: str, short: str) -> bool:
        return cls._short_uuid(uuid) == short.lower()

    def _clear_characteristics(self) -> None:
        self.write_path = None
        self.notify_paths = set()
        self.char_uuids = {}
        self.eq_path = None
        self.button_path = None
        self.battery_path = None
        self.version_path = None
        self.settings_path = None

    def _discover_characteristics(self) -> None:
        self._clear_characteristics()
        objects = self._managed_objects()
        services: dict[str, str] = {}
        chars: dict[str, tuple[str, set[str]]] = {}
        for path, interfaces in objects.items():
            path_string = str(path)
            if not self.device_path or not path_string.startswith(self.device_path + "/"):
                continue
            service = interfaces.get(SERVICE)
            if service:
                services[self._short_uuid(str(service.get("UUID", "")))] = path_string
            characteristic = interfaces.get(CHARACTERISTIC)
            if not characteristic:
                continue
            short = self._short_uuid(str(characteristic.get("UUID", "")))
            flags = {str(flag) for flag in characteristic.get("Flags", [])}
            self.char_uuids[path_string] = short
            chars.setdefault(short, (path_string, flags))

        qcy_command = chars.get(qcy_protocol.SHORT_COMMAND)
        qcy_settings = chars.get(qcy_protocol.SHORT_SETTINGS)
        if (qcy_command and qcy_settings) or "a001" in services:
            self.profile = PROFILE_TWS
            if qcy_command and qcy_command[1] & {"write", "write-without-response"}:
                self.write_path = qcy_command[0]
            for short, attribute in (
                (qcy_protocol.SHORT_SETTINGS, "settings_path"),
                (qcy_protocol.SHORT_BATTERY, "battery_path"),
                (qcy_protocol.SHORT_EQUALIZER, "eq_path"),
                (qcy_protocol.SHORT_BUTTON, "button_path"),
                (qcy_protocol.SHORT_VERSION, "version_path"),
            ):
                entry = chars.get(short)
                if entry:
                    setattr(self, attribute, entry[0])
            for short in (
                qcy_protocol.SHORT_SETTINGS,
                qcy_protocol.SHORT_BATTERY,
                qcy_protocol.SHORT_EQUALIZER,
                qcy_protocol.SHORT_BUTTON,
                qcy_protocol.SHORT_VERSION,
            ):
                entry = chars.get(short)
                if entry and "notify" in entry[1]:
                    self.notify_paths.add(entry[0])
            for path_string in self.notify_paths:
                self._start_notify(path_string)
            return

        self.profile = PROFILE_HEADPHONES
        notify_candidates: list[str] = []
        battery_candidates: list[tuple[str, set[str]]] = []
        for short in ("ae01", "0001"):
            entry = chars.get(short)
            if entry and entry[1] & {"write", "write-without-response"}:
                self.write_path = entry[0]
                break
        for short in ("ae02", "0002"):
            entry = chars.get(short)
            if entry and "notify" in entry[1]:
                notify_candidates.append(entry[0])
        entry = chars.get("2a19")
        if entry:
            battery_candidates.append(entry)

        # Do not subscribe on a name-matching A2DP-only endpoint that we are
        # about to skip. This also keeps the idle D-Bus traffic to a minimum.
        if not self.write_path:
            self.profile = self._profile_hint or "unknown"
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
                    self.eq_path = None
                    self.button_path = None
                    self.battery_path = None
                    self.version_path = None
                    self.settings_path = None
                    self._emit(self.on_connection, False, "Disconnected")
                    if not self._manual_disconnect and self._reconnect_source is None:
                        self._reconnect_source = GLib.timeout_add_seconds(3, self._auto_reconnect)
            return
        if interface == BATTERY and path == self.device_path and "Percentage" in changed:
            if getattr(self, "profile", None) != PROFILE_TWS:
                self._set_battery(int(changed["Percentage"]))
            return
        if interface != CHARACTERISTIC or path not in self.notify_paths or "Value" not in changed:
            return
        data = bytes(changed["Value"])
        self._log("RX", data.hex().upper())
        short = self._uuid_for_path(path)
        if short == qcy_protocol.SHORT_SETTINGS:
            self._handle_tws_settings(data)
            return
        if short in (
            qcy_protocol.SHORT_BATTERY,
            qcy_protocol.SHORT_EQUALIZER,
            qcy_protocol.SHORT_BUTTON,
            qcy_protocol.SHORT_VERSION,
        ):
            self._handle_tws_value(short, data)
            return
        if short == "2a19" and data:
            self._set_battery(data[0])
        battery = protocol.parse_target_battery(data)
        if battery is not None:
            self._set_battery(battery)
        if len(data) > 4 and data[:3] == b"\xfe\xdc\xba" and data[4] == 0xC1:
            self._emit(self.on_hardware_info, protocol.parse_hardware_info(data))

    def _uuid_for_path(self, path: str) -> str:
        path = str(path)
        cached = getattr(self, "char_uuids", {}).get(path)
        if cached:
            return cached
        try:
            value = dbus.Interface(self.bus.get_object(BLUEZ, path), PROPERTIES).Get(
                CHARACTERISTIC, "UUID"
            )
            short = self._short_uuid(str(value))
            self.char_uuids[path] = short
            return short
        except dbus.DBusException:
            return ""

    def _auto_reconnect(self) -> bool:
        self._reconnect_source = None
        if not self._manual_disconnect and not self.connected:
            self.connect()
        return GLib.SOURCE_REMOVE

    def _announce_profile(self) -> None:
        profile = getattr(self, "profile", None)
        if not profile or profile == "unknown":
            return
        if self._announced_profile == profile:
            return
        self._announced_profile = profile
        self._emit(getattr(self, "on_profile", None), profile)

    def _publish_advertised_battery(self) -> None:
        advertisement = getattr(self, "_advertisement", None)
        if advertisement is not None and getattr(self, "profile", None) == PROFILE_TWS:
            self._set_tws_battery(advertisement.battery)

    def _set_battery(self, level: int) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            return
        if 0 <= level <= 100:
            self.battery_level = level
            self._emit(self.on_battery, level)

    def _set_tws_battery(self, state: qcy_protocol.BatteryState) -> None:
        self.battery_state = state
        lowest = state.lowest()
        if lowest is not None:
            self.battery_level = lowest
        self._emit(self.on_battery_state, state)

    def send(self, packet: bytes) -> None:
        self.executor.submit(self._write_packet, packet)

    def _write_packet(self, packet: bytes) -> bool:
        if not self.write_path:
            self._log("ERR", "Device is not connected")
            return False
        return self._write_packet_to(self.write_path, packet)

    def _write_packet_to(self, path: str, packet: bytes) -> bool:
        if not self.connected:
            self._log("ERR", "Device is not connected")
            return False
        try:
            with self._write_lock:
                props = dbus.Interface(self.bus.get_object(BLUEZ, path), PROPERTIES)
                flags = {str(value) for value in props.Get(CHARACTERISTIC, "Flags")}
                write_type = "command" if "write-without-response" in flags else "request"
                characteristic = dbus.Interface(self.bus.get_object(BLUEZ, path), CHARACTERISTIC)
                characteristic.WriteValue(dbus.ByteArray(packet), {"type": dbus.String(write_type)})
            self._log("TX", packet.hex().upper())
            return True
        except Exception as error:
            self._log("ERR", f"Write failed: {error}")
            return False

    def _read_characteristic(self, path: str) -> bytes | None:
        if not self.connected:
            return None
        try:
            characteristic = dbus.Interface(self.bus.get_object(BLUEZ, path), CHARACTERISTIC)
            value = characteristic.ReadValue({})
            data = bytes(int(byte) for byte in value)
            self._log("RX", data.hex().upper())
            self._handle_tws_value(self._uuid_for_path(path), data)
            return data
        except dbus.DBusException as error:
            self._log("BLE", f"Read {path.rsplit('/', 1)[-1]} skipped: {error}")
            return None

    def _handle_tws_value(self, short: str, data: bytes) -> None:
        if short == qcy_protocol.SHORT_SETTINGS:
            self._handle_tws_settings(data)
            return
        if short == qcy_protocol.SHORT_BATTERY:
            state = qcy_protocol.parse_battery(data)
            if state is not None:
                self._set_tws_battery(state)
            return
        if short == qcy_protocol.SHORT_VERSION:
            left, right = qcy_protocol.parse_version(data)
            if left:
                firmware = f"L {left}" + (f" · R {right}" if right else "")
                self._emit(
                    self.on_hardware_info,
                    protocol.HardwareInfo(device_name=self.device_name, firmware=firmware),
                )
            return
        if short == qcy_protocol.SHORT_EQUALIZER:
            parsed = qcy_protocol.parse_equalizer(data)
            if parsed is not None:
                _eq_type, gains = parsed
                self._emit(self.on_equalizer, gains)
            return
        if short == qcy_protocol.SHORT_BUTTON:
            mapping = qcy_protocol.parse_key_map(data)
            if mapping:
                self.key_map = mapping
                self._emit(self.on_key_map, mapping)

    def _handle_tws_settings(self, data: bytes) -> None:
        for command, payload in qcy_protocol.parse_frames(data):
            if command == qcy_protocol.CMD_NOISE_MODE and payload:
                value = payload[0]
                self.noise_mode = value
                self._emit(self.on_noise_mode, value)
            elif command == qcy_protocol.CMD_GAMING:
                enabled = qcy_protocol.parse_gaming(payload)
                if enabled is not None:
                    self.gaming_mode = enabled
                    self._emit(self.on_gaming_state, enabled)
            elif command == qcy_protocol.CMD_SLEEP:
                enabled = qcy_protocol.parse_sleep(payload)
                if enabled is not None:
                    self.sleep_mode = enabled
                    self._emit(self.on_sleep_state, enabled)
            elif command == qcy_protocol.CMD_KEY_FUNCTION:
                mapping = qcy_protocol.parse_key_map(payload)
                if mapping:
                    self.key_map = mapping
                    self._emit(self.on_key_map, mapping)

    def set_anc_mode(self, anc_id: int) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            try:
                self.send(qcy_protocol.encode_noise_mode(anc_id))
            except ValueError as error:
                self._log("ERR", f"Invalid noise mode: {error}")
            return
        self.send(protocol.encode_anc(anc_id, self._next_sequence()))

    def set_equalizer(self, gains: list[float]) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            if not self.eq_path:
                self._log("ERR", "Equalizer characteristic is unavailable")
                return
            try:
                packet = qcy_protocol.encode_equalizer(gains)
            except ValueError as error:
                self._log("ERR", f"Invalid equalizer gains: {error}")
                return
            self.executor.submit(self._write_packet_to, self.eq_path, packet)
            return
        self.send(protocol.encode_equalizer(gains, sequence=self._next_sequence()))

    def set_gaming_mode(self, enabled: bool) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            self.send(qcy_protocol.encode_gaming(enabled))
            return
        self.send(protocol.encode_gaming_mode(enabled, self._next_sequence()))

    def set_sleep_mode(self, enabled: bool) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            self.send(qcy_protocol.encode_sleep(enabled))

    def set_key_mapping(self, key_number: int, action: int, function: int) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            self.set_key_function(key_number, function)
            return
        self.send(protocol.encode_key_mapping(key_number, action, function, self._next_sequence()))

    def set_key_function(self, event: int, function: int) -> None:
        if getattr(self, "profile", None) != PROFILE_TWS:
            return
        if not self.button_path:
            self._log("ERR", "Button characteristic is unavailable")
            return
        self.key_map[int(event)] = int(function)
        if len(self.key_map) < len(qcy_protocol.KEY_EVENTS):
            self._log("BLE", "Key map is incomplete; writing the known assignments")
        packet = qcy_protocol.encode_key_map(self.key_map)
        self.executor.submit(self._write_packet_to, self.button_path, packet)

    def query_hardware(self) -> None:
        self.executor.submit(self._query_hardware_worker)

    def _query_hardware_worker(self) -> None:
        if getattr(self, "profile", None) == PROFILE_TWS:
            self._query_tws()
            return
        self._write_packet(protocol.pack_rcsp(0x03, b"\xff\xff\xff\xff\x00", self._next_sequence()))
        time.sleep(0.08)
        self._write_packet(protocol.pack_rcsp(0xC1, b"\xff\xff\xff\xff", self._next_sequence()))

    def _query_tws(self) -> None:
        for path in (self.battery_path, self.version_path, self.eq_path, self.button_path):
            if path:
                self._read_characteristic(path)
        if self.settings_path:
            self._read_characteristic(self.settings_path)
        for command in (
            qcy_protocol.CMD_NOISE_MODE,
            qcy_protocol.CMD_GAMING,
            qcy_protocol.CMD_SLEEP,
        ):
            self._write_packet(qcy_protocol.encode_request(command))
            time.sleep(0.05)

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
        self._clear_characteristics()
        self.battery_level = None
        self.battery_state = qcy_protocol.BatteryState()
        self.key_map = {}
        self._emit(self.on_connection, False, "Disconnected")

    def close(self) -> None:
        self.disconnect()
        self.executor.shutdown(wait=False, cancel_futures=False)
