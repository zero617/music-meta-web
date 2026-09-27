# -*- coding: utf-8 -*-
"""音频指纹识别：Chromaprint(fpcalc) 计算指纹 + AcoustID 服务识别歌曲。

用途：文件名匹配失败/不准确时，用音频内容本身识别真实歌曲
（歌名/歌手/专辑），再交给 QQ 音乐源回查封面/年份/歌词元数据。

需要：
- fpcalc 可执行文件（chromaprint 工具；Debian 可用 apt 装 libchromaprint-tools，
  或从 https://github.com/acoustid/chromaprint/releases 下载静态版）
- AcoustID API key（免费注册：https://acoustid.org/new-application）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"

# fpcalc 找不到时给出的安装/配置提示（Errno 2 太晦涩，换成人话）
_FPCALC_NOT_FOUND = (
    "未找到 fpcalc 可执行文件，请安装 chromaprint(libchromaprint-tools) "
    "或设置 FPCALC_PATH 环境变量指向 fpcalc 路径"
)


def _resolve_fpcalc() -> str:
    """按优先级探测 fpcalc 路径，避免写死 /usr/bin/fpcalc（NAS 上常没有）。

    顺序：
      1) 环境变量 FPCALC_PATH / FPCALC
      2) PATH 里的 fpcalc（shutil.which，最常见）
      3) 常见绝对路径 + 应用自带 vendor/ 目录
      4) 都找不到则返回 'fpcalc'，交给 PATH 兜底（真跑不起时抛清晰错误）
    """
    # 1) 环境变量
    for env_name in ("FPCALC_PATH", "FPCALC"):
        val = (os.environ.get(env_name) or "").strip()
        if val:
            return val
    # 2) PATH 查找
    found = shutil.which("fpcalc")
    if found:
        return found
    # 3) 常见绝对路径 + 应用自带 vendor/fpcalc
    module_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        "/usr/bin/fpcalc",
        "/usr/local/bin/fpcalc",
        "/opt/homebrew/bin/fpcalc",
        os.path.join(module_dir, "..", "vendor", "fpcalc"),
    ]
    for cand in candidates:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    # 4) 兜底：交给 PATH（执行时若真找不到，fingerprint_file 会抛清晰错误）
    return "fpcalc"


#: 模块加载时解析一次，供旧插件 `from musicmeta.fingerprint import FPCALC_DEFAULT` 使用。
#: 注意：函数默认参数用 None 触发运行时再解析，以便读取运行时才设置的环境变量。
FPCALC_DEFAULT = _resolve_fpcalc()


def recognize(path: str, api_key: str,
              fpcalc: Optional[str] = None) -> List[dict]:
    """一步完成：计算指纹 → AcoustID 识别 → 返回歌曲候选列表。

    数据源插件（qqmusic 的指纹模式）通过 musicmeta.fingerprint 调用它，
    不要删除。fpcalc 为 None 时自动探测（环境变量/PATH/常见路径）。
    """
    duration, fp = fingerprint_file(path, fpcalc)
    return lookup_acoustid(fp, duration, api_key)


def fingerprint_file(path: str, fpcalc: Optional[str] = None,
                     timeout: int = 180) -> Tuple[float, str]:
    """计算音频指纹，返回 (时长秒, fingerprint)。

    fpcalc 为 None 时自动探测路径；找不到可执行文件时抛出清晰的 RuntimeError。
    """
    exe = fpcalc or _resolve_fpcalc()
    try:
        proc = subprocess.run([exe, "-json", path],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        # 原始的 [Errno 2] No such file or directory 太晦涩，换成安装提示
        raise RuntimeError(f"{_FPCALC_NOT_FOUND}（当前尝试路径：{exe}）") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"fpcalc 失败: {(proc.stderr or proc.stdout)[:200]}")
    data = json.loads(proc.stdout)
    return float(data["duration"]), data["fingerprint"]


def lookup_acoustid(fingerprint: str, duration: float, api_key: str,
                    timeout: int = 25) -> List[dict]:
    """提交指纹到 AcoustID，返回录音候选 [{title, artists, album, score}]。

    只取歌名/歌手/专辑用于回查 QQ；年份由 QQ 专辑详情提供更准确。
    """
    params = {
        "client": api_key,
        "duration": int(round(duration)),
        "fingerprint": fingerprint,
        "meta": "recordings+releasegroups",
        "format": "json",
    }
    url = ACOUSTID_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"User-Agent": "music-meta-web/1.0 (audio fingerprint)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))

    if data.get("status") != "ok":
        err = data.get("error") or {}
        raise RuntimeError(
            f"AcoustID 查询失败: {err.get('message') or data.get('status')}")

    results: List[dict] = []
    seen: set = set()
    for result in data.get("results", []):
        for rec in result.get("recordings", []):
            title = (rec.get("title") or "").strip()
            artists = [a.get("name", "") for a in rec.get("artists", [])]
            artist = " / ".join(a for a in artists if a)
            if not title or not artist:
                continue
            rgs = rec.get("releasegroups", [])
            album = rgs[0].get("title", "") if rgs else ""
            key = (title.lower(), artist.lower())
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "title": title,
                "artist": artist,
                "album": album,
                "score": float(result.get("score", 0)),
            })
    # 按匹配分数降序
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
