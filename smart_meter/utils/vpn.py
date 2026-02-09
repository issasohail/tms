# smart_meter/utils/vpn.py
from __future__ import annotations
import json
import subprocess
import re
import time
from django.core.cache import cache

# common VPN adapter/name hints; extend if you use others
VPN_HINTS = re.compile(
    r"(vpn|openvpn|wireguard|wg|nord|express|proton|surfshark|tailscale|zerotier|lynx|tun|tap)", re.I)

CACHE_KEY = "vpn_connected_flag"
CACHE_TTL = 30  # seconds


def _detect_vpn_windows() -> bool:
    """
    Returns True if default route likely goes via a VPN interface.
    Uses: Get-NetRoute -DestinationPrefix 0.0.0.0/0 | sort RouteMetric | select -First 1
    """
    try:
        ps = [
            "powershell", "-NoProfile", "-Command",
            "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1 "
            " | Select InterfaceAlias,NextHop,RouteMetric | ConvertTo-Json -Compress)"
        ]
        out = subprocess.check_output(ps, timeout=3)
        obj = json.loads(out.decode("utf-8") or "{}")
        alias = (obj.get("InterfaceAlias") or "").strip()
        # Heuristic: alias matches common VPN names
        return bool(VPN_HINTS.search(alias))
    except Exception:
        # If the probe fails, play it safe: assume not connected
        return False

# smart_meter/utils/vpn.py (optional)


def public_ip() -> str | None:
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
                "(Invoke-WebRequest -Uri 'https://api.ipify.org').Content"],
            timeout=3
        )
        return out.decode("utf-8").strip()
    except Exception:
        return None


def vpn_connected() -> bool:
    val = cache.get(CACHE_KEY)
    if val is not None:
        return bool(val)
    flag = _detect_vpn_windows()
    cache.set(CACHE_KEY, flag, CACHE_TTL)
    return flag
