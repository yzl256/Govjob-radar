# govjob-radar backend（P2）

体制内岗位推荐系统 · 后端。当前进度：**P2 可日用**——调度抓取（best-effort+健康记录）→ 职位表解析 → 三态匹配 → H5 界面 + 推送，186 个单测全绿。

## 快速开始

**开箱即用**：仓库根双击 `启动.bat`（Windows）或 `./start.sh`（macOS/Linux）——自动装依赖、全新环境首次启动播种样例职位表（已有数据绝不覆盖，`data/.seeded` 标记防重复）、起服务并开浏览器。

```bash
pip install -r requirements.txt   # 仅 pydantic
python -m unittest discover -s tests -v   # 186 tests
python -m app.cli demo                    # 端到端演示：生成样例职位表→解析→匹配→日报
python -m app.cli daily [--no-fetch] [--watch=21600]
                                          # 单趟/循环流水线：抓取→解析→匹配→落盘→推送
python -m app.cli serve [--port=8420] [--no-open]
                                           # H5 界面（档案表单+LLM设置+订阅省份+双页签结果）
                                           # 全新环境首次启动自动播种样例职位表并开浏览器
python -m app.cli extract <url|文件.html> [--source=gd-rcyj] [--path=5]
                                          # C 类公告 LLM 抽取联调（需 LLM Key：H5 右上角齿轮→弹窗保存到 SQLite，
                                          # 或环境变量 DEEPSEEK_API_KEY/LLM_API_KEY；离线回放：
                                          # $env:LLM_FAKE_RESPONSE='..\data\fake_response.json'）
python -m app.cli test-notify             # Server酱联调（需 SERVERCHAN_SENDKEY）
python -m app.cli match ..\data\inbox\sample_guokao_2027.xlsx ..\config\profiles\user.json  # 真实档案（085411）
```

## 模块

| 路径 | 职责 |
|---|---|
| `app/models/profile.py` | 用户档案（出生日期而非年龄；多段学历各带专业代码；订阅省份） |
| `app/models/job.py` | 岗位统一模型（资格约束全结构化）+ `parse_birth_after("1998年7月以后出生")` |
| `app/knowledge/catalogs.py` | 三套专业目录加载（本科/学术/专业学位）+ 目录推断 + 6位领域码→母类别 |
| `app/knowledge/schools.py` | 双一流高校知识表（官方 147 所名单，`config/schools/`）+ 安全匹配（精确/别名/受限前缀；独立学院不误判、未知≠不是） |
| `app/knowledge/cycles.py` | 赛道周期看板：10 条路径的「现在可投递 / 待报名仅备考」分层（滚动 vs 年度窗口估计 + 倒计时 + 档案交互提示，如广东选调仅限在校应届） |
| `app/knowledge/alias.py` | 模糊写法别名表 + 跨目录类族映射（`major_aliases.json`） |
| `app/knowledge/major_parse.py` | 公告专业列文本 → 统一 MajorRule（5 种写法） |
| `app/matching/majors.py` | 专业匹配：any/精确代码/类门类前缀/模糊写法 → True/False/None |
| `app/matching/engine.py` | 硬过滤引擎：✅可报 / ⚠️信息不足 / ❌不可报（附逐条原因） |
| `app/io/xlsx.py` | 零依赖 xlsx 读写器（zipfile + ElementTree） |
| `app/io/miniyaml.py` | 零依赖 YAML 子集加载器（覆盖 config/sources 全部用法） |
| `app/crawler/guokao.py` | 国考职位表解析（表头自动定位 + 列别名映射 + 行→Job） |
| `app/crawler/fetch.py` | 抓取器：UA 标识 / 同主机≥10s 限速 / xlsx 附件发现下载 |
| `app/llm/htmltext.py` | HTML→干净文本（stdlib，保留链接锚定界，超长截断） |
| `app/llm/client.py` | LLM 客户端：OpenAI 兼容（默认 DeepSeek，urllib 零依赖）+ FakeLLM 测试替身 |
| `app/llm/extract.py` | C 类公告抽取：prompt + JSON 容错解析 + 归一化 Job（专业原文交本地知识层，LLM 不猜代码） |
| `app/store/jobs.py` | 岗位库 data/jobs.jsonl：追加去重 + 加载（部署机换 PostgreSQL 接口不变） |
| `app/store/db.py` | SQLite 持久层 data/govjob.db：`llm_config`（齿轮弹窗保存的供应商/Key/模型，base_url 由后端注册表解析）+ `user_profile`（个人档案，JSON 文档列；WAL + 短连接；旧库自动迁移） |
| `app/pipeline/c_extract.py` | C 源编排：入口页→公告链接发现→抽取→入库 |
| `app/scheduler/sources.py` | 源站注册表加载（全国恒启 + 按订阅省份启用）+ 健康记录 |
| `app/pipeline/daily.py` | 日报管道：匹配 → 三态分组 → 可渲染报告 |
| `app/pipeline/run_daily.py` | 单趟流水线：抓取(best-effort)→inbox 解析→匹配→落盘→推送 |
| `app/notify/` | Console / Server酱(微信) / SMTP邮件 适配器 |
| `app/web/server.py` | 零依赖 Web 服务（stdlib http + pydantic 校验，API 薄可平移 FastAPI） |
| `app/cli.py` | `demo` / `daily` / `serve` / `test-notify` / `match` 命令 |

