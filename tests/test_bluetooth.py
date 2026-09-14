from spigen_audio_ctrl import bluetooth


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
