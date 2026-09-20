"""把 `frontend/` 里**可以公开的那部分**装进一个目录，然后过发布闸门。

用法：
    python -m scripts.site.build_artifact <输出目录>

## 为什么要多这一步，而不是直接把 frontend/ 传上去

`frontend/` 里同时躺着 `data/`（真实语料导出，33.5 MB，**永不发布**）和
`data-demo/`（自撰演示数据，MIT）。直接上传就等于把「哪些文件会公开」这件事
交给 `.gitignore` 一个文件回答 —— 而 `.gitignore` 的规则改一个字，后果是
不可逆的（§34 不许 force push、不许删历史，发出去就是发出去了）。

所以这里把「发布产物」变成一个**看得见、可以先检查**的目录：

    复制 frontend/ 的全部内容（**只排除 EXCLUDE 里点名的那一个**）
      → 断言输出里没有 data/
      → 跑 scripts/site/check_publish.py 的三条判据
      → 全通过才算装好

排除表只有一项，而且是**点名**的，不写 glob。理由：`data*` 这种写法会连
`data-demo/` 一起排掉（那演示数据就上不了线）；写成 `frontend/data/` 精确路径
则与 HistoryAI/.gitignore 里那条一致 —— 两处对同一件事的说法必须一样。

**除 data/ 之外的一切都会被复制过去**，包括你没打算发布的新文件。这是故意的：
多出来的文件会被 check_publish 的白名单判据抓住，CI 当场失败等人来看，
而不是被这里悄悄过滤掉、谁也不知道。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pipeline import config  # noqa: E402
from scripts.site import check_publish  # noqa: E402

# 唯一一项，点名，不写 glob。见模块开头。
EXCLUDE = ["data"]
EXPECT_EXCLUDED = ["data"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = Path(argv[1]).resolve()
    src = config.HISTORY_AI_DIR / "frontend"
    if not src.is_dir():
        print(f"找不到前端目录：{src}")
        return 2
    if not (out.parent).is_dir():
        print(f"输出目录的上级不存在：{out.parent}")
        return 2
    if out.exists():
        shutil.rmtree(out)

    print(f"装配发布产物：{src}\n         → {out}")
    shutil.copytree(src, out,
                    ignore=shutil.ignore_patterns(*EXCLUDE))
    for name in EXPECT_EXCLUDED:
        p = out / name
        if p.exists():
            print(f"**装配失败：{name}/ 被复制进了产物** —— 它是真实语料导出，"
                  f"绝不能公开（§5）")
            return 1
    n = sum(1 for p in out.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"已排除：{'、'.join(EXCLUDE)}/（真实语料导出，永不发布）")
    print(f"产物：{n} 个文件，{size / 1024:.0f} KB\n")

    # 闸门跑在**产物本身**上，不是跑在 frontend/ 上 —— 检查的就是要传上去的那份。
    rc = check_publish.main(["check_publish", str(out)])
    if rc != 0:
        print("\n装配中止：产物没通过发布闸门，不生成可上传的目录。")
        shutil.rmtree(out, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
