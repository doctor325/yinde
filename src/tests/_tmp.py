"""测试用临时目录。

**为什么不直接用 `tempfile.mkdtemp()` / `TemporaryDirectory()`。**

`mkdtemp` 是拿 `os.mkdir(file, 0o700)` 建的（自己看 `tempfile` 源码即见），
也就是「只有创建者可访问」。Windows 上这个权限位落成一条独占 DACL；普通
shell 里看不出差别，但只要测试不是同一个进程上下文直接跑 —— 经沙箱、容器
或以另一个账号执行的测试跑批 —— 目录一建出来就谁都进不去：往里写文件、
列目录、连 `chmod` 修权限都是 `PermissionError [WinError 5] 拒绝访问`。

已实测的对照（本机 Windows）：`mkdtemp` 建的 0700 目录必失败，
`makedirs` 建的默认 0755 目录同位置可正常读写。

症状很有迷惑性：报的是 `test_catalog` / `test_invariant` /
`test_frontend_volcells` 里「写临时文件失败」，看起来像业务代码的回归，
其实和被测代码没有任何关系。所以这里不判断「在不在沙箱里」，直接用
`os.makedirs` 建普通权限的目录 —— 它在两种环境下行为一致。

生产代码 (`scripts/site/make_demo_data.py`) 不受影响，那里用 `mkdtemp`
取「只有自己读得到」的隔离目录是对的。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path


def mkdtemp(prefix: str = "", dir: str | os.PathLike[str] | None = None) -> str:
    """建一个唯一的新目录并返回路径（普通权限，见模块说明）。

    参数与返回值和 `tempfile.mkdtemp()` 一致 —— 调用处可以逐字替换。
    """
    base = Path(dir) if dir is not None else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{prefix}{uuid.uuid4().hex}"
    os.makedirs(path, exist_ok=True)
    return str(path)
