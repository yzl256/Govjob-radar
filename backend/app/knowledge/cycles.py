# 赛道周期看板：10 条体制内路径的「现在可投递 / 待报名仅备考」分层。
# 数据来源：广东历年招考时间规律（估计值，窗口以当年公告为准）——
# 与岗位级截止日判定（apply_deadline）互补：周期赛道在非报名期没有在招岗位，
# 但用户需要知道"什么时候报名、现在该做什么"。
from __future__ import annotations

from datetime import date
from typing import List, Optional

from app.models.profile import UserProfile

# rolling=True → 全年滚动招聘（散招，随时可投）
# window=(open_month, close_month) → 年度集中报名窗口（可跨年，如省考 12-1 月）
_TRACKS = [
    {
        "path": 1, "name": "选调生", "rolling": False, "window": (12, 12),
        "cycle": "每年12月前后报名，次年1-3月笔试面试",
        "desc": "广东定向选调面向下届在校应届毕业生；毕业后（含择业期）不能报考（广东口径，多省类似）",
        "advice": "在校最后一年的12月是唯一窗口，提前备院校推荐表与党员/学生干部材料",
    },
    {
        "path": 2, "name": "国考", "rolling": False, "window": (10, 10),
        "cycle": "10月中旬报名，11月底-12月初笔试",
        "desc": "招考大户（税务/海关/网信/统计等）；择业期2年内未落实工作者可按应届生身份报考大部分岗位",
        "advice": "现在系统刷行测+申论；10月职位表发布当天立即筛岗——专硕代码先查目录归属再报",
    },
    {
        "path": 3, "name": "省考", "rolling": False, "window": (12, 1),
        "cycle": "广东12月-次年1月报名，春节后笔试",
        "desc": "广东省考择业期大部分岗位有效；职位表按地市拆分，留意专业目录口径（专硕 vs 学硕）",
        "advice": "与国考共用行测+申论备考体系，广东加考「思维能力测验」需专项练习",
    },
    {
        "path": 4, "name": "事业单位", "rolling": True, "window": (2, 4),
        "cycle": "全省统考2-4月集中报名；地市/单位自主招聘全年滚动",
        "desc": "两条通道：①全省统考（集中笔试）；②自主招聘/高层次引进（散招，硕士常见免笔试直接面试）",
        "advice": "现在就可投散招岗位（各地人社局官网滚动发布）；统考备考公共基础知识",
    },
    {
        "path": 5, "name": "人才引进", "rolling": True, "window": None,
        "cycle": "全年滚动，8-10月大湾区秋招高峰",
        "desc": "地市人才办/人社局单独发布，无统一大表；硕士层次多为「免笔试+综合面试」",
        "advice": "现在可投——盯紧各地人社局/人才办公众号，珠三角城市逐月都有新公告",
    },
    {
        "path": 6, "name": "国企央企", "rolling": True, "window": (9, 11),
        "cycle": "秋招9-11月高峰（8月提前批），春招3-4月次高峰",
        "desc": "烟草/电网/银行/电信等各自官网招聘系统，节奏快、周期短（网申到 offer 常在1个月内）",
        "advice": "8月底开始投提前批与正式批；网申简历按「学历+专业代码+项目」三要素准备",
    },
    {
        "path": 7, "name": "军队文职", "rolling": True, "window": (10, 11),
        "cycle": "管理技术岗统考10-11月报名；技能岗全年滚动",
        "desc": "统考考公共科目+专业科目；部分理工类硕士岗有免笔试专项（以当年岗位计划为准）",
        "advice": "统考备考基本知识+岗位能力；技能岗现在就可投（军队人才网滚动发布）",
    },
    {
        "path": 8, "name": "三支一扶", "rolling": False, "window": (4, 6),
        "cycle": "广东4-6月报名，服务期2年",
        "desc": "基层服务项目；服务期满考核合格可享定向考公/事业编定向招聘优惠",
        "advice": "已过本年度窗口；若以考公为主目标，此赛道优先级可放低",
    },
    {
        "path": 9, "name": "特岗/西部计划", "rolling": False, "window": (4, 5),
        "cycle": "4-5月集中报名",
        "desc": "西部计划/特岗教师面向应届与毕业期内青年；服务期满有考研加分与定向招录",
        "advice": "已过本年度窗口；下一轮明年4月，如需保留应届身份可关注",
    },
    {
        "path": 10, "name": "辅导员/社区工作者", "rolling": True, "window": None,
        "cycle": "高校辅导员随学期招聘（5-6月、11-12月两波）；社区工作者散招全年",
        "desc": "高校辅导员多要求党员+学生干部经历；社区工作者门槛较低、地市人社局发布",
        "advice": "高校岗盯目标院校人事处官网；社区岗作为保底选项滚动可投",
    },
]


def _in_window(month: int, window) -> bool:
    o, c = window
    if o <= c:
        return o <= month <= c
    return month >= o or month <= c  # 跨年窗口（如 12-1 月）


def _next_open(today: date, open_month: int) -> date:
    year = today.year + (1 if today.month >= open_month else 0)
    return date(year, open_month, 1)


def _user_note(track: dict, profile: Optional[UserProfile], today: date) -> Optional[str]:
    """赛道 × 档案的交互提示（目前只有选调生有硬性毕业校验）。"""
    if track["path"] == 1 and profile is not None:
        grads = [r.graduation_date for r in profile.education if r.graduation_date]
        if grads and max(grads) < today:
            ym = f"{max(grads).year}年{max(grads).month}月"
            return f"你已于{ym}毕业：广东定向选调仅限在校应届生报考，此赛道（广东）对你已关闭"
    return None


def track_board(profile: Optional[UserProfile] = None, today: Optional[date] = None) -> List[dict]:
    """10 条赛道的当前阶段看板。
    phase: "open"=现在就有可投通道（滚动或窗口期内）/ "prep"=待报名仅备考。"""
    today = today or date.today()
    board = []
    for t in _TRACKS:
        phase = "prep"
        countdown = None
        parts = []
        if t["rolling"]:
            phase = "open"
            parts.append("全年滚动可投")
        if t["window"]:
            if _in_window(today.month, t["window"]):
                phase = "open"
                parts.append("集中报名窗口进行中")
            else:
                d = _next_open(today, t["window"][0])
                countdown = (d - today).days
                parts.append(f"集中窗口预计 {d.year}年{t['window'][0]}月开启")
        board.append(
            {
                "path": t["path"],
                "name": t["name"],
                "phase": phase,
                "window": " · ".join(parts) if parts else "—",
                "countdown": countdown,
                "cycle": t["cycle"],
                "desc": t["desc"],
                "advice": t["advice"],
                "user_note": _user_note(t, profile, today),
            }
        )
    return board
