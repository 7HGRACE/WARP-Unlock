"""Optional real SmartDNS regression, using only temporary files and loopback UDP.

Run with SMARTDNS_BIN=/path/to/smartdns to select Debian's extracted 46.1 binary.
No external DNS, system service, routing, or WireGuard interface is required.
"""
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest

from test_installer import bash


SMARTDNS = os.environ.get("SMARTDNS_BIN") or shutil.which("smartdns")


def question(name, qtype):
    labels = b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\0"
    return struct.pack("!6H", 1234, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", qtype, 1)


@unittest.skipUnless(SMARTDNS, "Set SMARTDNS_BIN or install SmartDNS to run the real DNS test")
class SmartDNSRuntimeTests(unittest.TestCase):
    def test_generated_config_resolves_a_and_aaaa(self):
        stop = threading.Event()
        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        upstream.bind(("127.0.0.1", 0))
        upstream.settimeout(0.1)
        port = upstream.getsockname()[1]

        def serve():
            while not stop.is_set():
                try:
                    query, client = upstream.recvfrom(4096)
                except socket.timeout:
                    continue
                offset = 12
                while query[offset]:
                    offset += 1 + query[offset]
                end = offset + 5
                qtype = struct.unpack("!H", query[offset + 1:offset + 3])[0]
                if qtype not in (1, 28):
                    continue
                address = ipaddress.ip_address("192.0.2.123" if qtype == 1 else "2001:db8::123").packed
                response = query[:2] + struct.pack("!5H", 0x8180, 1, 1, 0, 0) + query[12:end]
                response += b"\xc0\x0c" + struct.pack("!HHIH", qtype, 1, 60, len(address)) + address
                upstream.sendto(response, client)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        process = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                result = bash('''STACK_MODE=none
SMARTDNS_CONF="$PWD/smartdns.conf"
mkdir() { :; }
touch() { :; }
chmod() { :; }
write_smartdns_conf
''', directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                # Redirect only bind/upstream/log paths; keep generated directives.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    listen_port = reservation.getsockname()[1]
                config = (path / "smartdns.conf").read_text()
                config = config.replace("bind [127.0.0.1]:53", f"bind [127.0.0.1]:{listen_port}")
                config = config.replace("server 1.1.1.1", f"server 127.0.0.1:{port}")
                config = config.replace("server 8.8.8.8", f"server 127.0.0.1:{port}")
                config = config.replace("/var/log/smartdns.log", str(path / "smartdns.log"))
                config += "\ncache-persist no\nspeed-check-mode none\n"
                (path / "smartdns.conf").write_text(config)
                with (path / "process.log").open("w+") as logfile:
                    process = subprocess.Popen(
                        [SMARTDNS, "-f", "-c", str(path / "smartdns.conf"), "-p", str(path / "smartdns.pid")],
                        cwd=directory, stdout=logfile, stderr=logfile,
                    )
                    try:
                        for qtype, expected in [(1, "192.0.2.123"), (28, "2001:db8::123")]:
                            response = None
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                if process.poll() is not None:
                                    logfile.seek(0)
                                    self.fail("SmartDNS exited: " + logfile.read())
                                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                                    client.settimeout(0.2)
                                    client.sendto(question("youtube.com", qtype), ("127.0.0.1", listen_port))
                                    try:
                                        response, _ = client.recvfrom(4096)
                                        break
                                    except socket.timeout:
                                        pass
                            self.assertIsNotNone(response, "SmartDNS did not answer")
                            _, flags, _, answer_count, _, _ = struct.unpack("!6H", response[:12])
                            self.assertEqual(flags & 15, 0, "DNS response was not NOERROR")
                            self.assertGreater(answer_count, 0)
                            self.assertIn(ipaddress.ip_address(expected).packed, response)
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                        process = None
        finally:
            stop.set()
            worker.join(timeout=1)
            upstream.close()
            if process is not None:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
