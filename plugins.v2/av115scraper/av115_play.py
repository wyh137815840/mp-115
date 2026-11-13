"""内外网播放地址。

Av115 自己的配置键（MoviePilot 里前缀 av115scraper_）：

- play_base：外网播放地址模板
- play_base_lan：内网播放地址模板
- lan_networks：内网网段
- strm_use_lan：strm 是否写入内网模板

P115StrmHelper 不在本仓库，它的 redirect_url 不会按来源分流。
strm 要指向本插件的 /redirect，上面的判断才会生效。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from av115_library import build_play_url

# 1.5.2 起写进配置里的默认值。升级后改到本插件自己的跳转，才能按内外网分流。
LEGACY_WAN_PLAY_BASE = (
    "https://mp.yuanhot.top/api/v1/plugin/P115StrmHelper/redirect_url"
    "?pickcode={pickcode}&file_name={filename}"
)
WAN_PLAY_BASE = (
    "https://mp.yuanhot.top:8443/api/v1/plugin/Av115Scraper/redirect"
    "?pickcode={pickcode}&file_name={filename}"
)
# 2026-11 备案期临时切换：旧默认是 443，读取旧配置时自动迁移到 8443。
OLD_WAN_PLAY_BASE_443 = (
    "https://mp.yuanhot.top/api/v1/plugin/Av115Scraper/redirect"
    "?pickcode={pickcode}&file_name={filename}"
)
LAN_PLAY_BASE = (
    "http://192.168.1.9:3000/api/v1/plugin/Av115Scraper/redirect"
    "?pickcode={pickcode}&file_name={filename}"
)
DEFAULT_LAN_NETWORKS = (
    "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8,"
    "::1/128,fc00::/7,fe80::/10"
)


def normalize_play_base(value: str) -> str:
    """空值和旧的 P115StrmHelper 默认模板，都换成外网侧的本插件跳转。"""
    text = (value or "").strip()
    if not text or text in {LEGACY_WAN_PLAY_BASE, OLD_WAN_PLAY_BASE_443}:
        return WAN_PLAY_BASE
    return text


def choose_strm_template(play_base: str, play_base_lan: str, use_lan: bool) -> str:
    """决定写进 strm 的模板。内网开关打开且填了内网地址时，不再写外网域名。"""
    lan = (play_base_lan or "").strip()
    wan = (play_base or "").strip()
    if use_lan and lan:
        return lan
    return wan or lan


def parse_networks(text: str) -> tuple[ipaddress._BaseNetwork, ...]:
    """解析网段。一条都无效时用内置私网，避免把所有人当成外网或内网。"""
    raw = text if (text or "").strip() else DEFAULT_LAN_NETWORKS
    found = []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            found.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    if found:
        return tuple(found)
    if raw == DEFAULT_LAN_NETWORKS:
        return tuple()
    return parse_networks(DEFAULT_LAN_NETWORKS)


def origin_of(url: str) -> tuple[str, str, int] | None:
    """从模板里取出 scheme、主机和端口。不是 http(s) 时返回 None。"""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname.lower().rstrip("."), port


def host_matches(host_header: str, origin: tuple[str, str, int] | None) -> bool:
    """Host 或 X-Forwarded-Host 是否就是这个入口。"""
    if origin is None:
        return False
    scheme, name, port = origin
    host, header_port = _split_host(host_header)
    if not host or host != name:
        return False
    if header_port is None:
        default = 443 if scheme == "https" else 80
        return port == default
    return header_port == port


def _split_host(value: str) -> tuple[str, int | None]:
    text = (value or "").strip().lower().rstrip(".")
    if not text:
        return "", None
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0]
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return text, None
        host = text[1:end]
        rest = text[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, None
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
    return text, None


def redirect_location(
    *,
    host: str,
    forwarded_host: str,
    peer: str,
    forwarded_for: str,
    real_ip: str,
    pickcode: str,
    filename: str,
    play_base: str,
    play_base_lan: str,
    networks: str,
) -> str | None:
    """内网客户端打到外网入口时，返回内网播放地址。

    外网客户端直接打到内网地址时，返回外网播放地址。
    返回 None 表示就在当前入口向 115 取直链。
    """
    nets = parse_networks(networks)
    wan_origin = origin_of(play_base)
    lan_origin = origin_of(play_base_lan)
    used = _apparent_host(host, forwarded_host)
    on_wan = host_matches(used, wan_origin)
    on_lan = host_matches(used, lan_origin)
    if _client_is_lan(
        host=used,
        peer=peer,
        forwarded_for=forwarded_for,
        real_ip=real_ip,
        networks=nets,
        lan_origin=lan_origin,
        wan_origin=wan_origin,
    ) and on_wan and not on_lan and (play_base_lan or "").strip():
        return build_play_url(play_base_lan, pickcode, filename)
    if _should_send_wan(
        forwarded_host=forwarded_host,
        peer=peer,
        forwarded_for=forwarded_for,
        real_ip=real_ip,
        networks=nets,
        on_lan=on_lan,
        on_wan=on_wan,
        play_base=play_base,
    ):
        return build_play_url(play_base, pickcode, filename)
    return None


def _should_send_wan(
    *,
    forwarded_host: str,
    peer: str,
    forwarded_for: str,
    real_ip: str,
    networks,
    on_lan: bool,
    on_wan: bool,
    play_base: str,
) -> bool:
    """外网播放器确实连的是内网地址时，改去外网模板。

    反代如果把 Host 改成内网、又没写 X-Forwarded-Host，这里不跳，
    避免公网请求被 302 回公网再打进来。
    """
    if not (play_base or "").strip() or not on_lan or on_wan:
        return False
    forwarded = _first_ip(forwarded_for) or _first_ip(real_ip)
    if forwarded:
        if _is_lan_ip(forwarded, networks):
            return False
        return bool((forwarded_host or "").strip())
    return bool(peer) and not _is_lan_ip(peer, networks)


def _client_is_lan(
    *,
    host: str,
    peer: str,
    forwarded_for: str,
    real_ip: str,
    networks,
    lan_origin,
    wan_origin,
) -> bool:
    """客户端自己是不是内网。

    公网域名上的对端如果是私网地址，又没有 X-Forwarded-For，多半是反代。
    这时不能把外网好友当成内网，否则会把他们 302 到 192.168.1.9。
    """
    forwarded = _first_ip(forwarded_for) or _first_ip(real_ip)
    if forwarded:
        return _is_lan_ip(forwarded, networks)
    if not _is_lan_ip(peer, networks):
        return False
    if host_matches(host, wan_origin) and not host_matches(host, lan_origin):
        return False
    return True


def _apparent_host(host: str, forwarded_host: str) -> str:
    """播放器实际使用的主机名。反代用 X-Forwarded-Host 保留原始域名。"""
    first = _first_item(forwarded_host)
    return first or (host or "").strip()


def _first_item(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return text.split(",")[0].strip()


def _first_ip(value: str) -> str:
    text = _first_item(value).strip('"').strip()
    if text.lower().startswith("for="):
        text = text[4:].strip().strip('"')
    if not text or text.lower() == "unknown":
        return ""
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end > 1 else ""
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            return host
    return text


def _is_lan_ip(value: str, networks) -> bool:
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return any(ip in network for network in networks)