`web/index.html`（仓库根 `web/`）为 H5 单文件页：顶栏齿轮弹窗配置大模型（供应商→Key→验证→选型，base_url 后端封装）、档案表单、多段学历编辑、订阅省份勾选、双页签结果（✅ 可报名投递 / 📚 备考看板——10 条赛道周期与行动建议）。

国企央企岗位来源（path=6，均已实测勘察 2026-08-23）：
- `gd-soe-gzw` 省国资委企业动态栏（LLM 入库 10 岗·宏大爆破）
- `gd-soe-szgzw` 深圳国资委·校园招聘专栏（8 公告链接，2026 春/秋招简章已入库）
- `gd-soe-fsgzw` 佛山国资委·国企招聘专题（企业官网直链可抽；公众号文章被微信验证墙拦）
- `gd-soe-gzgzw` 广州国资委·通知公告（job168 国企专区 JS 渲染暂不可抓）
- `gd-soe-dggzw` 东莞国资委（入口 502，best-effort 健康记录跟进）
- `cn-soe-iguopin` 国聘网（整站 SPA，entry 置 null 缓启用，待 API 逆向）
- 公众号路线关闭：mp.weixin.qq.com 反爬验证墙（实测"环境异常"59 字），同内容以官网栏目等价覆盖

## 三个关键口径（测试已固化行为）

