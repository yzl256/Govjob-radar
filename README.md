# 岗位雷达 govjob-radar

> 体制内岗位推荐系统 —— 十条路径 · 每日抓取 · 三态匹配：**你能报什么，为什么**。

个人自用工具：针对「目标省份体制内就业」的场景，把分散在各级政府网站的招聘公告/职位表聚合成结构化岗位库，对照个人档案做硬条件三态判定（✅ 可报 / ⚠️ 信息不足 / ❌ 不可报，附逐条依据），并按时间维度分为「**现在可报名投递**」与「**待报名·备考看板**」。

## 核心特性

- **十条体制内路径全覆盖**：选调生 / 国考 / 省考 / 事业单位 / 人才引进 / 国企央企 / 军队文职 / 三支一扶 / 特岗·西部计划 / 辅导员·社区工作者
- **三态匹配引擎**：不做二值判定——档案缺字段判 ⚠️「信息不足」而非误判；每条岗位附逐项判定依据（性别/年龄/学历/专业/政治面貌/户籍/双一流…）
- **专业代码级匹配**：三套专业目录（本科/学术学位/专业学位）+ 跨目录类族映射 + 别名表；研招网 6 位领域码（如 `085411` 大数据技术与工程）自动展开母类别 `0854`
- **双一流自动识别**：官方 147 所名单（第二轮 2022），简称/别名/更名前后校名可命中，独立学院绝不误判
- **两桶时间分层**：岗位按截止日分为「✅ 可报名投递」/「📚 备考看板」；10 条赛道周期看板（滚动可投 vs 年度窗口 + 倒计时 + 行动建议）
- **LLM 公告抽取**：C 类源站公告 → DeepSeek 抽取结构化岗位（LLM 只抄写原文，代码语义由本地确定性知识层解析，杜绝幻觉）
- **职位表附件展开**：公告页 xlsx/zip 附件自动下载、解压、逐岗展开入库
- **Web 界面**：档案表单（多段学历）、LLM Key 配置（存 SQLite，接口只回脱敏形式）、双页签匹配结果、来源公告直链

## 技术栈

**零依赖哲学**：除 `pydantic` 外全部使用 Python stdlib（http.server / sqlite3 / urllib / zipfile / ElementTree），YAML 用自研 miniyaml 子集加载器。部署机可平移 FastAPI / PostgreSQL，接口保持薄。

| 层 | 实现 |
|---|---|
| 后端 | Python 3.12+ · pydantic v2 |
| 前端 | 单文件 H5（`web/index.html`，无构建） |
| 存储 | SQLite（`data/govjob.db`：LLM 配置 + 档案）· JSONL（岗位库） |
| LLM | OpenAI 兼容 API（默认 DeepSeek，stdlib urllib） |

## 快速开始

```bash
cd backend
pip install -r requirements.txt        # 仅 pydantic

python -m unittest discover -s tests -v # 182 tests
python -m app.cli demo                  # 端到端演示（样例职位表→匹配→日报）
python -m app.cli serve [--port=8420]   # H5 界面 → http://127.0.0.1:8420
```

H5 页面「LLM 设置」卡填入 DeepSeek API Key（存本机 SQLite，`/models` 端点测试不耗 token），「我的档案」填学历/专业代码后点「立即匹配」。

```bash
# 每日流水线（抓取→解析→匹配→落盘→推送）
python -m app.cli daily [--no-fetch] [--watch=21600]
# C 类公告手动抽取联调
python -m app.cli extract <公告URL> [--source=xxx] [--path=6]
# 推送（Server酱微信 / SMTP / 控制台）
python -m app.cli test-notify
```

## 项目结构

```
├── backend/
│   ├── app/
│   │   ├── models/        # UserProfile / Job（资格约束全结构化）
│   │   ├── knowledge/     # 专业目录/双一流名单/别名/赛道周期看板
│   │   ├── matching/      # 三态引擎 + 专业匹配（代码/类/模糊）
│   │   ├── crawler/       # 抓取器（限速/UA）+ 国考职位表解析
│   │   ├── llm/           # DeepSeek 客户端 + 公告抽取（分权原则）
│   │   ├── pipeline/      # 每日流水线 + C 源编排（附件展开）
│   │   ├── scheduler/     # 源站注册表 + 健康记录
│   │   ├── store/         # 岗位库 JSONL + SQLite 持久层
│   │   ├── notify/        # Console / Server酱 / SMTP
│   │   └── web/           # 零依赖 Web 服务
│   └── tests/             # 182 个单测
├── config/
│   ├── sources/           # 源站注册表 YAML（national + 粤/鲁/浙）
│   ├── profiles/          # 用户档案（demo_user.json 为样例）
│   ├── majors/            # 三套专业目录 CSV + 别名表
│   └── schools/           # 双一流名单 CSV
├── web/index.html         # H5 单文件页
└── 体制内岗位推荐系统-设计文档.md
```

## 源站覆盖（2026-08 实测勘察）

| 源 | 类型 | 状态 |
|---|---|---|
| 国家公务员局（国考职位表） | A·Excel | ✅ |
| 广东选调生/省考/事业单位/三支一扶 | A/C | ✅ 真实联调入库 |
| 省国资委 + 深圳/佛山/广州/东莞市国资委（国企） | C·LLM | ✅ 4+1 源已注册 |
| 山东/浙江省考等 | A | ✅ 框架就绪 |
| 国聘网（央企聚合） | — | ⏸ JS 渲染，待 API 逆向 |
| 微信公众号 | — | ❌ 反爬验证墙，以官网栏目等价覆盖 |

新增源：在 `config/sources/<省份>.yaml` 登记入口即可，`daily` 自动按订阅省份启用（best-effort 抓取 + 健康记录 `data/source_health.json`）。

## 关键设计口径（测试固化）

1. **跨目录同码异义**：`0809` 在本科目录=计算机类、在学术目录=电子科学与技术——精确规则须带 `scope`
2. **跨目录类族**：本科"计算机类(0809)"岗，计算机硕士（0812/0835）应可报；未收录类族判 ⚠️ 不误判 ❌
3. **报考学历口径**：`HIGHEST_ONLY`（国考/省考/选调）仅最高学历专业可报；`ANY_DEGREE`（事业编/人才引进）任一符合层次学历均可
4. **LLM 分权原则**：LLM 只抄公告原文（含 evidence 引文），专业代码语义/目录归属/类族映射全由本地确定性知识层解析；找不到的字段一律 null → ⚠️
5. **有效期两桶**：截止日 ≥ 今日进「可投递」（当天截止仍可报；无截止日保留）；已截止岗位仅存档计数不展示

详见 [`backend/README.md`](backend/README.md)（模块表 + 全部口径）与[设计文档](体制内岗位推荐系统-设计文档.md)。

## 安全说明

- LLM API Key 明文只存本机私有 SQLite（`data/govjob.db`，已 gitignore），所有接口只回脱敏形式（`sk-4****0929`）
- 个人档案（出生日期等）不入库：`config/profiles/user.json` 已 gitignore，样例用 `demo_user.json`
- 抓取合规：UA 标识 + 同主机 ≥10s 限速，失败 best-effort 不阻塞

## License

个人自用，未设开源许可。
