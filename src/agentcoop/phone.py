"""Private-network phone access for the board viewer."""

from __future__ import annotations

import ipaddress
import secrets
import socket
import time
from dataclasses import dataclass

from agentcoop.errors import CoopError
from agentcoop.qrencode import qr_ascii, qr_svg

PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # Tailscale CGNAT
)

PAIR_TTL_SECONDS = 300
PHONE_SESSION_HOURS = 12


@dataclass
class PairingCode:
    code: str
    expires: float
    used: bool = False

    def ok(self, supplied: str) -> bool:
        if self.used:
            return False
        if time.time() > self.expires:
            return False
        return secrets.compare_digest(self.code, supplied.strip())


@dataclass
class PhoneSession:
    token: str
    expires: float
    readonly: bool = True

    def alive(self) -> bool:
        return time.time() <= self.expires


def is_private_ip(host: str) -> bool:
    """True when the address is loopback or a private / Tailscale range."""
    try:
        addr = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    return any(addr in net for net in PRIVATE_NETWORKS)


def refuse_public(host: str) -> None:
    if not is_private_ip(host):
        raise CoopError("Phone mode only listens on a private network address, not a public one.")


def list_private_ipv4() -> list[str]:
    """Return private IPv4 addresses on this computer, preferred first."""
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if is_private_ip(ip) and ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    # Also probe outbound route.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if is_private_ip(ip) and not ip.startswith("127.") and ip not in found:
                found.insert(0, ip)
        finally:
            probe.close()
    except OSError:
        pass
    return found


def pick_phone_host() -> tuple[str, list[str]]:
    """Return (primary, others) private addresses for phone mode."""
    addrs = list_private_ipv4()
    if not addrs:
        raise CoopError(
            "No private network address was found. Connect to Wi-Fi or Tailscale, then try again."
        )
    return addrs[0], addrs[1:]


def new_pairing_code() -> PairingCode:
    code = f"{secrets.randbelow(1_000_000):06d}"
    return PairingCode(code=code, expires=time.time() + PAIR_TTL_SECONDS)


def new_phone_session() -> PhoneSession:
    return PhoneSession(
        token=secrets.token_urlsafe(24),
        expires=time.time() + PHONE_SESSION_HOURS * 3600,
        readonly=True,
    )


def pairing_banner(url: str, code: str, others: list[str]) -> str:
    """Human-readable terminal and chat output with QR."""
    lines = [
        "Open this on your phone (same Wi-Fi or Tailscale):",
        url,
        "",
        "Pairing code (one use, 5 minutes): " + code,
        "",
        "Warning: plain HTTP on the local network can be read by others on the same network.",
        "Use it only on a trusted home network, or over Tailscale.",
        "On Windows, the firewall may ask to allow the listener; that window can take focus.",
        "Away from home, the phone can reach it only over a VPN such as Tailscale.",
        "",
        qr_ascii(url),
    ]
    if others:
        lines.append("")
        lines.append("Other private addresses on this computer: " + ", ".join(others))
    return "\n".join(lines)


def pairing_svg(url: str) -> str:
    return qr_svg(url)
