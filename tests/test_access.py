from resloom.access import host_allowed


def test_local_hosts_allowed():
    for host in ["127.0.0.1", "127.0.0.1:8501", "localhost", "LOCALHOST:80", "[::1]", "[::1]:8501"]:
        assert host_allowed(host), host


def test_other_hosts_refused():
    for host in [None, "", "attacker.example", "attacker.example:8501", "localhost.attacker.example",
                 "127.0.0.1.nip.io", "192.168.1.20:8501", "[::2]:8501", "localhost:abc", "[::1", "0.0.0.0:8501"]:
        assert not host_allowed(host), host
