"""
===============================================================================
Network Egress Policy (where text and credentials may be sent)
===============================================================================
Page text, screenshots and API credentials leave the machine only to an endpoint this policy
accepts: https anywhere, plain http only to a host whose every address is loopback or on a private
network. Callers say what they are sending, so a refusal reads in their own words.

Features:
    ✓ egress_refusal(url, what): why `what` may not be sent to `url`, or "" when it may
    ✓ Private ranges are RFC 1918, carrier-grade NAT / tailnets (RFC 6598) and IPv6 unique-local,
      not every reserved block (ipaddress.is_private also counts documentation ranges)
    ✓ Checked when a client is built, not on every request: the name is resolved here and again by
      the HTTP client when it connects, so a name that changes between the two is not caught

Author: PMAC
===============================================================================
"""
import ipaddress
import socket

import httpx

# RFC 1918, carrier-grade NAT / tailnets (RFC 6598) and IPv6 unique-local. Not ipaddress.is_private: that also counts
# documentation and other reserved ranges, which are not your network.
_PRIVATE_NETS = tuple(ipaddress.ip_network(n)
                      for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))
_DEFAULT_HTTP_PORT = 80


def _private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip.version == n.version and ip in n for n in _PRIVATE_NETS)


def egress_refusal(base_url: str, what: str) -> str:
    """Why `what` (for example "tickets") may not be sent to base_url, or "": https anywhere; plain http only to a host
    whose every address is loopback or private (RFC 1918, RFC 6598, IPv6 ULA)."""
    u = httpx.URL(base_url)
    if u.scheme == "https":
        return ""
    host = u.host or ""
    try:
        port = u.port or _DEFAULT_HTTP_PORT
        addrs = ({str(a[4][0]) for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)} if host else set())
    except OSError:
        addrs = set()
    if addrs and all(_private(a) for a in addrs):
        return ""
    return (f"{what} go over plain http only to a loopback or private-network host; {host or '(no host)'} is not one: "
            "use https")
