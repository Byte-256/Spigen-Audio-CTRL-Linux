import struct

import pytest

from spigen_audio_ctrl.protocol import (
    encode_anc,
    encode_equalizer,
    encode_gaming_mode,
    encode_key_mapping,
    pack_rcsp,
    parse_hardware_info,
    parse_target_battery,
)


def test_pack_rcsp_frame():
    assert pack_rcsp(0xC0, b"\x02\x05\x02", 9) == bytes.fromhex("FEDCBAC0C0000409020502EF")


@pytest.mark.parametrize(
    ("anc_id", "payload"),
    [
        (65794, "1703010102"),
        (66816, "1703010500"),
        (66048, "1703010200"),
        (66560, "1703010400"),
        (196868, "1703030104"),
        (131072, "1703020000"),
    ],
)
def test_all_anc_modes(anc_id, payload):
    packet = encode_anc(anc_id, 2)
    assert packet[:7] == bytes.fromhex("FEDCBAC0FF0006")
    assert packet[7:-1] == b"\x02" + bytes.fromhex(payload)
    assert packet[-1] == 0xEF


def test_gaming_and_key_mapping():
    assert encode_gaming_mode(True, 4)[7:-1] == bytes((4, 2, 5, 2))
    assert encode_gaming_mode(False, 4)[7:-1] == bytes((4, 2, 5, 1))
    assert encode_key_mapping(7, 2, 0, 5)[7:-1] == bytes((5, 4, 2, 7, 2, 127))


def test_equalizer_layout_matches_headphone_dsp():
    gains = [4, 2, 0, 0, 1, 3, 4, 2, 1, 0]
    packet = encode_equalizer(gains, sequence=8)
    assert len(packet) == 74
    assert packet[:8] == bytes.fromhex("FEDCBAC0FF004208")
    payload = packet[8:-1]
    assert payload[:5] == bytes((0x20, 63, 126, 0, 0))
    frequency, gain, q_value = struct.unpack_from("<Hhh", payload, 5)
    assert (frequency, gain, q_value) == (60, 400, 40)
    assert packet[-1] == 0xEF


def test_equalizer_rejects_invalid_input():
    with pytest.raises(ValueError):
        encode_equalizer([0] * 9)
    with pytest.raises(ValueError):
        encode_equalizer([0] * 9 + [8.5])


def test_hardware_info_tlvs():
    header = bytes(13)
    name = b"P10\0"
    keys = bytes((7, 1, 3, 5, 5, 2))
    firmware = b"1.2.3\0"
    data = (
        header
        + bytes((len(name) + 1, 1)) + name
        + bytes((len(keys) + 1, 2)) + keys
        + bytes((2, 5, 2))
        + bytes((len(firmware) + 1, 9)) + firmware
        + b"\xef"
    )
    info = parse_hardware_info(data)
    assert info.device_name == "P10"
    assert info.key_settings == {"7_1": 3, "5_5": 2}
    assert info.gaming_mode is True
    assert info.firmware == "1.2.3"


def test_target_info_battery():
    data = pack_rcsp(0x03, bytes((2, 1, 73)), 1)
    assert parse_target_battery(data) == 73
    assert parse_target_battery(pack_rcsp(0xC1, b"", 1)) is None
