"""Ansible 过滤器插件 —— 支撑「本地服务器不覆盖」的路径判定。

为什么值得单独写个插件：
  1. YAML 里的 Jinja 做「前缀匹配」非常难读（要么 namespace + from_yaml 往返，
     要么拼正则 + 反向引用），写成过滤器后调用点只剩一句 `| wf_under_any(...)`。
  2. 它是纯函数，**不用装 Ansible 就能直接单测** —— 这点很重要，
     因为本地服务器的保护是否可靠，不该依赖"跑一遍 playbook 看看"。

只包含两个过滤器：
    wf_under_any(paths, prefixes)  → 返回 paths 中落在 prefixes 任一前缀下的项
    wf_overlap(paths, prefixes)    → 同上，但返回布尔值（方便直接 assert）
"""

from __future__ import annotations

import posixpath

__all__ = ["wf_under_any", "wf_overlap", "normalize"]


def normalize(path: str) -> str:
    """把路径归一化成「以 / 开头、结尾无多余 /」的形式。

    用 posixpath 而不是 os.path：这些都是远端 Linux 上的路径，
    在 Windows 控制端上必须按 POSIX 规则解析（os.path 会按 NT 规则处理）。
    """
    if not isinstance(path, str):
        path = str(path)
    path = path.strip()
    if not path:
        return ""
    # normpath 会去掉结尾的 /、合并 //、解析 . 和 ..
    cleaned = posixpath.normpath(path)
    if not cleaned.startswith("/"):
        return ""
    return cleaned


def _is_under(path: str, prefix: str) -> bool:
    """path 是否等于 prefix，或位于 prefix 之下（按路径段判断，不是字符串前缀）。

    ★ 这里必须按「路径段」而不是字符串前缀来判断：
        /data/jenkins-data-OLD 并不是 /data/jenkins-data 的子路径，
        但它是字符串前缀。用 startswith 会误报，于是保护检查会误伤正常路径。
    """
    if not path or not prefix:
        return False
    if path == prefix:
        return True
    return path.startswith(prefix + "/")


def wf_under_any(paths, prefixes) -> list[str]:
    """返回 paths 中落在 prefixes 任一前缀之下的项（保持原顺序，去重）。"""
    if isinstance(paths, str):
        paths = [paths]
    if isinstance(prefixes, str):
        prefixes = [prefixes]

    clean_prefixes = [p for p in (normalize(x) for x in (prefixes or [])) if p]
    hits: list[str] = []
    for raw in paths or []:
        target = normalize(raw)
        if not target:
            continue
        if any(_is_under(target, prefix) for prefix in clean_prefixes):
            if target not in hits:
                hits.append(target)
    return hits


def wf_overlap(paths, prefixes) -> bool:
    """paths 与 prefixes 是否存在覆盖关系。"""
    return len(wf_under_any(paths, prefixes)) > 0


class FilterModule:
    def filters(self):
        return {
            "wf_under_any": wf_under_any,
            "wf_overlap": wf_overlap,
        }
