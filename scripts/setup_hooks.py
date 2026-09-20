#!/usr/bin/env python
"""把 .githooks/ 里的钩子安装到 .git/hooks/。

用法：
    python scripts/setup_hooks.py            # 安装（已存在的非本工具钩子会先备份）
    python scripts/setup_hooks.py --check    # 只检查是否已安装，不写入（CI 用）
    python scripts/setup_hooks.py --uninstall

为什么用「复制」而不是 core.hooksPath：
    复制对 VS Code / GitHub Desktop / 命令行一视同仁，且已装好的钩子不会因为
    设置项被别的工具覆盖而静默失效。
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / ".githooks"

# 钩子名 -> 需要的文件（.bat 仅在 Windows 上安装）
HOOKS = ("pre-commit", "pre-push")

# 识别"这个钩子是本工具装的"：两种形态都含 check_secrets 调用
def _looks_like_ours(text: str) -> bool:
    return "check_secrets" in text


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_stdio()


def git_dir() -> Path | None:
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    if p.returncode != 0:
        return None
    raw = p.stdout.decode("utf-8", "replace").strip()
    if not raw:
        return None
    d = Path(raw)
    return d if d.is_absolute() else (REPO / d).resolve()


def make_executable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass  # Windows 上 chmod 基本是空操作，git 仍能执行


def is_ours(path: Path) -> bool:
    try:
        return _looks_like_ours(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


def install(verbose: bool = True) -> int:
    gd = git_dir()
    if gd is None:
        print("错误：当前目录不是 git 仓库", file=sys.stderr)
        return 2
    hooks_dir = gd / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    if not SRC.is_dir():
        print(f"错误：找不到钩子源目录 {SRC}", file=sys.stderr)
        return 2

    installed, skipped = [], []
    for name in HOOKS:
        src = SRC / name
        if not src.is_file():
            skipped.append(f"{name}（源文件缺失）")
            continue
        dst = hooks_dir / name

        # 已存在且不是我们的 -> 备份，绝不覆盖别人的钩子
        if dst.exists() and not is_ours(dst):
            backup = dst.with_suffix(dst.suffix + ".bak")
            n = 1
            while backup.exists():
                backup = dst.with_suffix(dst.suffix + f".bak{n}")
                n += 1
            shutil.copy2(dst, backup)
            if verbose:
                print(f"  已备份原有钩子 -> {backup.name}")

        shutil.copy2(src, dst)
        make_executable(dst)
        installed.append(name)

    # Windows: 额外放 .bat，供 git 走 cmd 执行时使用
    if sys.platform == "win32":
        for name in HOOKS:
            src_bat = SRC / f"{name}.bat"
            if src_bat.is_file():
                shutil.copy2(src_bat, hooks_dir / f"{name}.bat")

    if verbose:
        print("敏感内容防护钩子已安装：")
        for name in installed:
            mark = ".bat" if sys.platform == "win32" else ""
            print(f"  ✓ {hooks_dir / name}{'  (+ ' + name + '.bat)' if mark else ''}")
        if skipped:
            print("  跳过：" + "、".join(skipped))
        print()
        print("  生效范围：git commit / git push 前自动扫描暂存区")
        print("  临时绕过：git commit --no-verify")
        print("  误报处理：见 .github/SECURITY.md")

    return 0


def check() -> int:
    gd = git_dir()
    if gd is None:
        print("错误：当前目录不是 git 仓库", file=sys.stderr)
        return 2
    hooks_dir = gd / "hooks"
    missing = [n for n in HOOKS if not (hooks_dir / n).is_file()]
    if missing:
        print("✗ 敏感内容防护钩子未安装：" + "、".join(missing))
        print("  请运行：python scripts/setup_hooks.py")
        return 1
    print("✓ 敏感内容防护钩子已就位：" + "、".join(HOOKS))
    return 0


def uninstall(verbose: bool = True) -> int:
    gd = git_dir()
    if gd is None:
        print("错误：当前目录不是 git 仓库", file=sys.stderr)
        return 2
    hooks_dir = gd / "hooks"
    removed = []
    for name in HOOKS:
        for candidate in (hooks_dir / name, hooks_dir / f"{name}.bat"):
            if candidate.is_file() and is_ours(candidate):
                candidate.unlink()
                removed.append(candidate.name)
        # 还原备份
        for backup in sorted(hooks_dir.glob(f"{name}.bak*")):
            target = hooks_dir / name
            if not target.exists():
                shutil.move(str(backup), str(target))
                removed.append(f"{backup.name} -> {name}")
                break
    if verbose:
        if removed:
            print("已卸载：" + "、".join(removed))
        else:
            print("没有找到本工具安装的钩子")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup_hooks",
        description="安装/检查/卸载敏感内容防护 git 钩子",
    )
    parser.add_argument("--check", action="store_true", help="只检查，不写入")
    parser.add_argument("--uninstall", action="store_true", help="卸载钩子")
    args = parser.parse_args(argv)

    if args.check:
        return check()
    if args.uninstall:
        return uninstall()
    return install()


if __name__ == "__main__":
    sys.exit(main())
