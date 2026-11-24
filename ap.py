import re
import shlex
from dataclasses import dataclass
from typing import Optional
import paramiko
from gnodeb_ssh import run_cmd


@dataclass(frozen=True)
class CUStatus:
    subsystem: str
    f1_interface: str
    active_ue_sessions: int
    ngap_amf_state: str


@dataclass(frozen=True)
class DUPhyStats:
    subsystem: str
    ptp_sync_state: str
    slot_timing_us: int
    cell_state: str


@dataclass(frozen=True)
class RCRadioHealth:
    subsystem: str
    rf_carrier_lock: str
    pa_temp_mc: int


def _parse_kv(raw: str) -> dict[str, str]:
    """Parses KEY=VALUE lines returned by subsystem telemetry scripts."""
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


class SubsystemNode:
    """Base wrapper providing validated command execution on a gNodeB subsystem."""

    def __init__(self, client: paramiko.SSHClient, bin_dir: str = "/usr/local/bin"):
        self._client = client
        self._bin_dir = bin_dir.rstrip("/")

    def _exec(self, cmd: str, timeout: int = 15, check: bool = True) -> tuple[int, str, str]:
        code, out, err = run_cmd(self._client, cmd, timeout=timeout)
        if check and code != 0:
            raise RuntimeError(
                f"Remote command failed (exit {code}): {cmd}\nSTDERR: {err or out}"
            )
        return code, out, err

    def hostname(self) -> str:
        _, out, _ = self._exec("hostname")
        return out

    def ip_addresses(self) -> str:
        _, out, _ = self._exec("hostname -i")
        return out


class JumpNode(SubsystemNode):
    """Jump host operations (useful for verifying L3 network isolation)."""

    _IP_REGEX = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

    def can_ping(self, target_ip: str, timeout_sec: int = 1) -> bool:
        if not self._IP_REGEX.match(target_ip):
            raise ValueError(f"Invalid IPv4 address: {target_ip!r}")
        if not isinstance(timeout_sec, int) or not (1 <= timeout_sec <= 10):
            raise ValueError(f"timeout_sec must be between 1 and 10, got {timeout_sec!r}")

        code, _, _ = self._exec(
            f"ping -c 1 -W {timeout_sec} {shlex.quote(target_ip)}",
            timeout=timeout_sec + 5,
            check=False,
        )
        return code == 0


class CUNode(SubsystemNode):
    """Explicit, parameter-validated methods for the Central Unit."""

    def get_status(self) -> CUStatus:
        _, out, _ = self._exec(f"{self._bin_dir}/cu_status.sh")
        data = _parse_kv(out)
        try:
            return CUStatus(
                subsystem=data["SUBSYSTEM"],
                f1_interface=data["F1_INTERFACE"],
                active_ue_sessions=int(data["ACTIVE_UE_SESSIONS"]),
                ngap_amf_state=data["NGAP_AMF_STATE"],
            )
        except KeyError as exc:
            raise RuntimeError(f"Missing expected key {exc} in cu_status.sh output: {out!r}") from exc

    def restart_system_monitor(self) -> None:
        self._exec("systemctl restart sysmon || service sysmon restart")


class DUNode(SubsystemNode):
    """Explicit, parameter-validated methods for the Distributed Unit."""

    def get_phy_stats(self, cell_id: Optional[int] = None) -> DUPhyStats:
        cmd = f"{self._bin_dir}/du_phy_stats.sh"
        if cell_id is not None:
            if isinstance(cell_id, bool) or not isinstance(cell_id, int) or not (0 <= cell_id <= 15):
                raise ValueError(f"cell_id must be an integer between 0 and 15, got {cell_id!r}")
            cmd = f"{cmd} {cell_id}"

        _, out, _ = self._exec(cmd)
        data = _parse_kv(out)
        try:
            return DUPhyStats(
                subsystem=data["SUBSYSTEM"],
                ptp_sync_state=data["PTP_SYNC_STATE"],
                slot_timing_us=int(data["SLOT_TIMING_US"]),
                cell_state=data["CELL_STATE"],
            )
        except KeyError as exc:
            raise RuntimeError(f"Missing expected key {exc} in du_phy_stats.sh output: {out!r}") from exc

    def extract_signal_rate_from_logs(self, log_path: str = "/var/log/rf.log") -> float:
        if not log_path.startswith("/var/log/") or ".." in log_path:
            raise ValueError(f"Log path must reside inside /var/log/: {log_path!r}")

        _, out, _ = self._exec(
            f"grep -E 'signal_rate=' {shlex.quote(log_path)} | tail -n 1"
        )
        match = re.search(r"signal_rate=([0-9]+(?:\.[0-9]+)?)", out)
        if not match:
            raise RuntimeError(f"Could not extract signal_rate from {log_path}: {out!r}")
        return float(match.group(1))


class RCNode(SubsystemNode):
    """Explicit, parameter-validated methods for the Radio Controller."""

    def get_radio_health(self) -> RCRadioHealth:
        _, out, _ = self._exec(f"{self._bin_dir}/rc_radio_health.sh")
        data = _parse_kv(out)
        try:
            return RCRadioHealth(
                subsystem=data["SUBSYSTEM"],
                rf_carrier_lock=data["RF_CARRIER_LOCK"],
                pa_temp_mc=int(data["PA_TEMP_MC"]),
            )
        except KeyError as exc:
            raise RuntimeError(f"Missing expected key {exc} in rc_radio_health.sh output: {out!r}") from exc


class AccessPoint:
    """High-level 5G gNodeB Access Point facade over multi-hop SSH clients."""

    def __init__(self, nodes: dict[str, paramiko.SSHClient]):
        self.jump = JumpNode(nodes["jump"])
        self.cu = CUNode(nodes["cu"])
        self.du = DUNode(nodes["du"])
        self.rc = RCNode(nodes["rc"])
