from spigen_audio_ctrl import bluetooth, qcy_protocol

DEVICE_PATH = "/org/bluez/hci0/dev_TEST"


class _ConnectedDevice:
    def __init__(self):
        self.connect_calls = 0

    def Connect(self):
        self.connect_calls += 1

    def Get(self, _interface, property_name):
        return property_name in {"Connected", "ServicesResolved"}


class _Bus:
    def __init__(self, device):
        self.device = device

    def get_object(self, _service, _path):
        return self.device


def test_connected_audio_device_still_gets_control_profile_connect(monkeypatch):
    """A2DP being connected must not suppress BlueZ's missing-profile Connect call."""
    remote = _ConnectedDevice()
    service = object.__new__(bluetooth.BluetoothService)
    service.bus = _Bus(remote)
    service.write_path = None
    service.notify_paths = set()
    service._log = lambda *_args: None
    service._find_devices = lambda _objects: [("/org/bluez/hci0/dev_TEST", {"Name": "Spigen SA-HP P10"})]
    service._discover_characteristics = lambda: setattr(service, "write_path", "/control/write")
    monkeypatch.setattr(bluetooth.dbus, "Interface", lambda obj, _interface: obj)

    attached = service._try_candidates({}, set(), [], connected_timeout=1)

    assert attached is True
    assert remote.connect_calls == 1


def _advertisement_bytes(vendor_id=19789, left=91, right=88, case=77):
    data = bytearray(24)
    data[0:2] = vendor_id.to_bytes(2, "big")
    data[5] = left
    data[6] = right
    data[7] = case
    return data


def _characteristic(uuid, *flags):
    return {bluetooth.CHARACTERISTIC: {"UUID": uuid, "Flags": list(flags)}}


def _service(uuid):
    return {bluetooth.SERVICE: {"UUID": uuid}}


def test_short_uuid_matching_does_not_confuse_1001_with_0001():
    assert bluetooth.BluetoothService._short_uuid(
        "00001001-0000-1000-8000-00805f9b34fb"
    ) == "1001"
    assert not bluetooth.BluetoothService._uuid_matches(
        "00001001-0000-1000-8000-00805f9b34fb", "0001"
    )
    assert bluetooth.BluetoothService._uuid_matches(
        "0000AE01-0000-1000-8000-00805f9b34fb", "ae01"
    )


def test_tws_gatt_table_is_detected_with_full_characteristic_set():
    service = object.__new__(bluetooth.BluetoothService)
    service.device_path = DEVICE_PATH
    service._profile_hint = None
    service._start_notify = lambda path: service.notify_paths.add(path)
    service._managed_objects = lambda: {
        DEVICE_PATH: {},
        DEVICE_PATH + "/svc_a001": _service("0000a001-0000-1000-8000-00805f9b34fb"),
        DEVICE_PATH + "/char_1001": _characteristic(
            "00001001-0000-1000-8000-00805f9b34fb", "write", "write-without-response"
        ),
        DEVICE_PATH + "/char_1002": _characteristic(
            "00001002-0000-1000-8000-00805f9b34fb", "read", "notify"
        ),
        DEVICE_PATH + "/char_0008": _characteristic(
            "00000008-0000-1000-8000-00805f9b34fb", "read", "notify"
        ),
        DEVICE_PATH + "/char_000b": _characteristic(
            "0000000b-0000-1000-8000-00805f9b34fb", "read", "write"
        ),
        DEVICE_PATH + "/char_000d": _characteristic(
            "0000000d-0000-1000-8000-00805f9b34fb", "read", "write"
        ),
        DEVICE_PATH + "/char_0007": _characteristic(
            "00000007-0000-1000-8000-00805f9b34fb", "read", "notify"
        ),
    }

    service._discover_characteristics()

    assert service.profile == bluetooth.PROFILE_TWS
    assert service.write_path == DEVICE_PATH + "/char_1001"
    assert service.settings_path == DEVICE_PATH + "/char_1002"
    assert service.battery_path == DEVICE_PATH + "/char_0008"
    assert service.eq_path == DEVICE_PATH + "/char_000b"
    assert service.button_path == DEVICE_PATH + "/char_000d"
    assert service.version_path == DEVICE_PATH + "/char_0007"
    assert service.notify_paths == {
        DEVICE_PATH + "/char_1002",
        DEVICE_PATH + "/char_0008",
        DEVICE_PATH + "/char_0007",
    }


def test_headphone_detection_ignores_qcy_lookalike_characteristic():
    service = object.__new__(bluetooth.BluetoothService)
    service.device_path = DEVICE_PATH
    service._profile_hint = None
    service._start_notify = lambda path: service.notify_paths.add(path)
    service._managed_objects = lambda: {
        DEVICE_PATH: {},
        DEVICE_PATH + "/svc": _service("0000ae00-0000-1000-8000-00805f9b34fb"),
        DEVICE_PATH + "/char_1001": _characteristic(
            "00001001-0000-1000-8000-00805f9b34fb", "write"
        ),
        DEVICE_PATH + "/char_ae01": _characteristic(
            "0000ae01-0000-1000-8000-00805f9b34fb", "write"
        ),
        DEVICE_PATH + "/char_ae02": _characteristic(
            "0000ae02-0000-1000-8000-00805f9b34fb", "notify"
        ),
    }

    service._discover_characteristics()

    assert service.profile == bluetooth.PROFILE_HEADPHONES
    assert service.write_path == DEVICE_PATH + "/char_ae01"
    assert service.notify_paths == {DEVICE_PATH + "/char_ae02"}


def test_earbud_le_advertisement_ranks_before_classic_audio_object():
    service = object.__new__(bluetooth.BluetoothService)
    service._advertisement = None
    service._profile_hint = None
    classic_path = "/org/bluez/hci0/dev_84_AC_60_3D_7F_DA"
    le_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    objects = {
        classic_path: {
            bluetooth.DEVICE: {
                "Name": "SA-TW P10",
                "Class": 2393092,
                "Connected": True,
            }
        },
        le_path: {
            bluetooth.DEVICE: {
                "Name": "SA-TW P10",
                "AddressType": "random",
                "Connected": False,
                "ManufacturerData": {
                    qcy_protocol.SPIGEN_COMPANY_ID: _advertisement_bytes()
                },
            }
        },
    }

    ranked = service._find_devices(objects)

    assert [path for path, _props in ranked] == [le_path, classic_path]
    assert service._profile_hint == "tws"
    assert service._advertisement is not None
    assert service._advertisement.vendor_id == qcy_protocol.SA_TW_P10_VENDOR_ID


def test_tws_settings_snapshot_updates_live_state():
    service = object.__new__(bluetooth.BluetoothService)
    service.key_map = {}
    service.noise_mode = None
    service.gaming_mode = None
    service.sleep_mode = None
    events = []
    service._emit = lambda callback, *args: callback(*args) if callback else None
    service.on_noise_mode = lambda value: events.append(("anc", value))
    service.on_gaming_state = lambda value: events.append(("gaming", value))
    service.on_sleep_state = lambda value: events.append(("sleep", value))
    service.on_key_map = lambda value: events.append(("keys", value))

    service._handle_tws_settings(
        bytes.fromhex("FF0F0C01630901011001022B0401010304")
    )

    assert service.noise_mode == 99
    assert service.gaming_mode is True
    assert service.sleep_mode is False
    assert service.key_map == {1: 1, 3: 4}
    assert ("anc", 99) in events
    assert ("gaming", True) in events
    assert ("sleep", False) in events
    assert ("keys", {1: 1, 3: 4}) in events
