# 运维/演示 CLI（P1）
# 用法（backend/ 目录下）：
#   python -m app.cli demo                # 生成样例职位表→匹配→控制台日报
#   python -m app.cli match <xlsx> <profile.json>   # 用真实职位表+档案跑匹配
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from app.crawler.guokao import parse_guokao_workbook
from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.matching.engine import Matcher
from app.notify import ConsoleNotifier
from app.pipeline.daily import build_report

ROOT = Path(__file__).resolve().parents[2]

SAMPLE_ROWS = [
    ["中央机关及其直属机构2027年度考试录用公务员招考职位表（样例）"],
    ["部门名称", "用人司局", "招考职位", "职位简介", "招考人数", "专业", "学历", "学位", "政治面貌", "基层工作最低年限", "服务基层项目工作经历", "工作地点", "备注"],
    [
        "外交部", "网络安全和信息化处", "主任科员及以下（网络安全）", "从事网络安全管理",
        "2", "0812计算机科学与技术、0835软件工程", "硕士研究生及以上", "硕士", "中共党员", "无要求", "无要求",
        "北京市", "限应届高校毕业生",
    ],
    [
        "国家税务总局", "深圳市税务局", "一级行政执法员（计算机类）", "从事税务信息化",
        "3", "计算机类（0809）", "本科及以上", "学士", "不限", "无要求", "无要求",
        "广东省深圳市", "",
    ],
    [
        "国家统计局", "调查总队", "业务处室一级科员（统计）", "从事统计分析",
        "1", "统计学、应用统计学", "本科及以上", "学士", "不限", "无要求", "无要求",
        "浙江省杭州市", "",
    ],
    [
        "司法部", "监狱管理局", "刑罚执行（法律）", "从事刑罚执行工作",
        "2", "0301法学", "本科及以上", "学士", "中共党员或共青团员", "二年", "无要求",
        "山东省济南市", "适合男性，需通过CET-6",
    ],
    [
        "科学技术部", "国际合作司", "翻译（英语）", "从事外事翻译",
        "1", "英语、翻译", "硕士研究生及以上", "硕士", "不限", "无要求", "无要求",
        "北京市", "",
    ],
]


def _write_sample(path: Path) -> None:
    from app.io.xlsx import write_workbook

    write_workbook(str(path), "职位表", SAMPLE_ROWS)


def demo() -> int:
    inbox = ROOT / "data" / "inbox"
    outbox = ROOT / "data" / "out"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    sample = inbox / "sample_guokao_2027.xlsx"
    _write_sample(sample)
    print(f"[demo] 样例职位表: {sample}")

    catalogs = load_catalogs(ROOT)
    jobs = parse_guokao_workbook(str(sample), catalogs, source_url="https://bm.scs.gov.cn/", apply_deadline=date(2026, 10, 25))
    print(f"[demo] 解析出岗位: {len(jobs)} 条")

    from app.models.profile import UserProfile

    profile = UserProfile.model_validate_json(
        (ROOT / "config" / "profiles" / "demo_user.json").read_text(encoding="utf-8")
    )
    matcher = Matcher(catalogs, load_aliases(ROOT))
    report = build_report(profile, jobs, matcher, today=date(2026, 8, 20))

    ConsoleNotifier().send(f"{profile.name} 的岗位日报", report.render_text())
    out = outbox / "daily_report_demo.txt"
    out.write_text(report.render_text(), encoding="utf-8")
    print(f"[demo] 报告已存: {out}")
    return 0


def _pick_notifier():
    """有 SendKey 用 Server酱，否则控制台。"""
    import os

    if os.environ.get("SERVERCHAN_SENDKEY"):
        from app.notify import ServerChanNotifier

        return ServerChanNotifier()
    from app.notify import ConsoleNotifier

    return ConsoleNotifier()


def daily(fetch: bool, watch: int) -> int:
    from app.pipeline.run_daily import run_daily

    while True:
        results = run_daily(ROOT, do_fetch=fetch, notifier=_pick_notifier())
        for name, r in results.items():
            print(
                f"[daily] {name}: 可报{r['eligible']} 不足{r['insufficient']} "
                f"不可报{r['ineligible']} → {r['report_file']} 推送:{r['notified']}"
            )
        if not watch:
            return 0
        print(f"[daily] --watch：休眠 {watch}s 后再来一趟（Ctrl+C 退出）")
        time.sleep(watch)


def test_notify() -> int:
    import os

    key = os.environ.get("SERVERCHAN_SENDKEY", "")
    if not key:
        print("未设置 SERVERCHAN_SENDKEY。获取：https://sct.ftqq.com/ 扫码 → 复制 SendKey →")
        print("  PowerShell:  $env:SERVERCHAN_SENDKEY='SCTxxx'; python -m app.cli test-notify")
        return 1
    from app.notify import ServerChanNotifier

    ServerChanNotifier().send("govjob-radar 测试", "如果你在微信里看到这条消息，推送链路就通了 ✅")
    print("已发送，请查微信（关注「方糖」服务号后接收）")
    return 0


