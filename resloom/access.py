"""Request checks for the Streamlit app."""

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def host_allowed(host: str | None) -> bool:
    """True for a Host header naming this machine: 127.0.0.1, localhost or [::1], with any port.

    A DNS-rebinding page reaches the app under its own host name, so its requests carry that
    name in the Host header and are refused.
    """
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return False
        name, rest = host[: end + 1], host[end + 1 :]
    else:
        name, _, port = host.partition(":")
        rest = ":" + port if port else ""
    if rest and not (rest.startswith(":") and rest[1:].isdigit()):
        return False
    return name in LOCAL_HOSTS
