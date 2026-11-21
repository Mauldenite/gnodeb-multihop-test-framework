#!/bin/bash
for cfg in /config/sshd/sshd_config /etc/ssh/sshd_config; do
  if [ -f "$cfg" ]; then
    sed -i '/AllowTcpForwarding/d' "$cfg"
    echo "AllowTcpForwarding yes" >> "$cfg"
  fi
done
