"""Isolated configuration and failure-injection regressions; no installation."""
import os
import ipaddress
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "run.sh").read_text()
FUNCTIONS = SOURCE.rsplit('\nmain "$@"', 1)[0]


def bash(code, directory):
    return subprocess.run(
        ["bash", "-c", FUNCTIONS + "\n" + code], cwd=directory,
        text=True, capture_output=True, timeout=10,
        env={**os.environ, "STACK_MODE": "dual"},
    )


# These fakes model external results, including commands that exit 0 on errors.
FAKES = r'''
systemctl() { [ "${FAIL:-}" != service ]; }
wg() {
  [ "${FAIL:-}" != wg ] || return 1
  if [ "${3:-}" = latest-handshakes ]; then
    if [ "${FAIL:-}" = handshake ]; then printf 'peer\t0\n'; else printf 'peer\t1700000000\n'; fi
  fi
}
dig() {
  [ "${FAIL:-}" != dig ] || return 1
  [ "${FAIL:-}" != dns-empty ] || return 0
  if [ "${FAIL:-}" = servfail ]; then
    echo ';; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1'
    return 0
  fi
  echo ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1'
  if [ "$3" = AAAA ]; then echo 'youtube.com. 60 IN AAAA 2001:db8::1'; else echo 'youtube.com. 60 IN A 192.0.2.1'; fi
}
ip() {
  [ "${FAIL:-}" != ip ] || return 1
  [ "${FAIL:-}" != route-empty ] || return 0
  if [[ " $* " == *' rule '* ]]; then
    if [ "${FAIL:-}" = wrong-mark ]; then echo '100: from all fwmark 0x3 lookup 250'; else echo '100: from all fwmark 0x2 lookup 250'; fi
  else
    if [ "${FAIL:-}" = wrong-interface ]; then echo 'default dev eth0'; else echo 'default dev wg'; fi
  fi
}
ipset() {
  [ "${FAIL:-}" != ipset ] || return 1
  if [ "${FAIL:-}" = ipset-missing ] && [ "$1" = test ]; then return 1; fi
}
iptables() { [ "${FAIL:-}" != firewall ]; }
ip6tables() { iptables "$@"; }
curl() {
  [ "${FAIL:-}" != curl ] || return 1
  [ "${FAIL:-}" != http-empty ] || return 0
  if [ "${FAIL:-}" = http-error ]; then echo '<html>HTTP error</html>'; return 0; fi
  # Ensure connection attempts have an overall timeout and reject HTTP errors.
  [[ " $* " == *' --max-time 20 '* ]] || return 1
  [[ " $* " == *' -fsS '* ]] || return 1
  if [ "$1" = -6 ]; then echo '2001:db8::2'; else echo '192.0.2.2'; fi
}
'''


