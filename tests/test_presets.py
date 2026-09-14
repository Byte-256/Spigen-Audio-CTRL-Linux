import json

from spigen_audio_ctrl.app import MainWindow, PRESETS


class _PresetStore:
    _unique_custom_name = MainWindow._unique_custom_name
    _load_custom_presets = MainWindow._load_custom_presets
    _persist_custom_presets = MainWindow._persist_custom_presets


def _preset_store(path):
    store = _PresetStore()
    store.custom_presets = {}
    store.presets = {name: gains.copy() for name, gains in PRESETS.items()}
    store.presets_path = path
    return store


def test_custom_presets_survive_a_restart(tmp_path):
    path = tmp_path / "spigen-audio-ctrl" / "presets.json"
    first_run = _preset_store(path)
    first_run.custom_presets["Late Night"] = [-2.0, -1.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0, -1.0, -2.0]
    first_run._persist_custom_presets()

    second_run = _preset_store(path)
    second_run._load_custom_presets()

    assert second_run.presets["Late Night"] == first_run.custom_presets["Late Night"]
    assert json.loads(path.read_text())["version"] == 1


def test_invalid_saved_presets_are_ignored(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": {"Too loud": [20] * 10, "Too short": [0] * 3}}))
    store = _preset_store(path)

    store._load_custom_presets()

    assert "Too loud" not in store.presets
    assert "Too short" not in store.presets
