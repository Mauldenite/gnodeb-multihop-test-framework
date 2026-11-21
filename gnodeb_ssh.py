import paramiko
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Optional, Tuple


@dataclass
class NodeCredentials:
    host: str
    username: str
    password: Optional[str] = None
    key_filename: Optional[str] = None
    port: int = 22


class MultiHopSSHManager:
    """
    Manages nested SSH tunnels using Paramiko direct-tcpip channels.
    Supports topologies like:
        Runner -> JumpHost -> CU -> DU (nested)
                           -> RC       (parallel)
    """

    def __init__(self, jump_creds: NodeCredentials):
        self.jump_creds = jump_creds
        self._clients: list[paramiko.SSHClient] = []

    def _create_client(
        self,
        creds: NodeCredentials,
        sock: Optional[paramiko.Channel] = None,
    ) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=creds.host,
            port=creds.port,
            username=creds.username,
            password=creds.password,
            key_filename=creds.key_filename,
            sock=sock,
            timeout=10,
            banner_timeout=10,
        )
        self._clients.append(client)
        return client

    def hop_from(
        self,
        parent_client: paramiko.SSHClient,
        target_creds: NodeCredentials,
    ) -> paramiko.SSHClient:
        """Opens a direct-tcpip tunnel through parent_client to target_creds."""
        transport = parent_client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("Parent SSH transport is not active.")

        channel = transport.open_channel(
            kind="direct-tcpip",
            dest_addr=(target_creds.host, target_creds.port),
            src_addr=("127.0.0.1", 0),
        )
        return self._create_client(target_creds, sock=channel)

    def close_all(self) -> None:
        """Closes all SSH clients in reverse order (deepest child first)."""
        while self._clients:
            client = self._clients.pop()
            try:
                client.close()
            except Exception:
                pass


def run_cmd(client: paramiko.SSHClient, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
    """Executes a command cleanly on any hop and returns (exit_code, stdout, stderr)."""
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    return (
        exit_code,
        stdout.read().decode("utf-8", errors="replace").strip(),
        stderr.read().decode("utf-8", errors="replace").strip(),
    )


@contextmanager
def connect_gnodeb(
    jump: NodeCredentials,
    cu: NodeCredentials,
    du: NodeCredentials,
    rc: NodeCredentials,
) -> Generator[dict[str, paramiko.SSHClient], None, None]:
    """
    Context manager providing simultaneous, independent SSHClient handles
    to Jump, CU, DU (nested inside CU), and RC (parallel to CU).
    """
    mgr = MultiHopSSHManager(jump)
    try:
        jump_client = mgr._create_client(jump)
        cu_client = mgr.hop_from(jump_client, cu)
        du_client = mgr.hop_from(cu_client, du)   # Nested hop: Jump -> CU -> DU
        rc_client = mgr.hop_from(jump_client, rc) # Parallel hop: Jump -> RC

        yield {
            "jump": jump_client,
            "cu": cu_client,
            "du": du_client,
            "rc": rc_client,
        }
    finally:
        mgr.close_all()
