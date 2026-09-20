#!/usr/bin/env python
"""敏感内容扫描器 —— 防止 API Key / 私人文件被提交或推送。

用法：
    python scripts/check_secrets.py                 # 只查暂存区（pre-commit 钩子用，快）
    python scripts/check_secrets.py --tree          # 查全部已跟踪文件
    python scripts/check_secrets.py --untracked     # 暂存区 + 未跟踪文件
    python scripts/check_secrets.py --history [N]   # 额外扫历史（不带 N = 全部，较慢）
    python scripts/check_secrets.py --ci            # CI 模式：全树（历史交给 gitleaks 步骤）
    python scripts/check_secrets.py --no-color      # 关闭彩色输出

退出码：
    0 = 干净（可能有 WARN，但不阻断）
    1 = 命中 HARD 规则，应阻止本次提交/推送
    2 = 扫描器自身出错（git 不可用、规则文件损坏等）

规则与豁免清单在 scripts/secret_rules.json，改规则不用改本文件。
误报处理见 .github/SECURITY.md。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RULES_FILE = HERE / "secret_rules.json"
SELF_REL = "scripts/secret_rules.json"

HARD = "hard"
WARN = "warn"


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK(cp936)，输出中文/符号会 UnicodeEncodeError。

    钩子与 CI 都在同样的控制台里跑，所以这里统一改成 UTF-8 并容错，
    避免"扫描本身没问题、却因为打印一个 ✓ 而崩掉"。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_stdio()

# ─────────────────────────────────────────────── 颜色 ────────────────────────
class C:
    def __init__(self, enabled: bool):
        self.on = enabled

    def _w(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def red(self, t): return self._w("31;1", t)
    def yellow(self, t): return self._w("33;1", t)
    def cyan(self, t): return self._w("36", t)
    def dim(self, t): return self._w("2", t)
    def bold(self, t): return self._w("1", t)


def use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt" and not os.environ.get("WT_SESSION") and not os.environ.get("TERM"):
        # 老 conhost 对 ANSI 支持不稳，保守关掉
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return True


# ────────────────────────────────────────── git 调用 ────────────────────────
class GitError(RuntimeError):
    pass


def git(*args: str, check: bool = True) -> str:
    """以 UTF-8 容错方式调用 git，返回 stdout。"""
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(REPO),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise GitError("找不到 git 可执行文件") from exc
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout.decode("utf-8", "replace")


def git_bytes(*args: str, check: bool = True) -> bytes:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(REPO),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise GitError("找不到 git 可执行文件") from exc
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout


def in_git_repo() -> bool:
    try:
        return git("rev-parse", "--git-dir", check=False).strip() != ""
    except GitError:
        return False


# ─────────────────────────────────────── glob 匹配 ──────────────────────────
def glob_to_regex(pattern: str) -> re.Pattern:
    """转成锚定整串的匹配器。

    ** 跨目录；* 不跨目录；? 单字符。大小写不敏感（Windows/mac 友好）。
    """
    pat = pattern.replace("\\", "/")
    out, i, n = [], 0, len(pat)
    while i < n:
        ch = pat[i]
        if ch == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # 吃掉 ** 以及紧随的 /
                out.append(".*")
                i += 2
                if i < n and pat[i] == "/":
                    i += 1
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


_glob_cache: dict[str, re.Pattern] = {}


def match_glob(pattern: str, path: str) -> bool:
    rx = _glob_cache.get(pattern)
    if rx is None:
        rx = _glob_cache[pattern] = glob_to_regex(pattern)
    return bool(rx.match(path.replace("\\", "/")))


# ─────────────────────────────────────────── 规则 ───────────────────────────
class Rules:
    def __init__(self, data: dict):
        scan = data.get("scan", {})
        self.inline_allow = scan.get("inline_allow", "# secret-scan: allow")
        self.max_bytes = int(scan.get("max_file_bytes", 1024 * 1024))
        self.skip_ext = {e.lower() for e in scan.get("skip_ext", [])}
        self.skip_paths = list(scan.get("skip_paths", []))

        self.sensitive_paths = [
            e for e in data.get("sensitive_paths", [])
            if e.get("glob")
        ]
        self.content_rules = []
        for e in data.get("content_rules", []):
            if not e.get("regex"):
                continue
            try:
                e["_rx"] = re.compile(e["regex"])
            except re.error as exc:
                raise ValueError(f"规则 {e.get('id')} 正则非法: {exc}") from exc
            self.content_rules.append(e)

        allow = (data.get("allowlist") or {}).get("paths", [])
        self.allow_paths = [(e["glob"], e.get("reason", "")) for e in allow if e.get("glob")]

    def path_verdict(self, path: str) -> str | None:
        """返回命中理由（str）或 None。allowlist 优先于敏感规则。"""
        rel = path.replace("\\", "/")
        if rel == SELF_REL:
            return None
        for glob, _reason in self.allow_paths:
            if match_glob(glob, rel):
                return None
        for entry in self.sensitive_paths:
            glob = entry["glob"]
            if not match_glob(glob, rel):
                continue
            excepts = entry.get("except") or []
            if any(match_glob(ex, rel) for ex in excepts):
                continue
            return entry.get("reason", "敏感文件")
        return None

    def is_allowed_path(self, path: str) -> bool:
        rel = path.replace("\\", "/")
        return any(match_glob(g, rel) for g, _ in self.allow_paths)

    def skippable(self, path: str) -> bool:
        rel = path.replace("\\", "/")
        if rel == SELF_REL:
            return True
        if Path(rel).suffix.lower() in self.skip_ext:
            return True
        return any(match_glob(g, rel) for g in self.skip_paths)

    def content_hits(self, text: str, ext: str) -> list[tuple[str, str, str, str]]:
        """返回 [(severity, rule_id, desc, masked_snippet)]。"""
        hits: list[tuple[str, str, str, str]] = []
        lines = text.splitlines()
        seen: set[tuple[str, int]] = set()
        for rule in self.content_rules:
            only_ext = rule.get("only_ext")
            if only_ext and ext.lower() not in {e.lower() for e in only_ext}:
                continue
            for lineno, line in enumerate(lines, 1):
                if self.inline_allow and self.inline_allow in line:
                    continue
                m = rule["_rx"].search(line)
                if not m:
                    continue
                key = (rule["id"], lineno)
                if key in seen:
                    continue
                seen.add(key)
                hits.append((
                    rule.get("severity", WARN),
                    rule["id"],
                    rule.get("desc", ""),
                    mask(m.group(0)),
                ))
        hits.sort(key=lambda h: (h[0] != HARD, h[1]))
        return hits


def mask(value: str, keep: int = 4) -> str:
    """只保留前后极短片段，绝不回显完整敏感值。"""
    v = value.strip()
    if len(v) <= keep * 2:
        return v[:keep] + "****"
    return f"{v[:keep]}****{v[-2:]}"


# ──────────────────────────────────────── 结果收集 ──────────────────────────
class Finding:
    __slots__ = ("path", "severity", "rule", "desc", "snippet", "lineno", "why")

    def __init__(self, path, severity, rule, desc="", snippet="", lineno=0, why=""):
        self.path, self.severity, self.rule = path, severity, rule
        self.desc, self.snippet, self.lineno, self.why = desc, snippet, lineno, why


def check_paths(rules: Rules, paths: list[str], read) -> tuple[list[Finding], int]:
    """read(path) -> str | None（返回 None 表示应跳过）。

    返回 (findings, 实际读取并扫描的文件数)。
    """
    out: list[Finding] = []
    scanned = 0
    for rel in paths:
        rel = rel.replace("\\", "/")
        if not rel or rules.skippable(rel):
            continue
        reason = rules.path_verdict(rel)
        if reason:
            out.append(Finding(rel, HARD, "sensitive-path", reason, why=reason))
            scanned += 1
            continue  # 敏感文件不再做内容扫描，避免重复噪声
        if rules.is_allowed_path(rel):
            continue
        text = read(rel)
        if text is None:
            continue
        scanned += 1
        ext = Path(rel).suffix
        for severity, rid, desc, snippet in rules.content_hits(text, ext):
            out.append(Finding(rel, severity, rid, desc, snippet))
    return out, scanned


# ────────────────────────────────────────── 采集 ────────────────────────────
def staged_paths() -> list[str]:
    raw = git_bytes("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]


def untracked_paths() -> list[str]:
    raw = git_bytes("ls-files", "--others", "--exclude-standard", "-z")
    return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]


def tracked_paths() -> list[str]:
    raw = git_bytes("ls-files", "-z")
    return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]


def make_readers(rules: Rules):
    def read_staged(rel: str) -> str | None:
        try:
            data = git_bytes("show", f":{rel}", check=False)
        except GitError:
            return None
        if not data or len(data) > rules.max_bytes:
            return None
        return data.decode("utf-8", "replace")

    def read_disk(rel: str) -> str | None:
        p = REPO / rel
        try:
            if not p.is_file() or p.stat().st_size > rules.max_bytes:
                return None
            return p.read_bytes().decode("utf-8", "replace")
        except OSError:
            return None

    return read_staged, read_disk


# ─────────────────────────────────────── 历史扫描 ───────────────────────────
def scan_history(rules: Rules, revisions: list[str], color: C) -> list[Finding]:
    """对每个提交做 git grep，收集命中。慢，仅体检模式使用。

    git grep 的 pattern 用简化集合（高置信度密钥形态），避免把大正则塞进命令行。
    详细规则校验交给全树模式。
    """
    patterns = [
        r"sk-[A-Za-z0-9]{28,}",
        r"sk-ant-[A-Za-z0-9_\-]{20,}",
        r"ark-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}",
        r"AKIA[0-9A-Z]{16}",
        r"gh[pousr]_[A-Za-z0-9]{36,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
    args = ["grep", "-I", "-n", "-E", "|".join(patterns)]
    for rev in revisions:
        args.append(rev)
    args += ["--", ".", f":(exclude){SELF_REL}"]
    raw = git_bytes(*args, check=False).decode("utf-8", "replace")

    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for line in raw.splitlines():
        # 形如 <rev>:<path>:<lineno>:<content>
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        rev, path, lineno, content = parts
        rel = path.replace("\\", "/")
        if rules.skippable(rel) or rules.is_allowed_path(rel):
            continue
        if rules.inline_allow and rules.inline_allow in content:
            continue
        key = (rev, rel)
        if key in seen:
            continue
        seen.add(key)
        for m in re.finditer("|".join(patterns), content):
            findings.append(Finding(rel, HARD, "history", f"历史提交 {rev[:8]} 第 {lineno} 行",
                                    mask(m.group(0))))
            break
    return findings


# ────────────────────────────────────────── 输出 ────────────────────────────
def report(findings: list[Finding], color: C, mode: str, *, history_ran: bool,
           scanned: int) -> int:
    hard = [f for f in findings if f.severity == HARD]
    warn = [f for f in findings if f.severity != HARD]

    if not findings:
        print(color.cyan(f"✓ 敏感内容扫描通过（检查了 {scanned} 个文件，模式：{mode}）"))
        if history_ran:
            print(color.dim("  历史扫描：未发现疑似密钥"))
        return 0

    if hard:
        print()
        print(color.red("✗ 检测到敏感内容，已阻止本次操作"))
        print()
        for f in hard:
            loc = f"{f.path}:{f.lineno}" if f.lineno else f.path
            print(f"  {color.red('[阻止]')} {color.bold(loc)}")
            print(f"         {f.rule} — {f.desc}")
            if f.snippet and f.rule != "sensitive-path":
                print(color.dim(f"         片段: {f.snippet}"))
        print()

    if warn:
        print(color.yellow("⚠ 以下内容可疑但不阻断（请自行确认）"))
        for f in warn:
            loc = f"{f.path}:{f.lineno}" if f.lineno else f.path
            print(f"  {color.yellow('[警告]')} {loc}")
            print(f"         {f.rule} — {f.desc}")
            if f.snippet:
                print(color.dim(f"         片段: {f.snippet}"))
        print()

    if hard:
        print(color.bold("如何处理："))
        print("  1) 真实密钥 —— 从暂存区移除，改用环境变量或本地配置文件：")
        if len(hard) == 1:
            print(color.cyan(f"       git restore --staged {hard[0].path}"))
        else:
            print(color.cyan("       git restore --staged <文件>"))
        print("  2) 误报 —— 三选一：")
        print("       a. 在命中行末加注释 " + color.cyan("# secret-scan: allow"))
        print("       b. 在 scripts/secret_rules.json 的 allowlist.paths 登记该路径")
        print("       c. 本次强行通过 " + color.cyan("git commit --no-verify")
              + color.dim("（仅限确认无害时）"))
        print()
        print(color.dim("  详见 .github/SECURITY.md"))

    return 1 if hard else 0


# ─────────────────────────────────────────── main ───────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_secrets",
        description="敏感内容扫描器：阻止 API Key 与私人文件进入 git",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--untracked", action="store_true",
                   help="额外扫描未跟踪文件（提交前全面体检）")
    p.add_argument("--tree", action="store_true",
                   help="扫描全部已跟踪文件（不只看暂存区）")
    p.add_argument("--history", nargs="?", const=0, type=int, default=-1,
                   metavar="N",
                   help="额外扫描历史提交；不带数字=全部，--history 20=最近 20 个")
    p.add_argument("--ci", action="store_true",
                   help="CI 模式：扫描全部已跟踪文件（历史交给 gitleaks 步骤）")
    p.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    p.add_argument("--quiet", action="store_true", help="只输出问题")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    color = C(False if args.no_color else use_color())

    if not RULES_FILE.is_file():
        print(f"错误：找不到规则文件 {RULES_FILE}", file=sys.stderr)
        return 2
    try:
        rules = Rules(json.loads(RULES_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"错误：规则文件解析失败 — {exc}", file=sys.stderr)
        return 2

    if not in_git_repo():
        print("错误：当前目录不是 git 仓库", file=sys.stderr)
        return 2

    read_staged, read_disk = make_readers(rules)
    findings: list[Finding] = []
    scanned = 0

    try:
        if args.ci:
            # CI 里历史扫描交给 gitleaks 步骤（熵检测更强），这里专注工作树
            hits, n = check_paths(rules, tracked_paths(), read_disk)
            findings += hits
            scanned += n
            mode = "CI 全树"
            history_ran = False
        else:
            if args.tree:
                hits, n = check_paths(rules, tracked_paths(), read_disk)
                findings += hits
                scanned += n
                mode = "已跟踪全树"
            else:
                hits, n = check_paths(rules, staged_paths(), read_staged)
                findings += hits
                scanned += n
                mode = "暂存区"
            history_ran = False
            if args.untracked:
                hits, n = check_paths(rules, untracked_paths(), read_disk)
                findings += hits
                scanned += n
                mode = "已跟踪全树 + 未跟踪" if args.tree else "暂存区 + 未跟踪"
            if args.history is not None and args.history >= 0:
                revs = [r for r in git("rev-list", "--all").split() if r]
                if args.history > 0:
                    revs = revs[: args.history]
                if not args.quiet:
                    print(color.dim(f"正在扫描 {len(revs)} 个历史提交（较慢，请稍候）…"))
                findings += scan_history(rules, revs, color)
                mode += " + 历史"
                history_ran = True
    except GitError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    # 去重（同一路径同一规则只报一次）
    dedup: dict[tuple, Finding] = {}
    for f in findings:
        k = (f.path, f.lineno, f.rule, f.severity)
        dedup.setdefault(k, f)
    findings = list(dedup.values())

    return report(findings, color, mode, history_ran=history_ran, scanned=scanned)


if __name__ == "__main__":
    sys.exit(main())
