import pytest
from gnodeb_ssh import NodeCredentials, connect_gnodeb
from ap import AccessPoint


@pytest.fixture(scope="session")
def gnodeb_nodes():
    """
    Establishes the multi-hop SSH topology once per pytest session:
      Runner -> Jump (localhost:2222)
               -> CU (172.28.2.10:2222) -> DU (172.28.3.10:2222)
               -> RC (172.28.2.20:2222)
    """
    jump = NodeCredentials(host="127.0.0.1", port=2222, username="qa_runner", password="jump_pass")
    cu = NodeCredentials(host="172.28.2.10", port=2222, username="admin", password="cu_pass")
    du = NodeCredentials(host="172.28.3.10", port=2222, username="admin", password="du_pass")
    rc = NodeCredentials(host="172.28.2.20", port=2222, username="admin", password="rc_pass")

    with connect_gnodeb(jump, cu, du, rc) as nodes:
        yield nodes


@pytest.fixture(scope="session")
def ap(gnodeb_nodes) -> AccessPoint:
    """Exposes the validated AccessPoint facade over the active multi-hop tunnels."""
    return AccessPoint(gnodeb_nodes)