1. **跨目录同码异义**：0809 在本科目录=计算机类，在学术学位目录=电子科学与技术。精确口径的岗位规则应带 `scope` 限定目录。
2. **跨目录类族**：本科"计算机类(0809)"岗，计算机硕士（0812/0835）应可报——类族映射（`class_families`）解决"类"要求的跨学历解释；未收录类族一律 ⚠️ 人工确认，不误判 ❌。同理，岗位只写本科 6 位代码而考生是研究生时也判 ⚠️。
3. **报考学历口径** `MajorPolicy`：`HIGHEST_ONLY`（国考/省考/选调默认）仅最高学历专业可报；`ANY_DEGREE`（事业编/人才引进默认）任一符合层次的学历专业均可。岗位级可覆盖路径默认。
4. **专业学位领域码**：研招网 6 位领域码（如 085411 大数据技术与工程）自动展开出母类别 0854 双候选——"0854电子信息"类岗位规则直接命中领域码考生（已用真实档案验证）。
5. **双一流自动识别**：档案只填校名即可——引擎查官方 147 所名单（第二轮，2022）自动判定；`人大`/`哈工大`/`西电`等简称、全半角括号、更名前后校名（第二/四军医大学、上海体育大学）均可命中；独立学院（"浙江大学城市学院"）绝不因母体误判；查不到一律 ⚠️"核对校名全称"而非 ❌。H5 院校输入框实时显示 🎓 徽标。
6. **C 类 LLM 抽取的分权原则**：LLM 只做"从公告原文抄写"（含 evidence 引文），专业代码语义、目录归属、类族映射全部由本地确定性知识层解析——LLM 输出 `0854 电子信息` 原文片段，本地才把它变成可匹配的规则。找不到的字段一律 null → ⚠️。岗位 id 由 source_id+URL 哈希稳定生成，重复公告自动去重。
7. **两桶分层（可投递 vs 备考）**：岗位按截止日二分（`split_by_deadline`）——H5「✅ 可报名投递」页只展示截止日 ≥ 今天的岗位（http 来源出「公告原文 ↗」链接，inbox 本地职位表出「来源职位表：文件名」）；「📚 备考看板」页只放赛道周期看板（`track_board`：🟢 滚动可投 / 📚 备考期 + 窗口倒计时），**已截止岗位不做参考展示**（仅 `archived_expired` 计数）。窗口时间为广东历年规律估计，以当年公告为准。
8. **有效期过滤**：`filter_active_jobs`——日报与 H5「可投递」页只展示截止日 ≥ 今天的岗位（当天截止仍可报；无截止日保留，无法判定不误删）；过期岗位留在岗位库存档，绝不进入可投递列表。
9. **职位表附件展开**：公告页的 xlsx 直链/zip 附件包自动下载到 `data/attachments/`（机器管理区，与用户手动投放的 `data/inbox` 严格分离——否则会被 inbox 扫描二次解析成无截止日副本），通用职位表解析器逐岗展开，LLM 只负责回填计划级截止日。id 用岗位代码内容寻址（改版/重跑不撞车）。省级自编目录代码（如广东 A01/B02）按原文进 TEXT 规则判 ⚠️，绝不瞎猜成 ✅/❌。

## 已知种子限制（import 脚本待做）

- 本科目录为子集（~150/770+ 专业）；学术/专业学位目录缺军事学等个别门类；
- 专科学历层次暂无目录（候选构建会给出"信息不足"而非误判）；
- 别名表仅 9 组，未收录别名一律判 ⚠️ 并提示人工确认。

## 环境备注（本机开发实测）

- ~~外网 TLS 整体不可用~~ **已解决**：沙箱拦截 TLS，升级 `danger-full-access` 后正常。2026-08-23 真实源+真实 LLM 联调完成（DeepSeek API + 广东 3 条 2026 公告入库，见 `config/sources/广东省.yaml` 普查注记）；
- 抓取失败不阻塞流水线：健康记录落盘 `data/source_health.json`（OK/FAIL + 原因）；
- `zj-shengkao` 入口 404 待勘察修正（源站注册表 status 仍为 pending_survey）；
- LLM Key 两种配置方式：**H5 齿轮弹窗保存 → SQLite `data/govjob.db`（推荐，优先级高）**；环境变量 `DEEPSEEK_API_KEY` / `LLM_API_KEY` 兜底（不落仓库文件）。供应商注册表 `app/llm/providers.py` 封装各家的 base_url 与可选模型（DeepSeek/Kimi/智谱/通义/OpenAI），前端永不接触接口地址；Key 明文存本机私有库文件，对外接口只出脱敏形式（`sk-a****f456`）；「验证 Key」走 `/models` 端点不耗 token（供应商无此端点时降级 1-token 对话探测）。个人档案同样以 SQLite 为权威源，保存时镜像写回 `config/profiles/user.json` 保持 CLI/日报兼容（首访自动迁移既有 JSON）。
