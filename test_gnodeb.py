from gnodeb_ssh import run_cmd


def test_all_subsystems_reachable(gnodeb_nodes):
    """Verify Jump, CU, DU (nested), and RC (parallel) all respond cleanly."""
    expected_hosts = {
        "cu": "gnodeb-cu",
        "du": "gnodeb-du",
        "rc": "gnodeb-rc",
    }

    for role, expected_hostname in expected_hosts.items():
        code, out, err = run_cmd(gnodeb_nodes[role], "hostname")
        assert code == 0, f"{role.upper()} failed: {err}"
        assert out == expected_hostname, f"Expected {expected_hostname}, got {out}"


def test_network_isolation_enforced(gnodeb_nodes):
    """Prove Jump Host CANNOT ping DU directly—confirming the nested tunnel through CU works."""
    code, out, _ = run_cmd(gnodeb_nodes["jump"], "ping -c 1 -W 1 172.28.3.10")
    assert code != 0, "Jump host should not have direct L3 routing to DU!"


def test_parallel_subsystem_telemetry(gnodeb_nodes):
    """Execute commands across CU, DU, and RC in a single test flow."""
    _, cu_ip, _ = run_cmd(gnodeb_nodes["cu"], "hostname -i")
    _, du_ip, _ = run_cmd(gnodeb_nodes["du"], "hostname -i")
    _, rc_ip, _ = run_cmd(gnodeb_nodes["rc"], "hostname -i")

    assert "172.28.2.10" in cu_ip
    assert "172.28.3.10" in du_ip
    assert "172.28.2.20" in rc_ip


def parse_kv_output(raw: str) -> dict[str, str]:
    """Parses KEY=VALUE lines returned by subsystem telemetry scripts."""
    result = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def test_cu_control_plane_status(gnodeb_nodes):
    """Verify CU F1 interface and AMF connection state."""
    code, out, err = run_cmd(gnodeb_nodes["cu"], "/usr/local/bin/cu_status.sh")
    assert code == 0, f"CU status script failed: {err}"

    stats = parse_kv_output(out)
    assert stats["F1_INTERFACE"] == "UP"
    assert stats["NGAP_AMF_STATE"] == "CONNECTED"
    assert int(stats["ACTIVE_UE_SESSIONS"]) > 0


def test_du_ptp_sync_and_cell_state(gnodeb_nodes):
    """Verify DU (nested inside CU) maintains PTP clock lock and active cell state."""
    code, out, err = run_cmd(gnodeb_nodes["du"], "/usr/local/bin/du_phy_stats.sh")
    assert code == 0, f"DU PHY script failed: {err}"

    stats = parse_kv_output(out)
    assert stats["PTP_SYNC_STATE"] == "LOCKED"
    assert stats["CELL_STATE"] == "ACTIVE"


def test_rc_rf_health_and_thermals(gnodeb_nodes):
    """Verify RC (parallel hop) RF carrier lock and safe power amplifier temperature."""
    code, out, err = run_cmd(gnodeb_nodes["rc"], "/usr/local/bin/rc_radio_health.sh")
    assert code == 0, f"RC radio script failed: {err}"

    stats = parse_kv_output(out)
    assert stats["RF_CARRIER_LOCK"] == "LOCKED"
    assert int(stats["PA_TEMP_MC"]) < 85000  # Below 85°C thermal threshold


def test_end_to_end_gnodeb_readiness(gnodeb_nodes):
    """Cross-subsystem check: Cell can only transmit if CU F1, DU PTP, and RC RF are all healthy."""
    _, cu_out, _ = run_cmd(gnodeb_nodes["cu"], "/usr/local/bin/cu_status.sh")
    _, du_out, _ = run_cmd(gnodeb_nodes["du"], "/usr/local/bin/du_phy_stats.sh")
    _, rc_out, _ = run_cmd(gnodeb_nodes["rc"], "/usr/local/bin/rc_radio_health.sh")

    cu = parse_kv_output(cu_out)
    du = parse_kv_output(du_out)
    rc = parse_kv_output(rc_out)

    assert (
        cu["F1_INTERFACE"] == "UP"
        and du["PTP_SYNC_STATE"] == "LOCKED"
        and rc["RF_CARRIER_LOCK"] == "LOCKED"
    ), "End-to-end 5G gNodeB readiness check failed!"