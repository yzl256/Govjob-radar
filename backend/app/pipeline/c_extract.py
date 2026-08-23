# C 类源抽取编排：入口页 → 公告链接发现 → 页面文本 → LLM 抽取 → 岗位库
# 含职位表附件展开：公告页的 xlsx 直链 / zip 附件包 → 下载解压 → 逐岗记录。
# 合规：复用 fetch.py 的 UA 与同主机限速；单源单次最多 processing_limit 个公告页。
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from app.crawler.fetch import fetch_url
from app.crawler.guokao import parse_guokao_workbook
from app.llm.extract import extract_jobs_from_text
from app.llm.htmltext import html_to_text
from app.models.job import Job
from app.store.jobs import append_jobs, load_jobs

# 公告标题锚文本关键词（发现用，宁多勿漏——LLM 侧还有 is_job_announcement 过滤）
_ANNOUNCE_RE = re.compile(
    r"招聘|招录|招考|公开招聘|选调|引进|遴选|文职|聘用|三支一扶|西部计划|特岗|社区工作者|辅导员"
)
_HREF_RE = re.compile(r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
# 职位表附件：xlsx 直链或 zip 附件包（xls 为 OLE2 老格式暂不支持，doc 不是职位表）
_ATTACH_RE = re.compile(r"\.(xlsx|zip)(?=[\"'?#&]|$)", re.I)


def discover_announcement_links(page_url: str, html: str, limit: int = 8) -> List[str]:
    """从列表页 HTML 找公告详情页链接（锚文本含招聘关键词，绝对化去重）。"""
    out: List[str] = []
    for m in _HREF_RE.finditer(html):
        href, anchor = m.group(1), _TAG_RE.sub("", m.group(2))
        if not _ANNOUNCE_RE.search(anchor):
            continue
        absu = urljoin(page_url, href.split("#")[0])
        if absu.startswith("http") and absu not in out and absu != page_url:
            out.append(absu)
        if len(out) >= limit:
            break
    return out


def discover_attachment_links(page_url: str, html: str, limit: int = 4) -> List[str]:
    """公告详情页 HTML 里的职位表附件链接（xlsx/zip；查询串与锚点剥离去重）。"""
    out: List[str] = []
    for m in _HREF_RE.finditer(html):
        href = m.group(1).split("#")[0].split("?")[0]
        if not _ATTACH_RE.search(href):
            continue
        absu = urljoin(page_url, href)
        if absu.startswith("http") and absu not in out:
            out.append(absu)
        if len(out) >= limit:
            break
    return out


def _download_to(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(fetch_url(url))
    return dest


def expand_attachment_jobs(
    html: str,
    page_url: str,
    source,
    catalogs,
    root: Path,
    apply_deadline=None,
) -> Tuple[List[Job], str]:
    """公告页附件 → 逐岗 Job 列表（下载到 data/attachments——与用户手动投放的
    data/inbox 严格分离，避免被 inbox 扫描二次解析成无截止日副本；zip 自动解出
    xlsx 再解析）。返回 (jobs, 说明)。任何失败都降级为空列表 + 原因说明。"""
    attach_dir = Path(root) / "data" / "attachments"
    links = discover_attachment_links(page_url, html)
    if not links:
        return [], "无职位表附件"
    jobs: List[Job] = []
    note_parts: List[str] = []
    for link in links:
        name = link.split("?")[0].split("/")[-1][:60] or "attachment"
        try:
            dest = _download_to(link, attach_dir / f"{source.id}__{name}")
        except Exception as e:
            note_parts.append(f"{name}: 下载失败({type(e).__name__})")
            continue
        if dest.suffix.lower() == ".zip":
            xlsx_files = _extract_xlsx_from_zip(dest, attach_dir)
            if not xlsx_files:
                note_parts.append(f"{name}: zip 内无 xlsx")
                continue
            for xf in xlsx_files:
                parsed = _safe_parse(xf, source, catalogs, page_url, apply_deadline)
                jobs.extend(parsed)
            note_parts.append(f"{name}: zip→{len(xlsx_files)}表")
        else:
            parsed = _safe_parse(dest, source, catalogs, page_url, apply_deadline)
            jobs.extend(parsed)
            note_parts.append(f"{name}: {len(parsed)}岗")
    return jobs, "；".join(note_parts) or "附件解析无结果"


def _extract_xlsx_from_zip(zip_path: Path, dest_dir: Path) -> List[Path]:
    """zip 附件包 → 解出所有 xlsx 成员（忽略目录与非 xlsx）。"""
    out: List[Path] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".xlsx"):
                    continue
                target = dest_dir / f"{zip_path.stem}__{Path(info.filename).name}"
                target.write_bytes(zf.read(info))
                out.append(target)
    except zipfile.BadZipFile:
        return []
    return out


def _safe_parse(xlsx: Path, source, catalogs, page_url: str, apply_deadline) -> List[Job]:
    try:
        return parse_guokao_workbook(
            str(xlsx), catalogs,
            source_url=page_url, apply_deadline=apply_deadline,
            path=source.path, job_prefix=source.id,
        )
    except Exception:
        return []


def process_announcement_url(
    url: str,
    llm,
    source,
    catalogs,
    root: Path,
) -> Tuple[Optional[int], str]:
    """抓一个公告页 →（附件职位表优先，否则 LLM 抽计划级）→ 入库。
    返回 (新增岗位数|None, 说明)。"""
    try:
        html_bytes = fetch_url(url)
    except Exception as e:
        return None, f"抓取失败: {type(e).__name__}"
    html = html_bytes.decode("utf-8", errors="ignore")
    text = html_to_text(html)
    if len(text) < 80:
        return None, "页面文本过短（可能是附件直链或 JS 渲染页）"

    # LLM 计划级抽取：拿截止日等公告级信息（也作为无附件时的兜底入库数据）
    plan_deadline = None
    llm_jobs: List[Job] = []
    try:
        llm_jobs = extract_jobs_from_text(text, llm, source.id, source.path, catalogs, source_url=url)
        if llm_jobs:
            plan_deadline = llm_jobs[0].apply_deadline
    except Exception as e:
        llm_err = f"LLM 抽取失败: {e}"
    else:
        llm_err = ""

    # 职位表附件优先：展开为逐岗记录（带计划级截止日）
    att_jobs, att_note = expand_attachment_jobs(
        html, url, source, catalogs, root, apply_deadline=plan_deadline
    )
    if att_jobs:
        added, _skipped = append_jobs(root, att_jobs)
        return added, f"职位表展开 {len(att_jobs)} 岗（{att_note}），新增 {added}"

    if llm_jobs:
        added, _skipped = append_jobs(root, llm_jobs)
        return added, f"抽出 {len(llm_jobs)} 岗（计划级，{att_note}），新增 {added}"
    return 0, f"未抽出岗位（{att_note}{llm_err and '；' + llm_err}）".rstrip("（）")


def harvest_c_source(
    source,
    llm,
    root: Path,
    catalogs,
    announce_limit: int = 3,
) -> Tuple[bool, str, int]:
    """一个 C 类源的单趟：入口页 → 公告链接 → 逐个处理。返回 (ok, detail, 新增岗位数)。"""
    if not source.entry:
        return False, "未配置入口 URL", 0
    try:
        html = fetch_url(source.entry)
    except Exception as e:
        return False, f"入口页抓取失败: {type(e).__name__}: {e}", 0
    links = discover_announcement_links(source.entry, html.decode("utf-8", errors="ignore"))
    if not links:
        return True, "入口页可达，未发现公告链接（非招聘季或页面改版）", 0
    total_new = 0
    notes = []
    for url in links[:announce_limit]:
        added, detail = process_announcement_url(url, llm, source, catalogs, root)
        notes.append(f"{detail}" + (f"[{added}新]" if added else ""))
        total_new += added or 0
    return True, f"{len(links)} 个公告链接，处理 {min(len(links), announce_limit)} 个：" + "；".join(notes), total_new


def c_source_jobs(root: Path) -> List[Job]:
    """岗位库全量（供 daily/H5 与 inbox 职位表合并）。"""
    return load_jobs(root)
