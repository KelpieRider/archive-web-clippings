#!/usr/bin/env python3
"""Run a lightweight local-first audit for a web-clipping project."""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


RELATION_FILE = "01-网站关系.md"
REPORT_FILE = "03-完整性测试.md"
ANCHOR_RE = re.compile(r"<a\b[^>]*>", re.IGNORECASE | re.DOTALL)
RESOURCE_TAG_RE = re.compile(
    r"<(?:audio|embed|iframe|img|input|link|object|script|source|track|video)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Record:
    page_id: str
    status: str
    title: str
    url: str
    parent_id: str
    relation: str
    local_page: str


@dataclass
class Reference:
    page_id: str
    kind: str
    value: str
    result: str


def split_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def read_records(project: Path) -> list[Record]:
    path = project / RELATION_FILE
    if not path.is_file():
        raise FileNotFoundError(f"缺少关系文件：{path}")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    required = {"ID", "状态", "标题", "URL", "本地页面"}
    for index, line in enumerate(lines[:-1]):
        headers = split_row(line) if "|" in line else []
        if not required.issubset(set(headers)):
            continue
        separator = split_row(lines[index + 1])
        if len(separator) != len(headers) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            continue
        records: list[Record] = []
        for row_line in lines[index + 2 :]:
            if not row_line.strip().startswith("|"):
                break
            cells = split_row(row_line)
            if len(cells) != len(headers):
                continue
            row = dict(zip(headers, cells))
            if row.get("URL"):
                records.append(
                    Record(
                        row.get("ID", "").strip(),
                        row.get("状态", "").strip(),
                        row.get("标题", "").strip(),
                        html.unescape(row.get("URL", "").strip()),
                        row.get("父ID", "").strip(),
                        row.get("关系类型", "").strip(),
                        row.get("本地页面", "").strip(),
                    )
                )
        return records
    raise ValueError("关系文件中没有找到所需的页面表格")


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def local_path(project: Path, value: str) -> Path:
    clean = unquote(value.split("#", 1)[0].split("?", 1)[0]).replace("/", os.sep)
    path = (project / clean).resolve()
    path.relative_to(project.resolve())
    return path


def existing_relative(project: Path, page: Path, value: str) -> Path | None:
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    candidate = project / unquote(parts.path).lstrip("/") if parts.path.startswith("/") else page.parent / unquote(parts.path)
    try:
        candidate = candidate.resolve()
        candidate.relative_to(project.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.exists() else None


def relative_href(source: Path, target: Path) -> str:
    return Path(os.path.relpath(target, source.parent)).as_posix()


def get_attr(tag: str, name: str) -> str | None:
    match = re.search(
        rf"\s{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        tag,
        re.IGNORECASE | re.DOTALL,
    )
    return next((group for group in match.groups() if group is not None), None) if match else None


def set_attr(tag: str, name: str, value: str) -> str:
    escaped = html.escape(value, quote=True)
    pattern = re.compile(
        rf"(\s{re.escape(name)}\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
        re.IGNORECASE | re.DOTALL,
    )
    if pattern.search(tag):
        return pattern.sub(lambda match: f'{match.group(1)}"{escaped}"', tag, count=1)
    return tag[:-1] + f' {name}="{escaped}">'


def remove_attr(tag: str, name: str) -> str:
    return re.sub(
        rf"\s{re.escape(name)}\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
        "",
        tag,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )


def rewrite_srcset(project: Path, page: Path, source_url: str, value: str) -> str:
    if value.lstrip().lower().startswith("data:"):
        return value
    items: list[str] = []
    for item in value.split(","):
        parts = item.strip().split(maxsplit=1)
        if not parts:
            continue
        url = parts[0]
        parsed = urlsplit(url)
        if not parsed.scheme and not url.startswith("//") and existing_relative(project, page, url) is None:
            url = urljoin(source_url, url)
        elif url.startswith("//"):
            url = urljoin(source_url, url)
        items.append(url + (" " + parts[1] if len(parts) > 1 else ""))
    return ", ".join(items)


def rewrite_page(project: Path, page: Path, record: Record, by_url: dict[str, Record]) -> dict[str, int]:
    content = page.read_text(encoding="utf-8-sig")
    counts = {"local": 0, "online": 0, "kept": 0}

    def anchor(match: re.Match[str]) -> str:
        tag = match.group(0)
        href = get_attr(tag, "href")
        if href is None:
            return tag
        original = html.unescape(get_attr(tag, "data-archive-original-href") or href).strip()
        parts = urlsplit(original)
        scheme = parts.scheme.lower()
        if not original or original.startswith("#") or scheme in {"data", "javascript", "mailto", "tel"}:
            counts["kept"] += 1
            return tag
        if get_attr(tag, "data-archive-original-href") is None and existing_relative(project, page, original):
            counts["kept"] += 1
            return tag
        if scheme in {"http", "https"} or original.startswith("//") or not scheme:
            absolute = urljoin(record.url, original)
            target_record = by_url.get(normalize_url(absolute))
            if target_record and target_record.status == "已保存" and target_record.local_page:
                try:
                    target = local_path(project, target_record.local_page)
                except ValueError:
                    target = project / "__invalid__"
                if target.is_file():
                    rewritten = relative_href(page, target)
                    if parts.fragment:
                        rewritten += "#" + parts.fragment
                    counts["local"] += 1
                    return set_attr(set_attr(tag, "href", rewritten), "data-archive-original-href", original)
            counts["online"] += 1
            return remove_attr(set_attr(tag, "href", absolute), "data-archive-original-href")
        counts["kept"] += 1
        return tag

    rewritten = ANCHOR_RE.sub(anchor, content)

    resource_attrs = {
        "audio": ("src",), "embed": ("src",), "iframe": ("src",),
        "img": ("src", "srcset"), "input": ("src",), "link": ("href",),
        "object": ("data",), "script": ("src",), "source": ("src", "srcset"),
        "track": ("src",), "video": ("src", "poster"),
    }

    def resource(match: re.Match[str]) -> str:
        tag = match.group(0)
        name_match = re.match(r"<\s*([a-z0-9]+)", tag, re.IGNORECASE)
        if not name_match:
            return tag
        name = name_match.group(1).lower()
        attrs = resource_attrs.get(name, ())
        if name == "link":
            rel = (get_attr(tag, "rel") or "").lower().split()
            if not set(rel).intersection({"stylesheet", "icon", "preload", "modulepreload", "manifest"}):
                return tag
        updated = tag
        for attr in attrs:
            value = get_attr(updated, attr)
            if not value:
                continue
            plain = html.unescape(value).strip()
            if attr == "srcset":
                updated = set_attr(updated, attr, rewrite_srcset(project, page, record.url, plain))
                continue
            parts = urlsplit(plain)
            if not plain or plain.startswith("#") or parts.scheme.lower() == "data":
                continue
            if parts.scheme.lower() in {"http", "https"}:
                continue
            if plain.startswith("//"):
                updated = set_attr(updated, attr, urljoin(record.url, plain))
            elif not parts.scheme and existing_relative(project, page, plain) is None:
                updated = set_attr(updated, attr, urljoin(record.url, plain))
        return updated

    rewritten = RESOURCE_TAG_RE.sub(resource, rewritten)
    if rewritten != content:
        temporary = page.with_suffix(page.suffix + ".tmp")
        try:
            temporary.write_text(rewritten, encoding="utf-8", newline="")
            os.replace(temporary, page)
        finally:
            if temporary.exists():
                temporary.unlink()
    return counts


class Auditor(HTMLParser):
    def __init__(self, project: Path, page: Path, page_id: str) -> None:
        super().__init__(convert_charrefs=False)
        self.project = project
        self.page = page
        self.page_id = page_id
        self.references: list[Reference] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs if value}
        tag = tag.lower()
        names = {"a": ("href",), "img": ("src", "srcset"), "script": ("src",), "link": ("href",),
                 "source": ("src", "srcset"), "video": ("src", "poster"), "audio": ("src",),
                 "iframe": ("src",), "object": ("data",)}.get(tag, ())
        if tag == "link":
            rel = values.get("rel", "").lower().split()
            if not set(rel).intersection({"stylesheet", "icon", "preload", "modulepreload", "manifest"}):
                names = ()
        for name in names:
            value = html.unescape(values.get(name, "")).strip()
            if not value or value.startswith("#"):
                continue
            candidates = [value]
            if name == "srcset" and not value.lstrip().lower().startswith("data:"):
                candidates = [item.strip().split()[0] for item in value.split(",") if item.strip()]
            for candidate in candidates:
                scheme = urlsplit(candidate).scheme.lower()
                if scheme in {"http", "https"} or candidate.startswith("//"):
                    self.references.append(Reference(self.page_id, f"{tag}[{name}]", candidate, "需要联网"))
                elif scheme in {"data", "javascript", "mailto", "tel"}:
                    continue
                elif scheme:
                    self.references.append(Reference(self.page_id, f"{tag}[{name}]", candidate, "无法确认长期有效"))
                elif existing_relative(self.project, self.page, candidate) is None:
                    self.references.append(Reference(self.page_id, f"{tag}[{name}]", candidate, "本地目标不存在"))


def write_index(project: Path, records: list[Record]) -> int:
    items: list[str] = []
    for record in records:
        if record.status != "已保存" or not record.local_page:
            continue
        try:
            page = local_path(project, record.local_page)
        except ValueError:
            continue
        if page.is_file():
            label = record.title or record.url
            items.append(f'<li><a href="{html.escape(Path(record.local_page).as_posix(), quote=True)}">{html.escape(label)}</a></li>')
    document = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>剪报入口</title><style>body{max-width:900px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui,sans-serif}li{margin:.45em 0}</style></head>
<body><h1>剪报入口</h1><ul>""" + "\n".join(items) + "</ul></body></html>\n"
    (project / "index.html").write_text(document, encoding="utf-8", newline="")
    return len(items)


def write_report(project: Path, checked: int, counts: dict[str, int], missing: list[Record], refs: list[Reference]) -> None:
    online = [ref for ref in refs if ref.result == "需要联网"]
    broken = [ref for ref in refs if ref.result != "需要联网"]
    lines = [
        "# 测试结果", "", "## 汇总", "",
        f"- 测试时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 检查页面：{checked}", f"- 本地页面跳转：{counts['local']}",
        f"- 原站链接：{counts['online']}", f"- 保持不变：{counts['kept']}",
        f"- 需要联网的引用：{len(online)}", f"- 无法恢复的引用：{len(broken)}", "",
        "## 丢失页面", "",
    ]
    lines.extend([f"- {record.page_id}：{record.local_page or '未填写本地路径'}" for record in missing] or ["没有。"])
    lines += ["", "## 无法恢复的引用", ""]
    lines.extend([f"- {ref.page_id} {ref.kind}：{ref.value}（{ref.result}）" for ref in broken] or ["没有。"])
    lines += ["", "## 在线依赖", ""]
    lines.extend([f"- {ref.page_id} {ref.kind}：{ref.value}" for ref in online] or ["没有。"])
    lines += ["", "## 判断", ""]
    lines.append("本地文件和链接检查通过。" if not missing and not broken else "仍有丢失页面或无法恢复的引用。")
    if online:
        lines.append("部分内容保留原站地址，需要联网才能完整显示。")
    (project / REPORT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")


def main() -> int:
    parser = argparse.ArgumentParser(description="轻量检查网站剪报并生成入口页")
    parser.add_argument("--project", required=True, type=Path, help="剪报项目目录")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        parser.error(f"项目目录不存在：{project}")
    records = read_records(project)
    by_url = {normalize_url(record.url): record for record in records}
    missing: list[Record] = []
    saved: list[tuple[Record, Path]] = []
    for record in records:
        if record.status != "已保存":
            continue
        try:
            page = local_path(project, record.local_page) if record.local_page else project / "__missing__"
        except ValueError:
            page = project / "__missing__"
        if page.is_file():
            saved.append((record, page))
        else:
            missing.append(record)
    counts = {"local": 0, "online": 0, "kept": 0}
    refs: list[Reference] = []
    for record, page in saved:
        result = rewrite_page(project, page, record, by_url)
        for key in counts:
            counts[key] += result[key]
        auditor = Auditor(project, page, record.page_id)
        auditor.feed(page.read_text(encoding="utf-8-sig"))
        refs.extend(auditor.references)
    available = write_index(project, records)
    write_report(project, len(saved), counts, missing, refs)
    broken = [ref for ref in refs if ref.result != "需要联网"]
    print(f"入口页面：{project / 'index.html'}")
    print(f"完整性报告：{project / REPORT_FILE}")
    print(f"可打开页面：{available}")
    print(f"在线依赖：{len(refs) - len(broken)}")
    print(f"丢失或无法恢复：{len(missing) + len(broken)}")
    return 1 if missing or broken else 0


if __name__ == "__main__":
    sys.exit(main())
