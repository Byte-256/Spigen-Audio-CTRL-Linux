"""GTK 4 desktop interface for Spigen Audio CTRL."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cairo
import dbus.mainloop.glib
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .bluetooth import BluetoothService  # noqa: E402
from .protocol import EQ_FREQUENCIES, HardwareInfo  # noqa: E402


PRESETS: dict[str, list[float]] = {
    "Harman Audiophile Target": [4, 2, 0, 0, 1, 3, 4, 2, 1, 0],
    "Default Flat": [0] * 10,
    "Bass Boost": [6, 4, 2, 0, -1, -2, -1, 0, 2, 3],
    "Pop": [-1, 1, 2, 3, 2, 0, 1, 2, 3, 2],
    "Rock": [4, 2, -1, -2, 0, 1, 2, 3, 4, 4],
    "Classical": [3, 2, 1, 0, 0, 1, 2, 3, 2, 1],
    "Vocal Enhance": [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
    "Gaming Footstep & Spatial": [2, 0, -2, 1, 3, 4, 4, 3, 2, 3],
}
HARMAN_GAINS = PRESETS["Harman Audiophile Target"]

ANC_MODES = (
    ("Deep ANC", "-43 dB", "Maximum isolation for flights, transit, and loud environments.", "◉", 65794),
    ("Adaptive ANC", "Dynamic", "Adapts filter strength to your surroundings and current ear seal.", "✦", 66816),
    ("Commuting", "Transit", "Targets low-frequency vehicle rumble on trains and buses.", "≋", 66048),
    ("Anti-Wind", "Outdoor", "Suppresses turbulence across the external microphones.", "〰", 66560),
    ("Transparency", "Ambient", "Stay aware of conversations and the world around you.", "◎", 196868),
    ("Off", "Passive", "Turns off ANC processing for the longest possible battery life.", "○", 131072),
)

FUNCTIONS = (
    (1, "Play / Pause"), (3, "Next Track"), (2, "Previous Track"),
    (5, "Volume Up"), (6, "Volume Down"), (4, "Voice Assistant"),
    (7, "Gaming Mode Toggle"), (8, "ANC Mode Switch"), (0, "Disabled"),
)


@dataclass(frozen=True, slots=True)
class RemapRow:
    badge: str
    key_name: str
    gesture: str
    key_number: int
    action: int
    default_function: int


REMAP_ROWS = (
    RemapRow("MFB", "Multi-Function Button", "Single Click", 7, 1, 1),
    RemapRow("MFB", "Multi-Function Button", "Double Click", 7, 2, 3),
    RemapRow("MFB", "Multi-Function Button", "Triple Click", 7, 3, 2),
    RemapRow("+", "Volume Up Button", "Single Click", 5, 1, 5),
    RemapRow("+", "Volume Up Button", "Long Press", 5, 5, 3),
    RemapRow("−", "Volume Down Button", "Single Click", 3, 1, 6),
    RemapRow("−", "Volume Down Button", "Long Press", 3, 5, 2),
)


CSS = b"""
window { background: #0b0b12; color: #f7f5ff; }
headerbar {
  background: #0b0b12; color: #f7f5ff;
  border-bottom: 1px solid rgba(255,255,255,.07); min-height: 46px;
}
headerbar button { background: transparent; border: none; }
.app-mark { color: #ff7759; font-weight: 900; font-size: 18px; }
.app-title { color: #f7f5ff; font-weight: 700; }
.status-pill {
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.09);
  border-radius: 999px; padding: 4px 10px; font-size: 12px;
}
.status-connected { color: #72e6b1; }
.status-disconnected { color: #ff8c75; }
.sidebar {
  background: #0e0e18; border-right: 1px solid rgba(255,255,255,.07);
  padding: 18px 14px;
}
.device-card {
  background: linear-gradient(145deg, #24203a, #151522);
  border: 1px solid rgba(187,160,255,.20); border-radius: 18px; padding: 16px;
}
.device-orb {
  background: rgba(255,119,89,.14); color: #ff896f; border-radius: 999px;
  min-width: 42px; min-height: 42px; font-size: 19px;
}
.device-name, .heading { color: #f9f7ff; font-weight: 700; }
.heading { font-size: 25px; }
.eyebrow { color: #b7a0ff; font-weight: 800; font-size: 11px; letter-spacing: 1.5px; }
.subtle { color: #9a97aa; }
.tiny { color: #777486; font-size: 11px; }
.battery { color: #84edbd; font-weight: 800; }
.battery-track trough {
  background: rgba(255,255,255,.09); border: none; border-radius: 999px; min-height: 6px;
}
.battery-track block.filled { background: #72e6b1; border-radius: 999px; border: none; }
.battery-track block.empty { background: transparent; border: none; }
.battery-low { color: #ff8c75; }
.battery-track.low block.filled { background: #ff7759; }
.nav-section { color: #656274; font-size: 10px; font-weight: 800; letter-spacing: 1.4px; margin: 8px 10px 0; }
.nav-button {
  background: transparent; border: none; box-shadow: none; padding: 10px 11px;
  border-radius: 12px; color: #9a97aa;
}
.nav-button:hover { background: rgba(255,255,255,.045); color: #ece9f5; }
.nav-button:checked {
  background: linear-gradient(90deg, rgba(128,91,255,.24), rgba(255,119,89,.10));
  color: #fff; font-weight: 700;
}
.nav-icon { color: #b7a0ff; font-size: 17px; min-width: 26px; }
.content { padding: 26px 32px 30px; }
.card {
  background: #13131d; border: 1px solid rgba(255,255,255,.075);
  border-radius: 16px; padding: 16px;
}
.anc-tile {
  background: #13131d; border: 1px solid rgba(255,255,255,.075);
  border-radius: 16px; padding: 15px; box-shadow: none;
}
.anc-tile:hover { background: #181824; border-color: rgba(183,160,255,.30); }
.anc-tile:checked {
  border-color: #9c7cff; background: linear-gradient(145deg, #211c39, #171724);
  box-shadow: inset 3px 0 #9c7cff;
}
.mode-icon {
  background: rgba(156,124,255,.12); color: #c5b3ff; border-radius: 999px;
  font-size: 17px; min-width: 35px; min-height: 35px;
}
.anc-tile:checked .mode-icon { background: #9c7cff; color: white; }
.tag {
  background: rgba(255,255,255,.055); border-radius: 999px; padding: 3px 8px;
  color: #aaa6b8; font-size: 10px; font-weight: 700;
}
.gaming-card { background: linear-gradient(90deg, #171525, #17131b); }
.accent-icon {
  background: rgba(255,119,89,.13); color: #ff896f; border-radius: 12px;
  min-width: 42px; min-height: 42px; font-size: 18px;
}
.remap-row { padding: 12px 10px; border-bottom: 1px solid rgba(255,255,255,.06); }
.badge {
  background: rgba(156,124,255,.14); color: #c5b3ff; border-radius: 9px;
  padding: 5px 8px; font-weight: 800; min-width: 28px;
}
.primary-button { background: #9c7cff; color: white; border: none; font-weight: 700; }
.danger-button { color: #ff9b86; }
button { border-radius: 10px; }
combobox button { background: #191923; border: 1px solid rgba(255,255,255,.09); }
switch:checked { background: #9c7cff; }
scale trough { background: rgba(255,255,255,.10); }
scale highlight { background: #9c7cff; }
scale slider { background: #f7f5ff; border: 3px solid #9c7cff; min-width: 13px; min-height: 13px; }
checkbutton check:checked { background: #ffb552; color: #111118; }
"""


def label(text: str, css_class: str | None = None, wrap: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    if css_class:
        widget.add_css_class(css_class)
    return widget


def centered_label(text: str, css_class: str) -> Gtk.Label:
    widget = label(text, css_class)
    widget.set_xalign(.5)
    widget.set_yalign(.5)
    return widget


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(application=application, title="Spigen Audio CTRL")
        self.set_default_size(1020, 680)
        self.set_size_request(880, 580)
        self.service = BluetoothService()
        self.service.on_connection = self.on_connection
        self.service.on_battery = self.on_battery
        self.service.on_hardware_info = self.on_hardware_info
        self.gains = [0.0] * 10
        self.scales: list[Gtk.Scale] = []
        self.gain_labels: list[Gtk.Label] = []
        self.remap_combos: dict[str, Gtk.ComboBoxText] = {}
        self.eq_timeout: int | None = None
        self.graph_band = -1
        self.graph_start_y = 0.0
        self.graph_start_gain = 0.0
        self.updating_hardware = False
        self.custom_presets: dict[str, list[float]] = {}
        self.presets = {name: gains.copy() for name, gains in PRESETS.items()}
        self.presets_path = Path(GLib.get_user_config_dir()) / "spigen-audio-ctrl" / "presets.json"
        self._load_custom_presets()

        self._build_ui()
        self.connect("close-request", self._on_close)
        GLib.idle_add(self.service.connect)

    def _build_ui(self) -> None:
        header = Gtk.HeaderBar()
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        title.append(centered_label("S", "app-mark"))
        title.append(label("SPIGEN AUDIO CTRL", "app-title"))
        self.status_label = label("●  Connecting…", "status-disconnected")
        self.status_label.add_css_class("status-pill")
        title.append(self.status_label)
        header.set_title_widget(title)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child(root)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar.add_css_class("sidebar")
        sidebar.set_size_request(250, -1)
        root.append(sidebar)

        device_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        device_card.add_css_class("device-card")
        device_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        device_top.append(centered_label("♫", "device-orb"))
        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        identity.set_hexpand(True)
        self.device_name_label = label("SA-HP P10", "device-name")
        identity.append(self.device_name_label)
        self.device_subtitle = label("Looking for headphones", "subtle")
        self.device_subtitle.set_wrap(True)
        identity.append(self.device_subtitle)
        device_top.append(identity)
        device_card.append(device_top)
        self.battery_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.battery_box.set_visible(False)
        battery_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        battery_row.append(label("BATTERY", "tiny"))
        spacer_label = label("")
        spacer_label.set_hexpand(True)
        battery_row.append(spacer_label)
        self.battery_label = label("—", "battery")
        battery_row.append(self.battery_label)
        self.battery_box.append(battery_row)
        self.battery_bar = Gtk.LevelBar.new_for_interval(0, 100)
        self.battery_bar.add_css_class("battery-track")
        self.battery_bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
        self.battery_box.append(self.battery_bar)
        device_card.append(self.battery_box)
        self.firmware_label = label("", "tiny")
        self.firmware_label.set_visible(False)
        device_card.append(self.firmware_label)
        sidebar.append(device_card)

        # No perpetual effects or frame clocks: pages only redraw after user/device events.
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        sidebar.append(label("CONTROL DECK", "nav-section"))
        nav_group: Gtk.ToggleButton | None = None
        for icon, title_text, page_name in (
            ("◉", "Noise Control", "anc"),
            ("⌁", "Equalizer", "eq"),
            ("⌨", "Button Mapping", "buttons"),
        ):
            button = Gtk.ToggleButton()
            button.add_css_class("nav-button")
            nav_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            nav_content.append(centered_label(icon, "nav-icon"))
            nav_content.append(label(title_text))
            button.set_child(nav_content)
            if nav_group:
                button.set_group(nav_group)
            else:
                nav_group = button
                button.set_active(True)
            button.connect("toggled", self._on_nav, page_name)
            sidebar.append(button)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        sidebar.append(spacer)
        sidebar.append(label("Bluetooth Low Energy • Event driven", "tiny"))
        self.connect_button = Gtk.Button(label="Disconnect")
        self.connect_button.add_css_class("danger-button")
        self.connect_button.connect("clicked", self._on_connect_clicked)
        sidebar.append(self.connect_button)

        root.append(self.stack)
        self.stack.add_named(self._build_anc_page(), "anc")
        self.stack.add_named(self._build_eq_page(), "eq")
        self.stack.add_named(self._build_buttons_page(), "buttons")
        self.stack.set_visible_child_name("anc")

    @staticmethod
    def _page(title_text: str, subtitle: str) -> tuple[Gtk.Box, Gtk.Box]:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.add_css_class("content")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        heading.append(label("YOUR SOUND, YOUR RULES", "eyebrow"))
        heading.append(label(title_text, "heading"))
        heading.append(label(subtitle, "subtle"))
        page.append(heading)
        return page, heading

    def _build_anc_page(self) -> Gtk.Widget:
        page, _ = self._page("Noise Control", "Select active acoustic isolation or ambient transparency profile")
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, column_homogeneous=True, row_homogeneous=True)
        grid.set_vexpand(True)
        group = None
        for index, (name, tag, description, icon, anc_id) in enumerate(ANC_MODES):
            button = Gtk.ToggleButton()
            button.add_css_class("anc-tile")
            if group:
                button.set_group(group)
            else:
                group = button
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            top.append(centered_label(icon, "mode-icon"))
            title = label(name, "device-name")
            title.set_hexpand(True)
            top.append(title)
            top.append(label(tag, "tag"))
            box.append(top)
            box.append(label(description, "subtle", wrap=True))
            button.set_child(box)
            button.connect("toggled", self._on_anc_toggled, anc_id)
            grid.attach(button, index % 3, index // 3, 1, 1)
            if index == 1:
                button.set_active(True)
        page.append(grid)

        gaming_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        gaming_card.add_css_class("card")
        gaming_card.add_css_class("gaming-card")
        gaming_card.append(centered_label("⚡", "accent-icon"))
        gaming_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        gaming_text.set_hexpand(True)
        gaming_text.append(label("Low-Latency Gaming Mode", "device-name"))
        gaming_text.append(label("Reduces wireless audio transmission delay to ~45 ms", "subtle"))
        gaming_card.append(gaming_text)
        self.gaming_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.gaming_switch.connect("notify::active", self._on_gaming_toggled)
        gaming_card.append(self.gaming_switch)
        page.append(gaming_card)
        return page

    def _build_eq_page(self) -> Gtk.Widget:
        page, heading = self._page("10-Band DSP Equalizer", "Fine-tune 16-bit parametric hardware filters stored on-chip")
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading.append(controls)
        self.preset_combo = Gtk.ComboBoxText()
        for name in self.presets:
            self.preset_combo.append_text(name)
        self.preset_combo.set_active(0)
        self.preset_combo.connect("changed", self._on_preset_changed)
        controls.append(self.preset_combo)
        save = Gtk.Button(label="Save as Preset")
        save.add_css_class("primary-button")
        save.connect("clicked", self._save_preset)
        controls.append(save)
        reset = Gtk.Button(label="Reset")
        reset.connect("clicked", lambda _button: self._select_preset("Default Flat"))
        controls.append(reset)
        self.preset_status = label("", "tiny")
        self.preset_status.set_visible(False)
        heading.append(self.preset_status)

        self.eq_graph = Gtk.DrawingArea(content_height=165)
        self.eq_graph.add_css_class("card")
        self.eq_graph.set_draw_func(self._draw_eq)
        click = Gtk.GestureClick()
        click.connect("pressed", self._graph_pressed)
        self.eq_graph.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._graph_drag_begin)
        drag.connect("drag-update", self._graph_drag_update)
        self.eq_graph.add_controller(drag)
        page.append(self.eq_graph)

        rack = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        rack.add_css_class("card")
        for index, frequency in enumerate(EQ_FREQUENCIES):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            gain_label = label("+0dB", "device-name")
            gain_label.set_xalign(0.5)
            self.gain_labels.append(gain_label)
            column.append(gain_label)
            adjustment = Gtk.Adjustment(value=0, lower=-8, upper=8, step_increment=.5, page_increment=1)
            scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adjustment, draw_value=False)
            scale.set_inverted(True)
            scale.set_size_request(-1, 125)
            scale.set_vexpand(True)
            scale.connect("value-changed", self._on_scale_changed, index)
            self.scales.append(scale)
            column.append(scale)
            text = f"{frequency / 1000:g}k" if frequency >= 1000 else str(frequency)
            freq_label = label(text, "subtle")
            freq_label.set_xalign(0.5)
            column.append(freq_label)
            rack.attach(column, index, 0, 1, 1)
        page.append(rack)

        self.harman_check = Gtk.CheckButton(label="Show Harman Target 2019 reference curve")
        self.harman_check.set_active(True)
        self.harman_check.connect("toggled", lambda _button: self.eq_graph.queue_draw())
        page.append(self.harman_check)
        self._apply_preset("Harman Audiophile Target", send=False)
        return page

    def _build_buttons_page(self) -> Gtk.Widget:
        page, _ = self._page("Button Remapping", "Customize gestures and physical key actions stored in headphone memory")
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows_box.add_css_class("card")
        scroller.set_child(rows_box)
        for row in REMAP_ROWS:
            line = Gtk.Grid(column_spacing=16)
            line.add_css_class("remap-row")
            left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
            left.set_hexpand(True)
            left.append(label(row.badge, "badge"))
            left.append(label(row.key_name, "device-name"))
            line.attach(left, 0, 0, 1, 1)
            gesture = label(row.gesture, "subtle")
            gesture.set_size_request(125, -1)
            line.attach(gesture, 1, 0, 1, 1)
            combo = Gtk.ComboBoxText()
            for function_id, function_name in FUNCTIONS:
                combo.append(str(function_id), function_name)
            combo.set_active_id(str(row.default_function))
            combo.set_size_request(190, -1)
            combo.connect("changed", self._on_remap_changed, row)
            self.remap_combos[f"{row.key_number}_{row.action}"] = combo
            line.attach(combo, 2, 0, 1, 1)
            rows_box.append(line)
        page.append(scroller)
        return page

    def _on_nav(self, button: Gtk.ToggleButton, page_name: str) -> None:
        if button.get_active() and hasattr(self, "stack"):
            self.stack.set_visible_child_name(page_name)
            if page_name == "eq":
                self.eq_graph.queue_draw()

    def _on_connect_clicked(self, _button: Gtk.Button) -> None:
        if self.service.connected:
            self.service.disconnect()
        else:
            self.service.connect()

    def on_connection(self, connected: bool, status: str) -> bool:
        if connected:
            self.status_label.set_text("●  Connected")
            self.status_label.remove_css_class("status-disconnected")
            self.status_label.add_css_class("status-connected")
            self.device_name_label.set_text(status)
            self.device_subtitle.set_text("Connected over Bluetooth LE")
            self.connect_button.set_label("Disconnect")
            self.connect_button.remove_css_class("primary-button")
            self.connect_button.add_css_class("danger-button")
        else:
            connecting = status.startswith("Connecting")
            failed = status.startswith("Error:")
            state_text = "●  Connecting…" if connecting else ("●  Connection failed" if failed else "●  Disconnected")
            self.status_label.set_text(state_text)
            self.status_label.remove_css_class("status-connected")
            self.status_label.add_css_class("status-disconnected")
            if connecting:
                self.device_subtitle.set_text("Connecting…")
            elif failed:
                self.device_subtitle.set_text(status.removeprefix("Error: "))
            else:
                self.device_subtitle.set_text("No device attached")
            self.connect_button.set_label("Connecting…" if connecting else ("Try Again" if failed else "Pair Headphones"))
            self.connect_button.remove_css_class("danger-button")
            self.connect_button.add_css_class("primary-button")
            self.battery_box.set_visible(False)
            self.firmware_label.set_visible(False)
        return GLib.SOURCE_REMOVE

    def on_battery(self, level: int) -> bool:
        self.battery_label.set_text(f"{level}%")
        self.battery_bar.set_value(level)
        low = level <= 20
        if low:
            self.battery_label.add_css_class("battery-low")
            self.battery_bar.add_css_class("low")
        else:
            self.battery_label.remove_css_class("battery-low")
            self.battery_bar.remove_css_class("low")
        self.battery_box.set_visible(True)
        return GLib.SOURCE_REMOVE

    def on_hardware_info(self, info: HardwareInfo) -> bool:
        self.updating_hardware = True
        try:
            if info.device_name:
                self.device_name_label.set_text(info.device_name)
            if info.firmware:
                self.firmware_label.set_text(f"Firmware  {info.firmware}")
                self.firmware_label.set_visible(True)
            if info.gaming_mode is not None:
                self.gaming_switch.set_active(info.gaming_mode)
            for key, function in info.key_settings.items():
                combo = self.remap_combos.get(key)
                if combo:
                    combo.set_active_id(str(function if function != 127 else 0))
        finally:
            self.updating_hardware = False
        return GLib.SOURCE_REMOVE

    def _on_anc_toggled(self, button: Gtk.ToggleButton, anc_id: int) -> None:
        if button.get_active() and self.service.connected:
            self.service.set_anc_mode(anc_id)

    def _on_gaming_toggled(self, switch: Gtk.Switch, _param) -> None:
        if not self.updating_hardware and self.service.connected:
            self.service.set_gaming_mode(switch.get_active())

    def _on_scale_changed(self, scale: Gtk.Scale, index: int) -> None:
        gain = round(scale.get_value() * 2) / 2
        self.gains[index] = gain
        self.gain_labels[index].set_text(f"{gain:+g}dB")
        self.eq_graph.queue_draw()
        self._debounce_eq()

    def _debounce_eq(self) -> None:
        if self.eq_timeout is not None:
            GLib.source_remove(self.eq_timeout)
        # Coalesce slider/graph input into one radio write after interaction settles.
        self.eq_timeout = GLib.timeout_add(300, self._send_eq)

    def _send_eq(self) -> bool:
        self.eq_timeout = None
        if self.service.connected:
            self.service.set_equalizer(self.gains.copy())
        return GLib.SOURCE_REMOVE

    def _on_preset_changed(self, combo: Gtk.ComboBoxText) -> None:
        name = combo.get_active_text()
        if name:
            self._apply_preset(name)

    def _apply_preset(self, name: str, send: bool = True) -> None:
        gains = self.presets.get(name)
        if not gains:
            return
        for index, gain in enumerate(gains):
            self.gains[index] = float(gain)
            if index < len(self.scales):
                self.scales[index].set_value(gain)
        if send:
            self._debounce_eq()

    def _select_preset(self, name: str) -> None:
        names = list(self.presets)
        if name in names:
            self.preset_combo.set_active(names.index(name))

    def _save_preset(self, _button: Gtk.Button) -> None:
        """Ask for a name; the response callback performs the actual disk write."""
        number = 1
        while f"Custom {number}" in self.presets:
            number += 1
        dialog = Gtk.Dialog(title="Save equalizer preset", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save_button = dialog.add_button("Save Preset", Gtk.ResponseType.ACCEPT)
        save_button.add_css_class("suggested-action")
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.append(label("Give this sound a name", "device-name"))
        content.append(label("Your current 10-band EQ values will be available next time you open the app.", "subtle", wrap=True))
        entry = Gtk.Entry(text=f"Custom {number}")
        entry.set_activates_default(True)
        entry.select_region(0, -1)
        dialog.set_default_widget(save_button)
        content.append(entry)
        dialog.connect("response", self._on_save_preset_response, entry)
        dialog.present()

    def _on_save_preset_response(self, dialog: Gtk.Dialog, response: int, entry: Gtk.Entry) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            requested_name = entry.get_text().strip()
            if requested_name:
                name = self._unique_custom_name(requested_name)
                self.custom_presets[name] = self.gains.copy()
                self.presets[name] = self.gains.copy()
                self.preset_combo.append_text(name)
                self.preset_combo.set_active(len(self.presets) - 1)
                try:
                    self._persist_custom_presets()
                    self.preset_status.set_text(f"Saved “{name}” on this computer")
                except OSError as error:
                    self.preset_status.set_text(f"Could not save preset: {error}")
                self.preset_status.set_visible(True)
        dialog.destroy()

    def _unique_custom_name(self, requested_name: str) -> str:
        """Preserve built-ins and existing presets rather than silently overwriting them."""
        if requested_name not in self.presets:
            return requested_name
        number = 2
        while f"{requested_name} {number}" in self.presets:
            number += 1
        return f"{requested_name} {number}"

    def _load_custom_presets(self) -> None:
        try:
            document = json.loads(self.presets_path.read_text(encoding="utf-8"))
            saved = document.get("presets", {}) if isinstance(document, dict) else {}
            for name, gains in saved.items():
                if (
                    isinstance(name, str)
                    and name.strip()
                    and isinstance(gains, list)
                    and len(gains) == 10
                    and all(isinstance(gain, (int, float)) and -8 <= gain <= 8 for gain in gains)
                ):
                    clean_gains = [float(gain) for gain in gains]
                    clean_name = self._unique_custom_name(name.strip())
                    self.custom_presets[clean_name] = clean_gains
                    self.presets[clean_name] = clean_gains.copy()
        except (OSError, ValueError, TypeError):
            # Missing or malformed user data should never prevent the controller opening.
            return

    def _persist_custom_presets(self) -> None:
        self.presets_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.presets_path.with_suffix(".tmp")
        document = {"version": 1, "presets": self.custom_presets}
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.presets_path)

    @staticmethod
    def _node(index: int, gain: float, width: float, height: float) -> tuple[float, float]:
        padding_x, padding_y = 36.0, 22.0
        x = padding_x + index / 9 * max(1, width - padding_x * 2)
        y = padding_y + (8 - gain) / 16 * max(1, height - padding_y * 2)
        return x, y

    def _curve(self, context: cairo.Context, gains: list[float], width: float, height: float) -> None:
        points = [self._node(i, gain, width, height) for i, gain in enumerate(gains)]
        context.move_to(*points[0])
        for index in range(9):
            p0 = points[index - 1] if index else points[index]
            p1, p2 = points[index], points[index + 1]
            p3 = points[index + 2] if index + 2 < 10 else p2
            cp1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            cp2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            context.curve_to(*cp1, *cp2, *p2)

    def _draw_eq(self, _area: Gtk.DrawingArea, context: cairo.Context, width: int, height: int) -> None:
        for db in (-8, -4, 0, 4, 8):
            y = self._node(0, db, width, height)[1]
            context.set_source_rgba(1, 1, 1, .16 if db == 0 else .07)
            context.set_line_width(1)
            context.move_to(27, y)
            context.line_to(width - 27, y)
            context.stroke()
        for index in range(10):
            x, _ = self._node(index, 0, width, height)
            context.set_source_rgba(1, 1, 1, .05)
            context.move_to(x, 16)
            context.line_to(x, height - 16)
            context.stroke()
        if self.harman_check.get_active():
            self._curve(context, HARMAN_GAINS, width, height)
            context.set_source_rgb(.9, .66, .24)
            context.set_line_width(1.8)
            context.set_dash((5, 4))
            context.stroke()
            context.set_dash(())
        self._curve(context, self.gains, width, height)
        context.set_source_rgba(1, 1, 1, .12)
        context.line_to(*self._node(9, -8, width, height))
        context.line_to(*self._node(0, -8, width, height))
        context.close_path()
        context.fill()
        self._curve(context, self.gains, width, height)
        context.set_source_rgb(1, 1, 1)
        context.set_line_width(2)
        context.stroke()
        for index, gain in enumerate(self.gains):
            x, y = self._node(index, gain, width, height)
            context.arc(x, y, 5.5, 0, math.tau)
            context.set_source_rgb(1, 1, 1)
            context.fill()
            context.arc(x, y, 2.5, 0, math.tau)
            context.set_source_rgb(.04, .04, .04)
            context.fill()

    def _band_at(self, x: float) -> int:
        width = self.eq_graph.get_width()
        return max(0, min(9, round((x - 36) / max(1, width - 72) * 9)))

    def _gain_at(self, y: float) -> float:
        height = self.eq_graph.get_height()
        value = 8 - (y - 22) / max(1, height - 44) * 16
        return max(-8, min(8, round(value * 2) / 2))

    def _set_graph_gain(self, index: int, value: float) -> None:
        self.scales[index].set_value(max(-8, min(8, round(value * 2) / 2)))

    def _graph_pressed(self, _gesture: Gtk.GestureClick, _count: int, x: float, y: float) -> None:
        self._set_graph_gain(self._band_at(x), self._gain_at(y))

    def _graph_drag_begin(self, _gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        self.graph_band = self._band_at(x)
        self.graph_start_y = y
        self.graph_start_gain = self.gains[self.graph_band]

    def _graph_drag_update(self, _gesture: Gtk.GestureDrag, _offset_x: float, offset_y: float) -> None:
        height = self.eq_graph.get_height()
        gain_delta = -offset_y / max(1, height - 44) * 16
        self._set_graph_gain(self.graph_band, self.graph_start_gain + gain_delta)

    def _on_remap_changed(self, combo: Gtk.ComboBoxText, row: RemapRow) -> None:
        active = combo.get_active_id()
        if active is not None and not self.updating_hardware and self.service.connected:
            self.service.set_key_mapping(row.key_number, row.action, int(active))

    def _on_close(self, _window) -> bool:
        if self.eq_timeout is not None:
            GLib.source_remove(self.eq_timeout)
        self.service.close()
        return False


class SpigenApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.spigen.AudioCTRL", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        window = self.props.active_window or MainWindow(self)
        window.present()


def main() -> int:
    dbus.mainloop.glib.threads_init()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    return SpigenApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
