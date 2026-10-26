from spigen_audio_ctrl import qcy_protocol


def test_noise_mode_frame_and_normal_mode():
    assert qcy_protocol.encode_noise_mode(99) == bytes.fromhex("FF030C0163")
    assert qcy_protocol.encode_noise_mode(164) == bytes.fromhex("FF030C01A4")
    assert qcy_protocol.encode_noise_mode(0) == bytes.fromhex("FF030C0102")


def test_request_and_gaming_sleep_frames():
    assert qcy_protocol.encode_request(12) == bytes.fromhex("FF03FE010C")
    assert qcy_protocol.encode_gaming(True) == bytes.fromhex("FF03090101")
    assert qcy_protocol.encode_gaming(False) == bytes.fromhex("FF03090102")
    assert qcy_protocol.encode_sleep(True) == bytes.fromhex("FF03100101")
    assert qcy_protocol.encode_sleep(False) == bytes.fromhex("FF03100102")


def test_parse_single_and_concatenated_frames():
    assert qcy_protocol.parse_frame(bytes.fromhex("FF030C0163")) == {12: b"\x63"}
    assert qcy_protocol.parse_frame(
        bytes.fromhex("FF030C0163") + bytes.fromhex("FF03090101")
    ) == {12: b"\x63", 9: b"\x01"}
    assert qcy_protocol.parse_frame(bytes.fromhex("FF060C0163090101")) == {
        12: b"\x63",
        9: b"\x01",
    }
    assert qcy_protocol.parse_frame(b"\x00\x01garbage") == {}


def test_battery_charging_and_levels():
    state = qcy_protocol.parse_battery(bytes([0x80 | 91, 88, 0x80 | 77]))
    assert state is not None
    assert (state.left, state.right, state.case) == (91, 88, 77)
    assert state.left_charging and not state.right_charging and state.case_charging
    assert state.lowest() == 77
    assert qcy_protocol.parse_battery(b"\x01") is None


def test_equalizer_roundtrip_uses_custom_type():
    gains = [-8, -6, -1, 0, 1, 2, 3, 4, 6, 8]
    payload = qcy_protocol.encode_equalizer(gains)
    assert payload[0] == qcy_protocol.EQ_CUSTOM_TYPE
    assert len(payload) == 11
    assert qcy_protocol.parse_equalizer(payload) == (
        qcy_protocol.EQ_CUSTOM_TYPE,
        [float(gain) for gain in gains],
    )


def test_equalizer_rejects_out_of_range_gains():
    try:
        qcy_protocol.encode_equalizer([9] + [0] * 9)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range EQ gain was accepted")


def test_key_map_roundtrip():
    mapping = {1: 1, 2: 0, 3: 4, 4: 1, 5: 3, 6: 11}
    payload = qcy_protocol.encode_key_map(mapping)
    assert payload == bytes((1, 1, 2, 0, 3, 4, 4, 1, 5, 3, 6, 11))
    assert qcy_protocol.parse_key_map(payload) == mapping


def test_anc_mode_ranges():
    assert qcy_protocol.anc_mode_for_value(99)[0] == "Noisy"
    assert qcy_protocol.anc_mode_for_value(65)[0] == "Indoor"
    assert qcy_protocol.anc_mode_for_value(82)[0] == "Commuting"
    assert qcy_protocol.anc_mode_for_value(113)[0] == "Anti-Wind"
    assert qcy_protocol.anc_mode_for_value(164)[0] == "Transparency"
    assert qcy_protocol.anc_mode_for_value(2)[0] == "Normal"
    assert qcy_protocol.anc_mode_for_value(1234) is None


def test_version_and_vendor_family():
    assert qcy_protocol.parse_version(bytes((1, 2, 3, 4, 5, 6))) == ("1.2.3", "4.5.6")
    assert qcy_protocol.family_for_vendor_id(qcy_protocol.SA_TW_P10_VENDOR_ID) == "tws"
    assert qcy_protocol.family_for_vendor_id(qcy_protocol.SA_HP_P10_VENDOR_ID) == "headphones"
    assert qcy_protocol.family_for_vendor_id(1) is None


def test_spigen_advertisement_battery_and_mac():
    data = bytearray(24)
    data[0:2] = qcy_protocol.SA_TW_P10_VENDOR_ID.to_bytes(2, "big")
    data[5] = 0x80 | 91
    data[6] = 88
    data[7] = 77
    # Vendor layout: 12,11,13,16,15,14 -> 84:AC:60:3D:7F:DA
    data[11] = 0xAC
    data[12] = 0x84
    data[13] = 0x60
    data[14] = 0xDA
    data[15] = 0x7F
    data[16] = 0x3D
    advertisement = qcy_protocol.parse_advertisement({qcy_protocol.SPIGEN_COMPANY_ID: data})
    assert advertisement is not None
    assert advertisement.vendor_id == qcy_protocol.SA_TW_P10_VENDOR_ID
    assert advertisement.model == "SA-TW P10"
    assert advertisement.classic_mac == "84:AC:60:3D:7F:DA"
    assert (advertisement.battery.left, advertisement.battery.right, advertisement.battery.case) == (91, 88, 77)
    assert advertisement.battery.left_charging
    assert qcy_protocol.parse_advertisement({}) is None
    assert qcy_protocol.parse_advertisement({"21036": bytes(8)}) is not None
