"""QCY-family BLE protocol used by the Spigen SA-TW P10 earbuds.

The SA-TW P10 is a QCY HT07 rebrand.  Unlike the SA-HP P10 (JL RCSP), it
speaks the compact QCY ``FF`` frame protocol over GATT service ``A001``.
This module only implements the wire format; transport lives in
:mod:`spigen_audio_ctrl.bluetooth`.
"""

from __future__ import annotations

from dataclasses import dataclass

SOF = 0xFF

SERVICE_UUID = "0000a001-0000-1000-8000-00805f9b34fb"
CHAR_COMMAND = "00001001-0000-1000-8000-00805f9b34fb"
CHAR_SETTINGS = "00001002-0000-1000-8000-00805f9b34fb"
CHAR_BATTERY = "00000008-0000-1000-8000-00805f9b34fb"
CHAR_EQUALIZER = "0000000b-0000-1000-8000-00805f9b34fb"
CHAR_BUTTON = "0000000d-0000-1000-8000-00805f9b34fb"
CHAR_VERSION = "00000007-0000-1000-8000-00805f9b34fb"

# A short UUID is enough because every characteristic lives in the A001 service.
SHORT_COMMAND = "1001"
SHORT_SETTINGS = "1002"
SHORT_BATTERY = "0008"
SHORT_EQUALIZER = "000b"
SHORT_BUTTON = "000d"
SHORT_VERSION = "0007"

CMD_RESET = 1
# cmd 7/0xFF is the vendor's ambient "noise value" read. It is not the ANC
# mode: cmd 12 carries the HT07 tile/mode byte used by this app.
CMD_NOISE = 7
CMD_GAMING = 9
CMD_NOISE_MODE = 12
CMD_SLEEP = 16
CMD_BALANCE = 22
CMD_ANC_SETTING = 23
CMD_MULTI_EQ = 32
CMD_MULTI_EQ2 = 34
CMD_KEY_FUNCTION = 43
CMD_REQUEST = 0xFE

# HT07/SA-TW P10 equalizer: ten whole-dB bands stored on the earbuds.
EQ_FREQUENCIES = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_MIN_DB = -8.0
EQ_MAX_DB = 8.0
EQ_CUSTOM_TYPE = 255

# Spigen's BLE advertisement uses its own Bluetooth SIG company identifier and
# packs the vendor/model id plus both batteries into the manufacturer data.
SPIGEN_COMPANY_ID = 21036
SA_HP_P10_VENDOR_ID = 16762
SA_TW_P20_VENDOR_ID = 19788
SA_TW_P10_VENDOR_ID = 19789
SA_TW_S10_VENDOR_ID = 18297

VENDOR_NAMES = {
    SA_HP_P10_VENDOR_ID: "SA-HP P10",
    SA_TW_P20_VENDOR_ID: "SA-TW P20",
    SA_TW_P10_VENDOR_ID: "SA-TW P10",
    SA_TW_S10_VENDOR_ID: "SA-TW S10",
}

# HT07 control-panel values from earphone_ht07.json.  The low nibble is the
# strength level, so a device may report any value inside the range.
ANC_MODES = (
    ("Noisy", "Max ANC", "Maximum cancellation for loud environments.", "◉", 99, 97, 99),
    ("Indoor", "Quiet", "Optimised for quiet rooms and offices.", "⌂", 67, 65, 67),
    ("Commuting", "Transit", "Targets engine and rail rumble while travelling.", "⇄", 83, 81, 83),
    ("Anti-Wind", "Outdoor", "Reduces wind buffeting across the microphones.", "≈", 113, 113, 113),
    ("Transparency", "Ambient", "Let outside sound through without removing the earbuds.", "◎", 164, 160, 166),
    ("Normal", "Passive", "Turns off ANC processing for the longest battery life.", "○", 2, 2, 2),
)
DEFAULT_ANC_MODE = ANC_MODES[0]

KEY_FUNCTIONS = (
    (0, "Disabled"),
    (1, "Play / Pause"),
    (2, "Previous Track"),
    (3, "Next Track"),
    (4, "Voice Assistant"),
    (5, "Volume Up"),
    (6, "Volume Down"),
    (7, "Gaming Mode Toggle"),
    (11, "ANC Mode Switch"),
)

