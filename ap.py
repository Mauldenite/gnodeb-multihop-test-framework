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
class RTLinkStats:
    rt_id: int
    beam_id: int
    mcs: int
    cinr_db: float
    rssi_dbm: float
    signal_rate: float
    harq_retx: int


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
    _RT_LINE_RE = re.compile(
        r"rt_id=(?P<rt_id>\d+)\s+"
        r"beam_id=(?P<beam_id>\d+)\s+"
        r"mcs=(?P<mcs>\d+)\s+"
        r"cinr_db=(?P<cinr_db>-?[0-9.]+)\s+"
        r"rssi_dbm=(?P<rssi_dbm>-?[0-9.]+)\s+"
        r"signal_rate=(?P<signal_rate>[0-9.]+)\s+"
        r"harq_retx=(?P<harq_retx>\d+)"
    )

    @staticmethod
    def _validate_log_path(log_path: str) -> None:
        if not log_path.startswith("/var/log/") or ".." in log_path:
            raise ValueError(f"Log path must reside inside /var/log/: {log_path!r}")

    def _get_latest_rt_stats(self, log_path: str) -> dict[int, RTLinkStats]:
        """Reads [DU-RT-STATS] lines and keeps the most recent entry per rt_id."""
        self._validate_log_path(log_path)
        _, out, _ = self._exec(
            f"grep -F '[DU-RT-STATS]' {shlex.quote(log_path)}"
        )

        latest_by_rt: dict[int, RTLinkStats] = {}
        for line in out.splitlines():
            match = self._RT_LINE_RE.search(line)
            if match:
                rt = RTLinkStats(
                    rt_id=int(match.group("rt_id")),
                    beam_id=int(match.group("beam_id")),
                    mcs=int(match.group("mcs")),
                    cinr_db=float(match.group("cinr_db")),
                    rssi_dbm=float(match.group("rssi_dbm")),
                    signal_rate=float(match.group("signal_rate")),
                    harq_retx=int(match.group("harq_retx")),
                )
                latest_by_rt[rt.rt_id] = rt
        return latest_by_rt

    def get_active_rt_count(self, log_path: str = "/var/log/rf.log") -> int:
        """Returns the number of unique Remote Terminals reporting telemetry."""
        return len(self._get_latest_rt_stats(log_path))

    def get_degraded_rts(
        self,
        min_cinr_db: float = 18.0,
        max_harq_retx: int = 2,
        log_path: str = "/var/log/rf.log",
    ) -> list[RTLinkStats]:
        """
        Returns RTs whose latest CINR falls below `min_cinr_db` OR whose
        HARQ retransmissions exceed `max_harq_retx`, sorted by rt_id.
        """
        if isinstance(min_cinr_db, bool) or not isinstance(min_cinr_db, (int, float)) or not (-10.0 <= min_cinr_db <= 50.0):
            raise ValueError(f"min_cinr_db must be between -10.0 and 50.0, got {min_cinr_db!r}")
        if isinstance(max_harq_retx, bool) or not isinstance(max_harq_retx, int) or not (0 <= max_harq_retx <= 16):
            raise ValueError(f"max_harq_retx must be an integer between 0 and 16, got {max_harq_retx!r}")

        latest = self._get_latest_rt_stats(log_path)
        return sorted(
            [
                rt
                for rt in latest.values()
                if rt.cinr_db < min_cinr_db or rt.harq_retx > max_harq_retx
            ],
            key=lambda rt: rt.rt_id,
        )

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

    def extract_signal_rate_from_logs(
        self,
        rt_id: Optional[int] = None,
        log_path: str = "/var/log/rf.log",
    ) -> float:
        if not log_path.startswith("/var/log/") or ".." in log_path:
            raise ValueError(f"Log path must reside inside /var/log/: {log_path!r}")

        if rt_id is not None:
            if isinstance(rt_id, bool) or not isinstance(rt_id, int) or not (1 <= rt_id <= 64):
                raise ValueError(f"rt_id must be an integer between 1 and 64, got {rt_id!r}")
            pattern = f"rt_id={rt_id} .*signal_rate="
        else:
            pattern = "signal_rate="

        _, out, _ = self._exec(
            f"grep -E {shlex.quote(pattern)} {shlex.quote(log_path)} | tail -n 1"
        )
        match = re.search(r"signal_rate=([0-9]+(?:\.[0-9]+)?)", out)
        if not match:
            raise RuntimeError(f"Could not extract signal_rate (rt_id={rt_id}) from {log_path}: {out!r}")
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
