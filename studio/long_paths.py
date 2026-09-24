"""
Windows 默认 260 字符 MAX_PATH 的统一规避办法。批次副本（<项目同级>/.clonedemocker-workspaces/
batch-<32 位 id>）比原项目深 40 多个字符，Spring Security 的 saml2/oauth2 深层测试文件、Gradle
报告放进去后正好达到或超过上限。超限时裸路径不会明确报错：is_file() 静默返回 False，打开或
写入报 Errno 2，看起来像文件不存在。
One workaround for Windows' default 260-char MAX_PATH. A batch copy
(<project sibling>/.clonedemocker-workspaces/batch-<32-char id>) sits 40-odd characters deeper
than the project, which puts Spring Security's deep saml2/oauth2 test files and Gradle reports at
or past the limit. Past it, a plain path does not fail loudly: is_file() silently returns False and
open or write raises Errno 2, as if the file did not exist.
"""

from __future__ import annotations

import os
from pathlib import Path

_PREFIX = "\\\\?\\"


def long_path(path: Path) -> Path:
    r"""
    带 \\?\ 扩展长度前缀的绝对路径，之后所有 / 拼接都会带着它；非 Windows 原样返回。
    只用于 Python 自己的文件读写：cmd.exe 不接受这个前缀作当前目录，传给子进程的仍是裸路径。
    The absolute path with the \\?\ extended-length prefix, inherited by every later `/` join;
    unchanged off Windows. Only for Python's own file I/O: cmd.exe rejects the prefix as a current
    directory, so paths handed to subprocesses stay plain.
    """
    if os.name != "nt" or str(path).startswith(_PREFIX):
        return path
    return Path(_PREFIX + str(path.resolve()))
