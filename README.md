# Multi-Hop SSH Test Automation Framework (5G gNodeB Topology)

A lightweight, containerized Python + `pytest` test automation framework demonstrating **nested `direct-tcpip` SSH tunneling** via Paramiko across isolated embedded Linux subsystems.

Modeled on real-world 5G mmWave Access Point (gNodeB) architectures where internal radio and baseband units sit behind non-routable backplane networks.

## Network & Subsystem Topology

```text
[ Pytest Runner ]
       │
       │  SSH (localhost:2222)
       ▼
[ Linux Jump Host ]  (172.28.1.10 / 172.28.2.2)
       │
       ├─────────────────────────────┐  (Isolated Backplane: 172.28.2.0/24)
       │                             │
       ▼                             ▼
[ gNodeB CU ] (172.28.2.10)   [ gNodeB RC ] (172.28.2.20)
  Central Unit                  Radio Controller (Parallel Hop)
       │
       │  (Isolated Internal Link: 172.28.3.0/24)
       ▼
[ gNodeB DU ] (172.28.3.10)
  Distributed Unit (Nested Hop via CU)
```

### Architectural Highlights
- **True Socket Forwarding (`direct-tcpip`):** Avoids brittle `invoke_shell()` prompt-scraping by opening native in-memory Paramiko TCP channels across hops, providing independent `SSHClient` handles with deterministic exit codes and separated `stdout`/`stderr`.
- **Enforced L3 Network Isolation:** Docker internal bridge networks (`internal: true`) ensure `gnodeb-du` is completely unreachable from the Jump Host except via a nested tunnel through `gnodeb-cu`.
- **Session-Scoped Pytest Fixtures:** Establishes the multi-hop tunnel hierarchy once per test session and guarantees reverse-order socket teardown via context managers.
- **Simulated 5G Telemetry Endpoints:** Container-mounted scripts (`cu_status.sh`, `du_phy_stats.sh`, `rc_radio_health.sh`) validate F1-C/NGAP control-plane state, PTP clock lock, and RF thermal thresholds.

## Quick Start

Run the entire lab (creates `.venv`, starts the 4 isolated Docker nodes, waits for `sshd` readiness, and executes `pytest`):

```bash
chmod +x run_lab.sh
./run_lab.sh
```

To run the suite and automatically tear down all containers afterwards:

```bash
./run_lab.sh --clean
```

## Project Structure

```text
├── gnodeb_ssh.py          # MultiHopSSHManager & direct-tcpip tunnel context manager
├── conftest.py            # Session-scoped pytest fixture exposing CU, DU, and RC handles
├── test_gnodeb.py         # Isolation, subsystem health, and end-to-end 5G readiness tests
├── docker-compose.yml     # 4-node topology across 3 isolated Docker bridge networks
├── enable_forwarding.sh   # Container init hook enabling AllowTcpForwarding in sshd
├── run_lab.sh             # One-command environment bootstrap and test runner
├── requirements.txt       # paramiko & pytest dependencies
└── scripts/               # Simulated gNodeB subsystem telemetry scripts (CU, DU, RC)
```