# event id -> (earbud, gesture).  The firmware numbers left/right interleaved.
KEY_EVENTS = {
    1: ("L", "Single Touch"),
    2: ("R", "Single Touch"),
    3: ("L", "Double Touch"),
    4: ("R", "Double Touch"),
    5: ("L", "Triple Touch"),
    6: ("R", "Triple Touch"),
}


@dataclass(slots=True)
class BatteryState:
    left: int | None = None
    right: int | None = None
    case: int | None = None
    left_charging: bool = False
    right_charging: bool = False
    case_charging: bool = False

    def lowest(self) -> int | None:
        levels = [level for level in (self.left, self.right, self.case) if level is not None]
        return min(levels) if levels else None


@dataclass(slots=True)
class Advertisement:
    vendor_id: int
    battery: BatteryState
    classic_mac: str | None = None
    other_mac: str | None = None

    @property
    def model(self) -> str | None:
        return VENDOR_NAMES.get(self.vendor_id)


def pack_tlv(command: int, data: bytes = b"") -> bytes:
    """Encode one command/data TLV (``cmd, length, data``)."""
    if not 0 <= command <= 0xFF:
        raise ValueError(f"invalid command id: {command}")
    if len(data) > 0xFF:
        raise ValueError("TLV payload is too long")
    return bytes((command, len(data))) + bytes(data)


def pack_frame(*tlvs: bytes) -> bytes:
    """Wrap one or more TLVs in the QCY ``FF length`` frame."""
    body = b"".join(tlvs)
    if not body:
        raise ValueError("a QCY frame needs at least one TLV")
    if len(body) > 0xFF:
        raise ValueError("QCY frame is too long")
    return bytes((SOF, len(body))) + body


def parse_frames(data: bytes) -> list[tuple[int, bytes]]:
    """Decode all TLVs from one or more concatenated QCY frames.

    Notifications normally contain a single frame, but the settings
    characteristic may return a batch.  Unknown leading bytes are skipped so
    a malformed notice never desynchronises the parser permanently.
    """
    packets: list[tuple[int, bytes]] = []
    position = 0
    while position + 2 <= len(data):
        if data[position] != SOF:
            position += 1
            continue
        frame_length = data[position + 1]
        end = position + 2 + frame_length
        if end > len(data):
            break
        body = data[position + 2 : end]
        inner = 0
        while inner + 2 <= len(body):
            command = body[inner]
            payload_length = body[inner + 1]
            if inner + 2 + payload_length > len(body):
                break
            packets.append((command, bytes(body[inner + 2 : inner + 2 + payload_length])))
            inner += 2 + payload_length
        position = end
    return packets


def parse_frame(data: bytes) -> dict[int, bytes]:
    """Map command id to its payload (the last occurrence wins)."""
    return dict(parse_frames(data))


def encode_request(command: int) -> bytes:
    """Ask the earbuds to report a setting (``FE 01 cmd``)."""
    return pack_frame(pack_tlv(CMD_REQUEST, bytes((command & 0xFF,))))


def encode_noise_mode(value: int) -> bytes:
    value = normalise_noise_value(value)
    return pack_frame(pack_tlv(CMD_NOISE_MODE, bytes((value & 0xFF,))))


def encode_anc_setting(mode: int, sub_sense: int, level: int) -> bytes:
    return pack_frame(
        pack_tlv(CMD_ANC_SETTING, bytes((mode & 0xFF, sub_sense & 0xFF, level & 0xFF)))
    )


def encode_gaming(enabled: bool) -> bytes:
    return pack_frame(pack_tlv(CMD_GAMING, b"\x01" if enabled else b"\x02"))


def encode_sleep(enabled: bool) -> bytes:
    return pack_frame(pack_tlv(CMD_SLEEP, b"\x01" if enabled else b"\x02"))


def encode_reset() -> bytes:
    return pack_frame(pack_tlv(CMD_RESET))


def encode_equalizer(gains: list[float], eq_type: int = EQ_CUSTOM_TYPE) -> bytes:
    """Encode the raw ``0000000B`` payload: type byte + ten signed dB bytes."""
    if len(gains) != len(EQ_FREQUENCIES):
        raise ValueError(f"expected {len(EQ_FREQUENCIES)} EQ gains")
    payload = bytearray((eq_type & 0xFF,))
    for gain in gains:
        if not EQ_MIN_DB <= gain <= EQ_MAX_DB:
            raise ValueError(f"EQ gain {gain} is outside {EQ_MIN_DB}..{EQ_MAX_DB} dB")
        payload.append(int(round(gain)) & 0xFF)
    return bytes(payload)


