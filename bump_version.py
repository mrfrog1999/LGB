"""版本号递增脚本 —— 仅在推送 GitHub 前调用。

版本号格式：vMAJOR.MINOR.PATCH（如 v1.0.0），唯一来源在 config.py 的 APP_VERSION。

递增规则：
  - PATCH 每次 +1；
  - PATCH 累加到 100 时进位到 MINOR，PATCH 归零（即 1.0.99 -> 1.1.0）；
  - MINOR 累加到 100 时进位到 MAJOR，MINOR 归零。

用法：python3 bump_version.py
"""
import re
from pathlib import Path

CONFIG = Path(__file__).with_name("config.py")
PATTERN = re.compile(r'(APP_VERSION\s*=\s*")v(\d+)\.(\d+)\.(\d+)(")')


def bump(version: str) -> str:
    major, minor, patch = (int(x) for x in version.lstrip("v").split("."))
    patch += 1
    if patch >= 100:
        patch = 0
        minor += 1
        if minor >= 100:
            minor = 0
            major += 1
    return f"v{major}.{minor}.{patch}"


def main():
    text = CONFIG.read_text(encoding="utf-8")
    m = PATTERN.search(text)
    if not m:
        raise SystemExit("未在 config.py 中找到 APP_VERSION 定义")
    old = f"v{m.group(2)}.{m.group(3)}.{m.group(4)}"
    new = bump(old)
    new_text = text[:m.start()] + f'{m.group(1)}{new}{m.group(5)}' + text[m.end():]
    CONFIG.write_text(new_text, encoding="utf-8")
    print(f"版本号已递增：{old} -> {new}")


if __name__ == "__main__":
    main()
