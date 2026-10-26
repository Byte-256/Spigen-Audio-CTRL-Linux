import socket
import threading
import time


class RfcommTransport:
    def __init__(self, address, channels=(4, 5)):
        self.address = address
        self.channels = channels
        self.sock = None
        self.channel = None

        self._rx_thread = None
        self._running = False

        self.on_data = None
        self.on_error = None

    def connect(self):
        last_error = None

        for channel in self.channels:
            print(
                f"[RFCOMM] Trying {self.address} channel {channel}..."
            )

            try:
                sock = socket.socket(
                    socket.AF_BLUETOOTH,
                    socket.SOCK_STREAM,
                    socket.BTPROTO_RFCOMM,
                )

                sock.settimeout(5)

                sock.connect((self.address, channel))

                sock.settimeout(0.5)

                self.sock = sock
                self.channel = channel
                self._running = True

                print(
                    f"[RFCOMM] Connected to "
                    f"{self.address} channel {channel}"
                )

                self._rx_thread = threading.Thread(
                    target=self._receive_loop,
                    daemon=True,
                )
                self._rx_thread.start()

                return True

            except Exception as e:
                last_error = e

                print(
                    f"[RFCOMM] Channel {channel} failed: {e}"
                )

                try:
                    sock.close()
                except Exception:
                    pass

        if self.on_error:
            self.on_error(last_error)

        return False

    def _receive_loop(self):
        while self._running and self.sock:
            try:
                data = self.sock.recv(4096)

                if not data:
                    print("[RFCOMM] Device closed connection")
                    break

                print(
                    "[RFCOMM RX]",
                    data.hex(" ")
                )

                if self.on_data:
                    self.on_data(data)

            except socket.timeout:
                continue

            except Exception as e:
                if self._running:
                    print(f"[RFCOMM] RX error: {e}")

                break

        self._running = False

    def send(self, data):
        if not self.sock:
            raise RuntimeError("RFCOMM is not connected")

        print(
            "[RFCOMM TX]",
            data.hex(" ")
        )

        self.sock.sendall(data)

    def close(self):
        self._running = False

        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass

            try:
                self.sock.close()
            except Exception:
                pass

        self.sock = None
