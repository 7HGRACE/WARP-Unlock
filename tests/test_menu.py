"""Exercise only mode selection; never run the installer or networking commands."""
import errno
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import subprocess
import tempfile
import time
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "run.sh").read_text()
MENU = SOURCE.split("\ndetect_arch() {", 1)[0] + '\nselect_stack_mode\nprintf "RESULT=%s\\n" "$STACK_MODE"\n'


class MenuTests(unittest.TestCase):
    def noninteractive(self, mode=None):
        env = os.environ.copy()
        env.pop("STACK_MODE", None)
        if mode is not None:
            env["STACK_MODE"] = mode
        return subprocess.run(
            ["bash"], input=MENU, text=True, capture_output=True,
            env=env, start_new_session=True, timeout=5,
        )

    def terminal(self, answer, piped=True):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "menu.sh"
            script.write_text(MENU)
            pid, fd = pty.fork()
            if pid == 0:
                os.environ.pop("STACK_MODE", None)
                path = shlex.quote(str(script))
                command = f"cat {path} | bash" if piped else f"bash {path}"
                os.execlp("bash", "bash", "-c", command)
            output = b""
            sent = False
            status = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if ready:
                        try:
                            chunk = os.read(fd, 65536)
                        except OSError as error:
                            if error.errno != errno.EIO:
                                raise
                            break
                        if not chunk:
                            break
                        output += chunk
                        if not sent and "请输入选项".encode() in output:
                            os.write(fd, answer)
                            sent = True
                else:
                    self.fail("Menu did not finish: " + output.decode(errors="replace"))
                _, status = os.waitpid(pid, 0)
                return os.waitstatus_to_exitcode(status), output.decode(errors="replace")
            finally:
                os.close(fd)
                if status is None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    os.waitpid(pid, 0)

    def test_pipeline_each_mode(self):
        for number, mode in enumerate(("dual", "ipv4", "ipv6", "none"), 1):
            with self.subTest(mode=mode):
                code, output = self.terminal(f"{number}\n".encode())
                self.assertEqual(code, 0, output)
                self.assertIn(f"RESULT={mode}", output)

    def test_direct_file(self):
        code, output = self.terminal(b"2\n", piped=False)
        self.assertEqual(code, 0, output)
        self.assertIn("RESULT=ipv4", output)

    def test_empty_defaults_to_dual(self):
        code, output = self.terminal(b"\n")
        self.assertEqual(code, 0, output)
        self.assertIn("RESULT=dual", output)

    def test_invalid_then_valid(self):
        code, output = self.terminal(b"wrong\n3\n")
        self.assertEqual(code, 0, output)
        self.assertIn("无效选项", output)
        self.assertIn("RESULT=ipv6", output)

    def test_terminal_eof(self):
        code, output = self.terminal(b"\x04")
        self.assertNotEqual(code, 0, output)
        self.assertIn("Input ended", output)
        self.assertNotIn("RESULT=", output)

    def test_no_terminal_requires_mode(self):
        result = self.noninteractive()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No interactive terminal", result.stderr)
        self.assertNotIn("RESULT=", result.stdout)

    def test_explicit_modes_without_terminal(self):
        for mode in ("dual", "ipv4", "ipv6", "none"):
            with self.subTest(mode=mode):
                result = self.noninteractive(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"RESULT={mode}", result.stdout)
                self.assertNotIn("请选择", result.stdout)

    def test_invalid_environment(self):
        result = self.noninteractive("typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid STACK_MODE", result.stderr)
        self.assertNotIn("RESULT=", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
