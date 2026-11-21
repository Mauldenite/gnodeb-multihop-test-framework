#!/usr/bin/env bash
set -euo pipefail

# Navigate to the script's directory so it works from anywhere
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== 1. Checking Python Virtual Environment ==="
if [ ! -d ".venv" ]; then
    echo "Creating .venv..."
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "=== 2. Ensuring SSH TCP Forwarding Hook Exists ==="
if [ ! -f "enable_forwarding.sh" ]; then
    cat << 'EOF' > enable_forwarding.sh
#!/bin/bash
for cfg in /config/sshd/sshd_config /etc/ssh/sshd_config; do
  if [ -f "$cfg" ]; then
    sed -i '/AllowTcpForwarding/d' "$cfg"
    echo "AllowTcpForwarding yes" >> "$cfg"
  fi
done
EOF
    chmod +x enable_forwarding.sh
fi

echo "=== 3. Starting 4-Node Isolated gNodeB Topology ==="
docker compose up -d

echo "=== 4. Waiting for Jump Host SSHD to Become Ready ==="
for i in {1..15}; do
    if nc -z 127.0.0.1 2222 2>/dev/null; then
        # Give internal CU/DU/RC containers a brief moment to finish keygen too
        sleep 2
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "Error: Jump host SSHD did not become ready in time."
        exit 1
    fi
    sleep 1
done

echo "=== 5. Running Multi-Hop Pytest Suite ==="
pytest -v -s
TEST_EXIT_CODE=$?

if [[ "${1:-}" == "--clean" ]]; then
    echo "=== 6. Tearing Down Lab Containers ==="
    docker compose down
fi

exit $TEST_EXIT_CODE
