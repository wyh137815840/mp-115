"""从文件名和文件夹名里识别番号。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 这些词会撞上「字母-数字」规则，但不是片商前缀。
_BAD_PREFIXES = {
    "AAC",
    "ASS",
    "AVC",
    "AVI",
    "BLURAY",
    "BDRIP",
    "CHI",
    "CHN",
    "CHS",
    "CHT",
    "DTS",
    "DVDRIP",
    "EAC3",
    "FHD",
    "FLAC",
    "H264",
    "H265",
    "HD",
    "HDR",
    "HDTV",
    "HEVC",
    "ISO",
    "MKV",
    "MP4",
    "SD",
    "SDR",
    "SRT",
    "SSA",
    "SUB",
    "TRUEHD",
    "UHD",
    "WEB",
    "WMV",
    "X264",
    "X265",
}

_DOMAIN = re.compile(
    r"(?i)(?:www\.)?[a-z0-9][a-z0-9-]{0,40}\."
    r"(?:com|net|org|xyz|cc|me|tv|vip|app|la|to|cn|co|info|pw)"
    r"[@._\- ]*"
)
_FC2_PPV = re.compile(r"(?<![A-Z0-9])FC2[^A-Z0-9]{0,6}PPV[^A-Z0-9]{0,6}(\d{5,8})(?!\d)")
_FC2_ONLY = re.compile(r"(?<![A-Z0-9])FC2[^A-Z0-9]{0,6}(\d{5,8})(?!\d)")
_HEYZO = re.compile(r"(?<![A-Z0-9])HEYZO[^A-Z0-9]{0,4}(\d{3,5})(?!\d)")
_PONDO = re.compile(r"(?<!\d)(\d{6})_(\d{2,3})(?!\d)")
_CARIB = re.compile(r"(?<!\d)(\d{6})-(\d{3})(?!\d)")
_STANDARD = re.compile(r"(?<![A-Z0-9])([A-Z]{2,10})[-_ ]?(\d{2,5})(?!\d)")
_LONE_UC = re.compile(r"(?<![A-Z0-9])UC(?![A-Z0-9])")
_LONE_CH = re.compile(r"(?<![A-Z0-9])CH(?![A-Z0-9])")
_LONE_C = re.compile(r"(?<![A-Z0-9])C(?![A-Z0-9])")
_GLUED_C = re.compile(r"\dC(?:\.[A-Z0-9]+)?$")


@dataclass(frozen=True)
class AvCode:
    """识别结果。code 用来搜刮，labeled 用来重命名。"""

    code: str
    tag: str
    kind: str

    @property
    def labeled(self) -> str:
        return f"{self.code}{self.tag}"


def detect_tag(name: str) -> str:
    """字幕和破解标记。破解优先于中文字幕。"""
    upper = name.upper()
    if "破解" in name or _LONE_UC.search(upper):
        return "-UC"
    if "中文字幕" in name or "中字" in name:
        return "-C"
    if _LONE_CH.search(upper) or _LONE_C.search(upper) or _GLUED_C.search(upper):
        return "-C"
    return ""


def _stem(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    if "." not in base:
        return base
    return base.rsplit(".", 1)[0]


def _match(text: str) -> tuple[str, str] | None:
    if match := _FC2_PPV.search(text):
        return f"FC2-PPV-{match.group(1)}", "fc2"
    if match := _FC2_ONLY.search(text):
        return f"FC2-PPV-{match.group(1)}", "fc2"
    if match := _HEYZO.search(text):
        return f"HEYZO-{match.group(1)}", "uncensored"
    if match := _PONDO.search(text):
        return f"{match.group(1)}_{match.group(2)}", "uncensored"
    if match := _CARIB.search(text):
        return f"{match.group(1)}-{match.group(2)}", "uncensored"
    for match in _STANDARD.finditer(text):
        prefix = match.group(1)
        if prefix in _BAD_PREFIXES:
            continue
        return f"{prefix}-{match.group(2)}", "censored"
    return None


def parse_av(name: str) -> AvCode | None:
    """从一段文件名或文件夹名提取番号。识别不到时返回 None。"""
    if not name or not name.strip():
        return None
    tag = detect_tag(name)
    stem = _stem(name)
    cleaned = _DOMAIN.sub(" ", stem).upper()
    hit = _match(cleaned) or _match(stem.upper())
    if not hit:
        return None
    code, kind = hit
    return AvCode(_normalize(code, kind), tag, kind)


def _normalize(code: str, kind: str) -> str:
    if kind != "censored":
        return code
    prefix, _, number = code.rpartition("-")
    if not number.isdigit():
        return code
    if len(number) < 3:
        number = number.zfill(3)
    return f"{prefix}-{number}"


def merge_tag(left: str, right: str) -> str:
    if "-UC" in (left, right):
        return "-UC"
    if "-C" in (left, right):
        return "-C"
    return ""


def choose_code(file_name: str, folder_name: str) -> AvCode | None:
    """文件名优先。两边番号一致时，合并字幕标记。"""
    file_hit = parse_av(file_name)
    folder_hit = parse_av(folder_name)
    if file_hit and folder_hit and file_hit.code == folder_hit.code:
        return AvCode(file_hit.code, merge_tag(file_hit.tag, folder_hit.tag), file_hit.kind)
    return file_hit or folder_hit