class InstallerTests(unittest.TestCase):
    def test_single_stack_address_forms(self):
        forms = [
            "Address = 172.16.0.2/32, 2606:4700:110::2/128\n",
            "Address = 2606:4700:110::2/128,172.16.0.2/32\n",
            "Address = 172.16.0.2/32\nAddress = 2606:4700:110::2/128\n",
            "  Address\t= 172.16.0.2/32 , 2606:4700:110::2/128 # comment\n",
            "Address=172.16.0.2/32,2606:4700:110::2/128,172.16.0.3/32,2606:4700:110::3/128\n",
        ]
        for mode in ("ipv4", "ipv6"):
            for form in forms:
                with self.subTest(mode=mode, form=form), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)
                    profile = "[Interface]\nPrivateKey = test-key\n" + form + "[Peer]\nAllowedIPs = 0.0.0.0/0, ::/0\n"
                    (path / "input").write_text(profile)
                    result = bash(f'STACK_MODE={mode}\nfilter_wireguard_addresses input output', directory)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = (path / "output").read_text()
                    addresses = [item.strip() for line in output.splitlines() if line.startswith("Address =") for item in line.split("=", 1)[1].split(",")]
                    self.assertTrue(addresses)
                    self.assertTrue(all(ipaddress.ip_interface(item).version == (4 if mode == "ipv4" else 6) for item in addresses))
                    self.assertIn("PrivateKey = test-key", output)
                    self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", output)
                    self.assertEqual(len(addresses), 2 if form == forms[-1] else 1)

    def test_dual_and_plain_preserve_profile(self):
        profile = '[Interface]\nAddress = 172.16.0.2/32, 2606:4700:110::2/128\n'
        for mode in ("dual", "none"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "input").write_text(profile)
                result = bash(f'STACK_MODE={mode}\nfilter_wireguard_addresses input output', directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((path / "output").read_text(), profile)

    def test_generated_profiles_and_endpoint(self):
        for mode in ("dual", "ipv4", "ipv6", "none"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "account").touch()
                (path / "fixture").write_text('[Interface]\nPrivateKey = test\nAddress = 172.16.0.2/32, 2606:4700:110::2/128\nDNS = 1.1.1.1\n[Peer]\nPublicKey = test\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = old.example:2408\n')
                (path / "wgcf").write_text('#!/bin/bash\nwhile [ "$#" -gt 0 ]; do\n if [ "$1" = --profile ]; then cp fixture "$2"; exit; fi\n shift\ndone\nexit 1\n')
                (path / "wgcf").chmod(0o700)
                result = bash(f'''STACK_MODE={mode}
WG_DIR="$PWD"
WG_CONF="$PWD/wg.conf"
WGCF_ACCOUNT="$PWD/account"
WGCF_BIN="$PWD/wgcf"
WARP_ENDPOINT=162.159.193.1:2408
generate_warp_profile
''', directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = (path / "wg.conf").read_text()
                self.assertEqual((path / "wg.conf").stat().st_mode & 0o777, 0o600)
                self.assertIn('Endpoint = 162.159.193.1:2408', output)
                self.assertEqual('172.16.0.2/32' in output, mode != "ipv6")
                self.assertEqual('2606:4700:110::2/128' in output, mode != "ipv4")
                self.assertEqual('Table = off' in output, mode != "none")

    def test_smartdns_default_upstreams_all_modes(self):
        for mode in ("dual", "ipv4", "ipv6", "none"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                result = bash(f'''STACK_MODE={mode}
SMARTDNS_CONF="$PWD/smartdns.conf"
# Avoid all fixed system paths used by the writer.
mkdir() {{ :; }}
touch() {{ :; }}
chmod() {{ :; }}
write_smartdns_conf
''', directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = (Path(directory) / "smartdns.conf").read_text()
                servers = [line.split() for line in config.splitlines() if line.startswith("server ")]
                self.assertEqual(servers, [["server", "1.1.1.1"], ["server", "8.8.8.8"]])
                self.assertNotIn('server-quic', config)
                self.assertNotIn('server-h3', config)
                self.assertNotIn('-bootstrap-dns', config)
                self.assertNotIn('-fallback', config)
                self.assertIn('dualstack-ip-selection no', config)
                self.assertEqual('ipset /youtube.com/' in config, mode != "none")

    def checks(self, mode="dual", failure="", main=False, all_fail=False):
        code = FAKES + f'\nSTACK_MODE={mode}\nFAIL={shlex.quote(failure)}\n'
        if all_fail:
            code += '\n' + '\n'.join(f'{name}() {{ return 1; }}' for name in ['systemctl', 'wg', 'dig', 'ip', 'ipset', 'iptables', 'ip6tables', 'curl'])
        if main:
            for name in ['require_root', 'select_stack_mode', 'detect_arch', 'apt_install_base', 'install_smartdns', 'disable_dnsmasq_and_resolved', 'install_wgcf', 'generate_warp_profile', 'configure_sysctl', 'write_smartdns_conf', 'write_wireguard_hooks', 'enable_services']:
                code += f'\n{name}() {{ :; }}'
            code += '\nmain\n'
        else:
            code += '\nrun_checks\n'
        with tempfile.TemporaryDirectory() as directory:
            return bash(code, directory)

    def test_checks_success_each_mode(self):
        for mode in ("dual", "ipv4", "ipv6", "none"):
            with self.subTest(mode=mode):
                result = self.checks(mode=mode, main=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Done.', result.stdout)

    def test_each_critical_failure_blocks_done(self):
        for failure in ['service', 'wg', 'dig', 'servfail', 'dns-empty', 'ip', 'route-empty', 'wrong-mark', 'wrong-interface', 'ipset', 'ipset-missing', 'firewall', 'curl', 'http-empty', 'http-error', 'handshake']:
            with self.subTest(failure=failure):
                result = self.checks(failure=failure, main=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Done.', result.stdout)
                self.assertIn('verification failed', result.stderr)

    def test_all_external_checks_fail(self):
        for mode in ("dual", "ipv4", "ipv6", "none"):
            with self.subTest(mode=mode):
                result = self.checks(mode=mode, main=True, all_fail=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Done.', result.stdout)
                self.assertIn('check(s) failed', result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
