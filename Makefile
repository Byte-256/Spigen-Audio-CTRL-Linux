PREFIX ?= /usr
DESTDIR ?=
PYTHON_SITELIB ?= $(shell python3 -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base":"$(PREFIX)", "platbase":"$(PREFIX)"}))')

.PHONY: test run install uninstall

test:
	python3 -m pytest -q

run:
	python3 -m spigen_audio_ctrl

install:
	install -Dm755 spigen-audio-ctrl "$(DESTDIR)$(PREFIX)/bin/spigen-audio-ctrl"
	install -d "$(DESTDIR)$(PYTHON_SITELIB)/spigen_audio_ctrl"
	install -m644 spigen_audio_ctrl/*.py "$(DESTDIR)$(PYTHON_SITELIB)/spigen_audio_ctrl/"
	install -Dm644 packaging/io.github.spigen.AudioCTRL.desktop "$(DESTDIR)$(PREFIX)/share/applications/io.github.spigen.AudioCTRL.desktop"
	install -Dm644 Assets/logo.png "$(DESTDIR)$(PREFIX)/share/icons/hicolor/256x256/apps/spigen-audio-ctrl.png"
	install -Dm644 LICENSE "$(DESTDIR)$(PREFIX)/share/licenses/spigen-audio-ctrl/LICENSE"

uninstall:
	rm -f "$(DESTDIR)$(PREFIX)/bin/spigen-audio-ctrl"
	rm -rf "$(DESTDIR)$(PYTHON_SITELIB)/spigen_audio_ctrl"
	rm -f "$(DESTDIR)$(PREFIX)/share/applications/io.github.spigen.AudioCTRL.desktop"
	rm -f "$(DESTDIR)$(PREFIX)/share/icons/hicolor/256x256/apps/spigen-audio-ctrl.png"
	rm -rf "$(DESTDIR)$(PREFIX)/share/licenses/spigen-audio-ctrl"
