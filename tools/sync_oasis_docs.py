# -*- coding: utf-8 -*-
"""绿洲启元官方 API / Wiki 同步器（标准库实现，兼容 Python 3.7+）

数据源（均公开，无需登录）：
  https://developer.gp.qq.com/api/{family}/list/sorted_list.json   索引
  https://developer.gp.qq.com/api/{path}.json                      详情
  https://developer.gp.qq.com/wikieditor/_api/look-Category        Wiki 分类
  https://developer.gp.qq.com/wikieditor/_api/query-articles?Id=N  Wiki 正文

用法：
  python sync_oasis_docs.py --out <输出目录> --workers 8
  python sync_oasis_docs.py --out <目录> --families class
  python sync_oasis_docs.py --out <目录> --wiki
  python sync_oasis_docs.py --out <目录> --index-only
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
API_BASE = "https://developer.gp.qq.com/api"
WIKI_BASE = "https://developer.gp.qq.com/wikieditor"
FAMILIES = ["class", "cppenum", "cppstruct", "globalfunc"]

_fail = []


def log(msg):
    sys.stdout.write(str(msg) + "\n")
    sys.stdout.flush()


def quote_url(url):
    """只对非 ASCII 和空格做编码，保留 URL 结构字符。"""
    return urllib.parse.quote(url, safe=":/?=&%")


def fetch_json(url, retries=4, timeout=30):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 * (i + 1))
    raise last


def walk(node, out):
    """把 {a:{b:{name:path}}} 三层索引摊平成 [(name, path)]。"""
    if isinstance(node, dict):
        for key, val in node.items():
            if isinstance(val, str):
                out.append((key, val))
            else:
                walk(val, out)
    elif isinstance(node, list):
        for item in node:
            walk(item, out)


def cell(text):
    """Markdown 表格单元格转义。"""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = s.replace("|", "\\|")
    s = " ".join(s.split())
    return s


def class_to_markdown(data, source_path):
    lines = []
    name = data.get("Name") or os.path.basename(source_path).replace(".json", "")
    lines.append("# " + str(name))
    lines.append("")

    lang = data.get("Language")
    parents = data.get("Parents")
    meta = []
    if parents:
        if isinstance(parents, list):
            meta.append("父类：" + ", ".join(str(p) for p in parents))
        else:
            meta.append("父类：" + str(parents))
    if lang:
        meta.append("语言：" + str(lang))
    meta.append("来源：" + source_path)
    lines.append("> " + " ｜ ".join(meta))
    lines.append("")

    desc = (data.get("Description") or "").strip()
    if desc:
        lines.append(desc)
        lines.append("")

    variables = data.get("Variables") or []
    if variables:
        lines.append("## 变量（%d）" % len(variables))
        lines.append("")
        lines.append("| 名称 | 类型 | 说明 |")
        lines.append("| --- | --- | --- |")
        for var in variables:
            lines.append("| `%s` | `%s` | %s |" % (
                cell(var.get("Name")), cell(var.get("Type")), cell(var.get("Description"))))
        lines.append("")

    functions = data.get("Functions") or []
    if functions:
        lines.append("## 函数（%d）" % len(functions))
        lines.append("")
        for fn in functions:
            params = fn.get("Params") or []
            rets = fn.get("Return") or []
            sig_params = ", ".join(
                "%s: %s" % (p.get("Name") or "", p.get("Type") or "") for p in params)
            sig_return = ", ".join(str(r.get("Type") or "void").strip() for r in rets) or "void"
            lines.append("### `%s(%s) -> %s`" % (fn.get("Name") or "", sig_params, sig_return))
            fdesc = (fn.get("Description") or "").strip()
            if fdesc:
                lines.append("")
                lines.append(fdesc)
            if params:
                lines.append("")
                lines.append("| 参数 | 类型 | 说明 |")
                lines.append("| --- | --- | --- |")
                for p in params:
                    lines.append("| `%s` | `%s` | %s |" % (
                        cell(p.get("Name")), cell(p.get("Type")), cell(p.get("Description"))))
            if rets:
                lines.append("")
                lines.append("返回值：`%s`" % sig_return)
            lines.append("")

    for key, title in (("Event", "事件"), ("Delegate", "委托")):
        block = data.get(key)
        if not block:
            continue
        lines.append("## %s" % title)
        lines.append("")
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    lines.append("- `%s` %s" % (
                        cell(item.get("Name")), cell(item.get("Description"))))
                else:
                    lines.append("- `%s`" % cell(item))
        else:
            lines.append(str(block))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_text(path, text):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def normalize_path(src_path, family):
    """class/detail/XX/Y/Name.json -> class/XX/Y/Name.md"""
    p = src_path.replace("\\", "/").lstrip("/")
    if p.endswith(".json"):
        p = p[:-5]
    if p.startswith(family + "/detail/"):
        p = family + "/" + p[len(family + "/detail/"):]
    return p + ".md"


def sync_family(family, out_dir, workers, force):
    log("[%s] 拉取索引 ..." % family)
    index_url = "%s/%s/list/sorted_list.json" % (API_BASE, family)
    try:
        index = fetch_json(index_url)
    except Exception as exc:  # noqa: BLE001
        log("[%s] 索引失败：%s" % (family, exc))
        return 0, 0

    items = []
    walk(index, items)
    log("[%s] 索引条目 %d" % (family, len(items)))

    detail_dir = os.path.join(out_dir, "docs", "api", family)
    tasks = []
    for name, path in items:
        rel = normalize_path(path, family)
        target = os.path.join(out_dir, "docs", "api", rel)
        if (not force) and os.path.exists(target):
            continue
        tasks.append((name, path, target))
    log("[%s] 待下载 %d（已存在 %d）" % (family, len(tasks), len(items) - len(tasks)))

    ok = 0
    fail = 0

    def work(item):
        name, path, target = item
        url = "%s/%s" % (API_BASE, path)
        data = fetch_json(quote_url(url))
        write_text(target, class_to_markdown(data, path))
        return target

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, t): t for t in tasks}
        done = 0
        for fut in as_completed(futures):
            done += 1
            name, path, target = futures[fut]
            try:
                fut.result()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                fail += 1
                _fail.append((family, path, str(exc)))
                log("[%s] 失败 %s : %s" % (family, name, exc))
            if done % 100 == 0:
                log("[%s] 进度 %d/%d" % (family, done, len(tasks)))

    log("[%s] 完成 ok=%d fail=%d" % (family, ok, fail))
    return ok, fail


def sync_wiki(out_dir):
    log("[wiki] 拉取分类 ...")
    try:
        cat = fetch_json("%s/_api/look-Category" % WIKI_BASE)
    except Exception as exc:  # noqa: BLE001
        log("[wiki] 分类失败：%s" % exc)
        return 0, 0

    ids = []

    def collect(node):
        if isinstance(node, dict):
            node_id = node.get("id") or node.get("Id")
            node_type = node.get("type") or node.get("Type")
            if node_id is not None and (node_type in (1, "1", None)):
                ids.append((str(node_id), node.get("label") or node.get("Title") or str(node_id)))
            for val in node.values():
                collect(val)
        elif isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, str):
            try:
                parsed = json.loads(node)
            except Exception:  # noqa: BLE001
                return
            collect(parsed)

    collect(cat)

    seen = set()
    uniq = []
    for item_id, title in ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        uniq.append((item_id, title))
    log("[wiki] 文章 %d" % len(uniq))

    wiki_dir = os.path.join(out_dir, "docs", "wiki")
    ok = 0
    fail = 0
    for item_id, title in uniq:
        target = os.path.join(wiki_dir, "%s_%s.md" % (item_id, safe_name(title)))
        if os.path.exists(target):
            ok += 1
            continue
        try:
            data = fetch_json(quote_url("%s/_api/query-articles?Id=%s" % (WIKI_BASE, item_id)))
            body = ""
            rows = data.get("data") or []
            for row in rows:
                body += (row.get("Body") or "") + "\n\n"
            write_text(target, "# %s\n\n> wiki id: %s\n\n%s" % (title, item_id, body.strip()))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            _fail.append(("wiki", item_id, str(exc)))
            log("[wiki] 失败 %s : %s" % (item_id, exc))
    log("[wiki] 完成 ok=%d fail=%d" % (ok, fail))
    return ok, fail


def safe_name(text):
    bad = '\\/:*?"<>|\r\n\t'
    out = "".join("_" if ch in bad else ch for ch in str(text))
    return out.strip() or "untitled"


def build_index(out_dir):
    api_root = os.path.join(out_dir, "docs", "api")
    lines = ["# 绿洲启元官方 API 索引", "", "> 由 sync_oasis_docs.py 自动生成", ""]
    if os.path.isdir(api_root):
        for family in FAMILIES:
            fam_dir = os.path.join(api_root, family)
            if not os.path.isdir(fam_dir):
                continue
            files = []
            for root, _dirs, names in os.walk(fam_dir):
                for name in names:
                    if name.endswith(".md"):
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, out_dir).replace("\\", "/")
                        files.append((os.path.splitext(name)[0], rel))
            if not files:
                continue
            files.sort(key=lambda x: x[0].lower())
            lines.append("## %s（%d）" % (family, len(files)))
            lines.append("")
            lines.append("| 名称 | 文件 |")
            lines.append("| --- | --- |")
            for name, rel in files:
                lines.append("| `%s` | [%s](%s) |" % (name, rel, rel))
            lines.append("")

    wiki_dir = os.path.join(out_dir, "docs", "wiki")
    if os.path.isdir(wiki_dir):
        files = sorted(
            name for name in os.listdir(wiki_dir) if name.endswith(".md"))
        if files:
            lines.append("## wiki（%d）" % len(files))
            lines.append("")
            for name in files:
                rel = "docs/wiki/" + name
                lines.append("- [%s](%s)" % (os.path.splitext(name)[0], rel))
            lines.append("")

    write_text(os.path.join(out_dir, "INDEX.md"), "\n".join(lines))
    log("[index] 写入 INDEX.md")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--families", default=",".join(FAMILIES))
    parser.add_argument("--wiki", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    if args.index_only:
        build_index(out_dir)
        return 0

    total_ok = 0
    total_fail = 0
    for family in [f.strip() for f in args.families.split(",") if f.strip()]:
        ok, fail = sync_family(family, out_dir, args.workers, args.force)
        total_ok += ok
        total_fail += fail

    if args.wiki:
        ok, fail = sync_wiki(out_dir)
        total_ok += ok
        total_fail += fail

    build_index(out_dir)

    log("")
    log("总计 ok=%d fail=%d" % (total_ok, total_fail))
    if _fail:
        log("失败明细（前 20）：")
        for item in _fail[:20]:
            log("  %s | %s | %s" % item)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
