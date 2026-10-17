"""URL safety guard (SSRF protection).

A URL is considered unsafe if:
* the scheme is not http(s)
* the hostname resolves to a private, loopback, link-local,
  multicast, reserved or metadata range
* the URL is malformed
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            return True
    return False


def is_safe_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if not p.hostname:
        return False
    # If hostname is an IP literal, check directly.
    try:
        ipaddress.ip_address(p.hostname)
        return not _is_blocked_ip(p.hostname)
    except ValueError:
        pass
    # Otherwise resolve and check. If DNS fails we trust the
    # caller to do further validation; we don't reject just
    # because we can't resolve.
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        return True
    for fam, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            return False
    return True