def serve(port: int) -> int:
    from app.web.server import run

    run(ROOT, port)
    return 0


def extract(target: str, source_id: str, path: int) -> int:
    """C 类公告抽取联调：URL 或本地 HTML 文件 → LLM → 岗位库。"""
    from pathlib import Path as P

    from app.llm.client import FakeLLM, HttpLLM, llm_available, parse_json_loose
    from app.pipeline.c_extract import process_announcement_url
    from app.scheduler.sources import SourceSpec

    from app.knowledge.catalogs import load_catalogs  # 两个分支共用（提前导入，避免 URL 分支未定义）

    llm = None
    env_fake = __import__("os").environ.get("LLM_FAKE_RESPONSE")
    if env_fake:
        fake_path = P(env_fake)
        if fake_path.exists():
            llm = FakeLLM(parse_json_loose(fake_path.read_text(encoding="utf-8")))
            print(f"[extract] 使用 FakeLLM（{fake_path.name}）")
        else:
            print(f"[extract] LLM_FAKE_RESPONSE 文件不存在: {env_fake}")
            return 1
    elif llm_available(root=ROOT):  # SQLite（H5 配置页）优先，环境变量兜底
        llm = HttpLLM(root=ROOT)
    else:
        print("未配置 LLM（在 H5「LLM 设置」保存，或设置 DEEPSEEK_API_KEY / LLM_API_KEY）。")
        print("离线联调：$env:LLM_FAKE_RESPONSE='fake_response.json'; python -m app.cli extract <目标>")
        return 1

    spec = SourceSpec(id=source_id, name=source_id, path=path, tier="C", extractor="llm")
    target_url = target
    if not target.startswith("http"):
        f = P(target)
        if not f.exists():
            print(f"文件不存在: {target}")
            return 1
        target_url = f"file://{f.resolve()}"

        # 本地文件走内联路径（fetch_url 不支持 file://）
        from app.llm.extract import extract_jobs_from_text
        from app.llm.htmltext import html_to_text
        from app.store.jobs import append_jobs

        text = html_to_text(f.read_text(encoding="utf-8"))
        jobs = extract_jobs_from_text(text, llm, source_id, path, load_catalogs(ROOT))
        if not jobs:
            print("[extract] 未抽出岗位")
            return 0
        added, skipped = append_jobs(ROOT, jobs)
        for j in jobs:
            print(f"  {j.title} · {j.employer} · {j.region_detail} · 招{j.quota or '?'}人 · 截止{j.apply_deadline or '?'}")
            print(f"    专业规则: {[(r.type, r.value) for r in j.major_rules]}")
        print(f"[extract] 抽出 {len(jobs)}，新增 {added}，跳过 {skipped} → data/jobs.jsonl")
        return 0

    added, detail = process_announcement_url(target_url, llm, spec, load_catalogs(ROOT), ROOT)
    print(f"[extract] {detail}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "demo":
        return demo()
    if len(argv) >= 3 and argv[1] == "extract":
        target = argv[2]
        source_id, path = "manual", 4
        for a in argv[3:]:
            if a.startswith("--source="):
                source_id = a.split("=", 1)[1]
            elif a.startswith("--path="):
                path = int(a.split("=", 1)[1])
        return extract(target, source_id, path)
    if len(argv) >= 2 and argv[1] == "daily":
        fetch = "--no-fetch" not in argv
        watch = 0
        for a in argv[2:]:
            if a.startswith("--watch="):
                watch = int(a.split("=", 1)[1])
        return daily(fetch, watch)
    if len(argv) >= 2 and argv[1] == "test-notify":
        return test_notify()
    if len(argv) >= 2 and argv[1] == "serve":
        port = 8420
        for a in argv[2:]:
            if a.startswith("--port="):
                port = int(a.split("=", 1)[1])
        return serve(port)
    if len(argv) >= 4 and argv[1] == "match":
        from app.models.profile import UserProfile

        xlsx, profile_json = Path(argv[2]), Path(argv[3])
        catalogs = load_catalogs(ROOT)
        jobs = parse_guokao_workbook(str(xlsx), catalogs)
        profile = UserProfile.model_validate_json(profile_json.read_text(encoding="utf-8"))
        matcher = Matcher(catalogs, load_aliases(ROOT))
        report = build_report(profile, jobs, matcher)
        ConsoleNotifier().send(f"{profile.name} 的岗位日报", report.render_text())
        return 0
    print(
        "用法:\n"
        "  python -m app.cli demo                        # 样例端到端\n"
        "  python -m app.cli daily [--no-fetch] [--watch=21600]\n"
        "                                                # 单趟/循环流水线\n"
        "  python -m app.cli serve [--port=8420]         # H5 界面\n"
        "  python -m app.cli extract <url|文件.html> [--source=gd-rcyj] [--path=5]\n"
        "                                                # C 类公告 LLM 抽取联调\n"
        "  python -m app.cli test-notify                 # Server酱联调\n"
        "  python -m app.cli match <xlsx> <profile.json>"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
