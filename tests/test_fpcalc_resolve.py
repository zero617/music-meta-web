# -*- coding: utf-8 -*-
"""_resolve_fpcalc 探测优先级自检（无框架，assert 即可）。

运行：python tests/test_fpcalc_resolve.py
"""
import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "server"))

from musicmeta.sources.fingerprint import _resolve_fpcalc, fingerprint_file  # noqa: E402


def _clear_env():
    os.environ.pop("FPCALC_PATH", None)
    os.environ.pop("FPCALC", None)


# 1) FPCALC_PATH 环境变量优先返回
_clear_env()
os.environ["FPCALC_PATH"] = "/custom/path/fpcalc"
assert _resolve_fpcalc() == "/custom/path/fpcalc", "FPCALC_PATH 未被优先采用"

# 2) 只设 FPCALC（别名）也生效
_clear_env()
os.environ["FPCALC"] = "/alt/fpcalc"
assert _resolve_fpcalc() == "/alt/fpcalc", "FPCALC 别名未生效"

# 3) 无环境变量时用 PATH 里的 fpcalc（用一个真存在的可执行文件模拟）
_clear_env()
tmp = tempfile.mkdtemp()
fake = os.path.join(tmp, "fpcalc")
with open(fake, "w") as f:
    f.write("#!/bin/sh\necho ok\n")
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
old_path = os.environ.get("PATH", "")
os.environ["PATH"] = tmp + os.pathsep + old_path
try:
    got = _resolve_fpcalc()
    assert got == shutil.which("fpcalc") == fake, f"PATH 探测失败: {got}"
finally:
    os.environ["PATH"] = old_path

# 4) 找不到 fpcalc 时抛清晰 RuntimeError（不是晦涩的 Errno 2）
_clear_env()
try:
    fingerprint_file("/nonexistent/song.mp3", fpcalc="/definitely/not/here/fpcalc")
    raise AssertionError("应抛 RuntimeError 却没抛")
except RuntimeError as exc:
    assert "未找到 fpcalc" in str(exc), f"错误提示不清晰: {exc}"
except FileNotFoundError:
    raise AssertionError("仍抛出晦涩的 FileNotFoundError，未被捕获转换")

_clear_env()
print("所有 _resolve_fpcalc 自检通过")
