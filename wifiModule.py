import re
import subprocess
import time


class WifiManager:
    def __init__(self, ssid: str = "PiTest", password: str = "pitest123"):
        self.ssid = ssid
        self.password = password
        self.interface = self._detectWifiInterface() or "wlan0"

    def prepare_environment(self):
        self._run_command(["sudo", "apt", "update"])
        self._run_command(["sudo", "apt", "install", "-y", "hostapd", "dnsmasq"])
        self._prepareServices()

    def _run_command(self, cmd, check=True, capture_output=True, text=True):
        try:
            return subprocess.run(
                cmd,
                check=check,
                capture_output=capture_output,
                text=text,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else ""
            raise RuntimeError(f"erro ao executar {' '.join(cmd)}: {stderr}") from exc

    def _prepareServices(self):
        self._run_command(["sudo", "systemctl", "unmask", "hostapd"])
        self._run_command(["sudo", "systemctl", "enable", "hostapd"])
        self._run_command(["sudo", "systemctl", "enable", "dnsmasq"])

    def _checkWifiCapabilities(self):
        try:
            result = self._run_command(["iw", "list"])
            output = result.stdout or ""
            return "Supported interface modes" in output and "* AP" in output
        except Exception:
            return False

    def configureFiles(self):
        if not self._checkWifiCapabilities():
            raise RuntimeError(f"a interface {self.interface} não suporta modo AP")

        try:
            driver = self._detectDriver()

            hostapd_conf = f"""interface={self.interface}
driver={driver}
ssid={self.ssid}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={self.password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
"""

            with open("/tmp/hostapd.conf", "w") as f:
                f.write(hostapd_conf)

            self._run_command(["sudo", "cp", "/tmp/hostapd.conf", "/etc/hostapd/hostapd.conf"])
            self._run_command(
                [
                    "sudo",
                    "sed",
                    "-i",
                    "s|^#\\?DAEMON_CONF=.*|DAEMON_CONF=\"/etc/hostapd/hostapd.conf\"|",
                    "/etc/default/hostapd",
                ]
            )

            dnsmasq_conf = f"""interface={self.interface}
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
"""

            with open("/tmp/dnsmasq.conf", "w") as f:
                f.write(dnsmasq_conf)

            self._run_command(["sudo", "cp", "/tmp/dnsmasq.conf", "/etc/dnsmasq.conf"])
            return True

        except Exception as exc:
            raise RuntimeError(f"erro ao configurar arquivos do hotspot: {exc}") from exc

    def _detectDriver(self):
        return "nl80211"

    def _unblockWifi(self):
        try:
            self._run_command(["sudo", "rfkill", "unblock", "wifi"], check=False)
            self._run_command(["sudo", "rfkill", "unblock", "all"], check=False)
            return True
        except Exception:
            return False

    def startHotspot(self):
        try:
            self._unblockWifi()

            self._run_command(["sudo", "systemctl", "stop", "NetworkManager"], check=False)
            self._run_command(["sudo", "systemctl", "stop", "wpa_supplicant"], check=False)
            self._run_command(["sudo", "systemctl", "stop", "hostapd"], check=False)
            self._run_command(["sudo", "systemctl", "stop", "dnsmasq"], check=False)

            time.sleep(2)

            self._run_command(["sudo", "ip", "link", "set", self.interface, "down"], check=False)
            self._run_command(["sudo", "iw", "dev", self.interface, "set", "type", "__ap"], check=False)
            self._run_command(["sudo", "ip", "link", "set", self.interface, "up"], check=False)

            if not self.configureFiles():
                return False

            self._run_command(["sudo", "ip", "addr", "flush", "dev", self.interface], check=False)
            self._run_command(
                ["sudo", "ip", "addr", "add", "192.168.4.1/24", "dev", self.interface]
            )
            self._run_command(["sudo", "ip", "link", "set", self.interface, "up"])

            self._run_command(["sudo", "systemctl", "restart", "dnsmasq"])
            self._run_command(["sudo", "systemctl", "restart", "hostapd"])

            time.sleep(3)

            self._run_command(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)
            return True

        except Exception as exc:
            raise RuntimeError(f"erro ao iniciar hotspot: {exc}") from exc

    def stopHotspot(self):
        try:
            self._run_command(["sudo", "systemctl", "stop", "hostapd"], check=False)
            self._run_command(["sudo", "systemctl", "stop", "dnsmasq"], check=False)

            self._run_command(["sudo", "ip", "addr", "flush", "dev", self.interface], check=False)
            self._run_command(["sudo", "ip", "link", "set", self.interface, "down"], check=False)
            self._run_command(["sudo", "iw", "dev", self.interface, "set", "type", "managed"], check=False)
            self._run_command(["sudo", "ip", "link", "set", self.interface, "up"], check=False)

            self._run_command(["sudo", "systemctl", "restart", "wpa_supplicant"], check=False)
            self._run_command(["sudo", "systemctl", "restart", "NetworkManager"], check=False)
            return True

        except Exception as exc:
            raise RuntimeError(f"erro ao parar hotspot: {exc}") from exc

    def wait_for_station(self, timeout: int, stable_s: int = 5) -> dict | None:
        """Aguarda até `timeout` segundos por uma estação associada.

        Após detectar cliente, aguarda `stable_s` segundos e re-verifica
        para confirmar que a conexão é estável antes de retornar.
        Retorna dict com mac + métricas, ou None se expirar.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._run_command(
                ["sudo", "iw", "dev", self.interface, "station", "dump"],
                check=False,
            )
            out = result.stdout if result else ""
            if out and "Station" in out:
                time.sleep(stable_s)
                result2 = self._run_command(
                    ["sudo", "iw", "dev", self.interface, "station", "dump"],
                    check=False,
                )
                out2 = result2.stdout if result2 else ""
                if out2 and "Station" in out2:
                    return self._parse_station_dump(out2)
            time.sleep(2)
        return None

    def _parse_station_dump(self, out: str) -> dict:
        info: dict = {}

        m = re.search(r"Station ([0-9a-f:]{17})", out)
        info["mac"] = m.group(1) if m else "unknown"

        m = re.search(r"signal:\s+([-\d]+)", out)
        if m:
            info["signal_dbm"] = int(m.group(1))

        m = re.search(r"signal avg:\s+([-\d]+)", out)
        if m:
            info["signal_avg_dbm"] = int(m.group(1))

        m = re.search(r"tx bytes:\s+(\d+)", out)
        if m:
            info["tx_bytes"] = int(m.group(1))

        m = re.search(r"rx bytes:\s+(\d+)", out)
        if m:
            info["rx_bytes"] = int(m.group(1))

        m = re.search(r"connected time:\s+(\d+)", out)
        if m:
            info["connected_time_s"] = int(m.group(1))

        m = re.search(r"tx bitrate:\s+([\d.]+ \S+)", out)
        if m:
            info["tx_bitrate"] = m.group(1)

        return info

    def _detectWifiInterface(self):
        try:
            result = self._run_command(["iw", "dev"], check=False)
            output = result.stdout or ""

            interfaces = []
            for line in output.split("\n"):
                if "Interface" in line:
                    interface = line.split("Interface")[1].strip()
                    interfaces.append(interface)

            for interface in interfaces:
                if interface.startswith(("wlan", "wlp")):
                    return interface

            if interfaces:
                return interfaces[0]

            return None
        except Exception:
            return None