def encode_key_map(mapping: dict[int, int]) -> bytes:
    """Encode raw ``0000000D`` pairs (event id, function id)."""
    payload = bytearray()
    for event in sorted(mapping):
        payload.extend((event & 0xFF, mapping[event] & 0xFF))
    return bytes(payload)


def parse_battery(data: bytes) -> BatteryState | None:
    if len(data) < 3:
        return None
    return BatteryState(
        left=data[0] & 0x7F,
        right=data[1] & 0x7F,
        case=data[2] & 0x7F,
        left_charging=bool(data[0] & 0x80),
        right_charging=bool(data[1] & 0x80),
        case_charging=bool(data[2] & 0x80),
    )


def parse_equalizer(data: bytes) -> tuple[int, list[float]] | None:
    if len(data) < 1 + len(EQ_FREQUENCIES):
        return None
    eq_type = data[0]
    gains = [float(_signed(byte)) for byte in data[1 : 1 + len(EQ_FREQUENCIES)]]
    return eq_type, gains


def parse_key_map(data: bytes) -> dict[int, int]:
    return {data[index]: data[index + 1] for index in range(0, len(data) - 1, 2)}


def parse_version(data: bytes) -> tuple[str | None, str | None]:
    if len(data) < 3:
        return None, None
    left = f"{data[0]}.{data[1]}.{data[2]}"
    right = f"{data[3]}.{data[4]}.{data[5]}" if len(data) >= 6 else None
    return left, right


def parse_gaming(data: bytes) -> bool | None:
    if not data:
        return None
    return data[0] == 1


def parse_sleep(data: bytes) -> bool | None:
    if not data:
        return None
    return data[0] == 1


def normalise_noise_value(value: int) -> int:
    value = int(value)
    if value in (0, 1):
        return 2
    if not 0 <= value <= 0xFF:
        raise ValueError(f"invalid noise mode: {value}")
    return value


def anc_mode_for_value(value: int | None):
    """Return the matching ANC tile entry for a live noise-mode value."""
    if value is None:
        return None
    try:
        value = normalise_noise_value(value)
    except (TypeError, ValueError):
        return None
    for mode in ANC_MODES:
        if mode[5] <= value <= mode[6]:
            return mode
    return None


def model_for_vendor_id(vendor_id: int) -> str | None:
    return VENDOR_NAMES.get(vendor_id)


def family_for_vendor_id(vendor_id: int) -> str | None:
    if vendor_id in (SA_TW_P10_VENDOR_ID, SA_TW_P20_VENDOR_ID, SA_TW_S10_VENDOR_ID):
        return "tws"
    if vendor_id == SA_HP_P10_VENDOR_ID:
        return "headphones"
    return None


def parse_advertisement(manufacturer_data) -> Advertisement | None:
    """Parse Spigen's company advertisement (vendor id, batteries, MACs)."""
    if not manufacturer_data:
        return None
    raw = None
    try:
        raw = manufacturer_data.get(SPIGEN_COMPANY_ID)
    except (AttributeError, TypeError):
        return None
    if raw is None:
        try:
            items = manufacturer_data.items()
        except AttributeError:
            return None
        for key, value in items:
            try:
                if int(key) == SPIGEN_COMPANY_ID:
                    raw = value
                    break
            except (TypeError, ValueError):
                continue
    if raw is None:
        return None
    try:
        data = bytes(int(byte) for byte in raw)
    except (TypeError, ValueError):
        return None
    if len(data) < 8:
        return None
    battery = BatteryState(
        left=data[5] & 0x7F,
        right=data[6] & 0x7F,
        case=data[7] & 0x7F,
        left_charging=bool(data[5] & 0x80),
        right_charging=bool(data[6] & 0x80),
        case_charging=bool(data[7] & 0x80),
    )
    return Advertisement(
        vendor_id=(data[0] << 8) | data[1],
        battery=battery,
        classic_mac=_mac_from(data, (12, 11, 13, 16, 15, 14)) if len(data) >= 18 else None,
        other_mac=_mac_from(data, (19, 18, 20, 23, 22, 21)) if len(data) >= 24 else None,
    )


def _mac_from(data: bytes, indexes: tuple[int, ...]) -> str:
    return ":".join(f"{data[index]:02X}" for index in indexes)


def _signed(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte
