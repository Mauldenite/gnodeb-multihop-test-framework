from unittest.mock import MagicMock
import pytest
from ap import AccessPoint


def test_all_subsystems_reachable(ap):
    """Verify CU, DU (nested), and RC (parallel) all respond cleanly."""
    assert ap.cu.hostname() == "gnodeb-cu"
    assert ap.du.hostname() == "gnodeb-du"
    assert ap.rc.hostname() == "gnodeb-rc"


def test_network_isolation_enforced(ap):
    """Prove Jump Host CANNOT ping DU directly—confirming the nested tunnel through CU works."""
    assert ap.jump.can_ping("172.28.3.10") is False


def test_parallel_subsystem_telemetry(ap):
    """Verify subsystem IP addresses across CU, DU, and RC."""
    assert "172.28.2.10" in ap.cu.ip_addresses()
    assert "172.28.3.10" in ap.du.ip_addresses()
    assert "172.28.2.20" in ap.rc.ip_addresses()


def test_cu_control_plane_status(ap):
    """Verify CU F1 interface and AMF connection state."""
    status = ap.cu.get_status()
    assert status.f1_interface == "UP"
    assert status.ngap_amf_state == "CONNECTED"
    assert status.active_ue_sessions > 0


def test_du_ptp_sync_and_cell_state(ap):
    """Verify DU (nested inside CU) maintains PTP clock lock and active cell state."""
    stats = ap.du.get_phy_stats()
    assert stats.ptp_sync_state == "LOCKED"
    assert stats.cell_state == "ACTIVE"


def test_rc_rf_health_and_thermals(ap):
    """Verify RC (parallel hop) RF carrier lock and safe power amplifier temperature."""
    health = ap.rc.get_radio_health()
    assert health.rf_carrier_lock == "LOCKED"
    assert health.pa_temp_mc < 85000  # Below 85°C thermal threshold


def test_end_to_end_gnodeb_readiness(ap):
    """Cross-subsystem check: Cell can only transmit if CU F1, DU PTP, and RC RF are all healthy."""
    cu = ap.cu.get_status()
    du = ap.du.get_phy_stats()
    rc = ap.rc.get_radio_health()

    assert (
        cu.f1_interface == "UP"
        and du.ptp_sync_state == "LOCKED"
        and rc.rf_carrier_lock == "LOCKED"
    ), "End-to-end 5G gNodeB readiness check failed!"


def test_ap_parameter_guardrails_block_dodgy_inputs():
    """Verify invalid parameters fail fast in Python without touching SSH."""
    mock_nodes = {role: MagicMock() for role in ("jump", "cu", "du", "rc")}
    mock_ap = AccessPoint(mock_nodes)

    with pytest.raises(ValueError):
        mock_ap.du.get_phy_stats(cell_id=99)

    with pytest.raises(ValueError):
        mock_ap.du.extract_signal_rate_from_logs("/etc/passwd")

    with pytest.raises(ValueError):
        mock_ap.jump.can_ping("172.28.3.10; rm -rf /")

    for client in mock_nodes.values():
        client.exec_command.assert_not_called()
