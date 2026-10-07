"""Unit-test guard only: not OS isolation, worker egress or deployment proof."""

import ipaddress
import socket


def allowed_host(host: object) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


def check_address(family: int, address: object) -> None:
    if family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
        if not allowed_host(address[0]) or address[1] in (8080, 11434):
            raise OSError("external/live-default destination blocked by offline verifier")


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def connect(self, address):
    check_address(self.family, address)
    return _connect(self, address)


def connect_ex(self, address):
    check_address(self.family, address)
    return _connect_ex(self, address)


def getaddrinfo(host, *args, **kwargs):
    if not allowed_host(host):
        raise OSError("external DNS blocked by offline verifier")
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo
