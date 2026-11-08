# AI 猎头智能助手

## 项目简介

AI 猎头智能助手面向技术岗位的早期筛选环节：将分散的简历汇入统一候选池，抽取技能、年限与学历标签，按可调权重为岗位排序打分并生成推荐依据。系统以「算法负责筛选与标记、人负责判断与决策」为设计前提，适合招聘量较大、需要留存决策痕迹的中小团队招聘负责人使用。

---

## 目录

| 章节 | 内容 |
| --- | --- |
| [项目简介](#项目简介) | 项目定位、解决的问题与适用对象 |
| [核心特性](#核心特性) | 已实现的能力及其在流程中的位置 |
| [快速开始](#快速开始) | 环境要求、安装步骤、首次使用流程 |
| [项目结构](#项目结构) | 目录职责划分与模块调用关系 |
| [配置说明](#配置说明) | 环境变量清单与关键运行参数 |
| [常见问题](#常见问题) | 部署与使用中的高频问题 |
| [测试](#测试) | 单元测试、接口测试与浏览器验收 |
| [适用范围与限制](#适用范围与限制) | 已知能力边界 |
| [许可证](#许可证) | 授权方式 |
| [附录：接口一览](#附录接口一览) | 全部 HTTP 接口分类索引 |

---

## 核心特性

本节说明系统当前已实现的能力，以及各项能力在流程中的位置。

**四步作业链路。** 收集（简历入库与标签抽取）→ 匹配（技能与经验加权计分）→ 陈述（生成可解释的推荐理由）→ 送达（理由与邀约邮件一并成稿、确认后发出）。每一步的产物都落库留痕，可回溯。

**可解释的匹配计分。** 总分由技能分与经验分加权构成，默认 85% / 15%。内置均衡型、重潜力·轻经验、资深优先三档预设，权重与阈值调整后即时重算全库。

**硬性门槛独立于权重。** 岗位设定最低年限后，经验不足者无论技能分多高均直接标记「不通过」，权重无法越过该约束。

**三种来源的推荐理由。** 支持模型撰写、规则模板兜底与 HR 手写，界面明确标注来源；人工改写的理由在后续全库重算中不被覆盖。

**发送前的四道校验。** 决策状态、职位在招状态、收件域名合法性、当日发送限额，四项全部通过方可发送，失败记录保留。

**对话式操作入口。** 支持自然语言查询与指令，写入类语句须通过 SQL 白名单校验；越权与注入类输入会被拦截。

**公司背调与合作价值分析。** 依据百科与可选的企业信息数据源生成分章节报告，历次结论留痕。

**账号与审计。** PBKDF2-SHA256 加盐存储密码、内存令牌鉴权，采集与查询操作分别写入 `crawl_logs` 与 `query_logs`。

---

## 快速开始

本节给出从零启动系统的完整步骤。全部命令均在项目根目录 `ai-headhunter/` 下执行。

### 环境要求

| 项目 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11 及以上 | 已在 3.13 环境验证 |
| MySQL | 8.0 及以上 | 字符集须为 utf8mb4 |
| 操作系统 | Windows / Linux / macOS | 无平台特定依赖 |
| 网络 | 可访问 SMTP 服务 | 仅推送邮件时需要 |

### 安装

1. 安装依赖：

```bash
pip install -r requirements.txt
```

主要依赖为 `fastapi>=0.110`、`uvicorn>=0.29`、`langgraph>=0.2`、`mysql-connector-python>=8.0` 与 `python-dotenv>=1.0`。

2. 初始化数据库结构与版本迁移：

```bash
mysql -u root -p < db/schema.sql
python tools/apply_migration.py db/migrations/V2__p0_fixes.sql
python tools/apply_migration.py db/migrations/V3__add_data_source.sql
python tools/apply_migration.py db/migrations/V4__add_gender.sql
python tools/apply_migration.py db/migrations/V5__add_company_analysis.sql
python tools/apply_migration.py db/migrations/V6__add_risk_checks.sql
python tools/apply_migration.py db/migrations/V7__match_weights_and_decision.sql
python tools/apply_migration.py db/migrations/V8__resume_rich_sections.sql
python tools/apply_migration.py db/migrations/V9__app_users.sql
python tools/apply_migration.py db/migrations/V10__push_reason.sql
```

> 迁移脚本一次只处理一个文件，须按 V2 至 V10 的顺序逐个执行。也可使用 Navicat 等客户端直接运行上述 SQL。

3. 配置环境变量：

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

编辑 `.env`，至少填写 `DB_PASSWORD` 与 `SMTP_PASSWORD` 两项。完整字段见「配置说明」。

4. 导入公开简历语料：

```bash
python crawler/resume_corpus_import.py
```

5. 启动服务：

```bash
python web/server.py
```

服务启动后访问 <http://127.0.0.1:7862>，交互式接口文档位于 <http://127.0.0.1:7862/api/docs>。

系统功能的完整操作演示见 [docs/demo.mp4](docs/demo.mp4)（约 2 分钟，涵盖登录、人才库、公司分析、匹配策略等主要视图）。

### 使用

首次启动会自动创建演示账号，用于快速验证系统功能：

| 用户名 | 密码 |
| --- | --- |
| `demo` | `demo123` |

登录后为「演示 HR」身份，权限为管理员，七个视图全部可用。基本操作顺序如下：

1. 在「在招职位」中录入岗位，填写技能要求与最低年限；
2. 在「匹配策略」中选择预设或自定义权重，点击重算全库；
3. 在「工作台」查看推荐结果与推选理由，逐条标记「邀约」或「不通过」；
4. 对标记为「邀约」的记录执行推送，确认邮件内容后发送。

若决策台为空，可运行种子脚本生成演示记录：

```bash
python tools/seed_records.py        # 默认 6 条，可追加数字指定条数
```

> 演示密码为公开值，定义于 `web/auth.py` 的 `DEMO_PASSWORD` 常量。部署到公网环境前，须通过环境变量 `DEMO_PASSWORD` 覆盖，或在「系统设置 → 修改密码」中更改。

---

## 项目结构

本节说明各目录的职责划分，便于定位代码与扩展功能。

```text
ai-headhunter/
├── config.py                    配置读取入口，集中从环境变量加载
├── requirements.txt             Python 依赖清单
├── core/                        核心业务能力，与界面解耦
│   ├── business.py              业务函数层：列表、统计、职位、导出、背调、重置
│   ├── matcher.py               匹配算法：计分、档位、硬性门槛、理由生成
│   ├── push.py                  推送编排：理由 → 邮件 → 确认 → 发送 → 落库
│   ├── mailer.py                SMTP 发送、退避重试、正文清洗
│   ├── llm.py                   大模型客户端，未配置密钥时自动降级
│   ├── nl2sql.py                自然语言转 SQL，附白名单校验
│   ├── company_lookup.py        公司背调，百科优先、模型兜底
│   ├── company_analyst.py       合作价值分析，结论可回溯
│   └── risk_monitor.py          风险信号核查
├── graph/                       LangGraph 对话编排
│   ├── builder.py               意图路由至各业务节点
│   └── nodes/                   意图识别、查询执行、匹配、分析、邮件等节点
├── crawler/                     数据采集
│   ├── baike_spider.py          百度百科抓取
│   ├── qcc_client.py            企查查开放平台客户端（可选）
│   ├── resume_corpus_import.py  公开简历语料导入
│   └── dataset_import.py        示例数据集导入
├── db/
│   ├── schema.sql               建表脚本，含 V1 全量结构
│   ├── pool.py                  数据库连接池
│   └── migrations/              版本化迁移 V2 至 V10
├── web/
│   ├── server.py                34 个 API 路由、2 个页面入口与静态资源托管
│   ├── auth.py                  账号体系与会话令牌
│   ├── preview.py               文档预览辅助
│   └── static/                  前端页面、样式、脚本与字体
├── tests/                       pytest 单元测试，55 个用例
└── tools/                       集成测试、安全测试、界面验收与运维脚本
```

各模块的调用关系为：`web` 层接收请求并调用 `core` 中的业务函数，`core` 依赖 `db` 完成持久化，`graph` 编排对话流程，`crawler` 独立运行、向 `db` 写入采集结果。

---

## 配置说明

本节列出系统可配置项。所有配置均从项目根目录的 `.env` 文件读取，由 `config.py` 统一加载。

### 配置文件

| 文件 | 用途 | 是否纳入版本控制 |
| --- | --- | --- |
| `.env.example` | 模板，仅含键名与占位值 | 是 |
| `.env` | 实际配置，含密钥 | 否（已列入 `.gitignore`） |

### 配置项

| 键名 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_HOST` | `127.0.0.1` | MySQL 主机地址 |
| `DB_PORT` | `3306` | MySQL 端口 |
| `DB_USER` | `root` | 数据库用户名 |
| `DB_PASSWORD` | 空 | 数据库密码，必填 |
| `DB_NAME` | `headhunter` | 数据库名 |
| `LLM_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | 模型服务地址，兼容 OpenAI 协议 |
| `LLM_API_KEY` | 空 | 模型密钥，留空则降级为规则模式 |
| `LLM_MODEL` | `glm-5.3-flash` | 主模型名称 |
| `LLM_MODEL_FALLBACK` | 空 | 备用模型，主模型不可用时启用 |
| `SMTP_HOST` | 空 | SMTP 服务器地址 |
| `SMTP_PORT` | `465` | SMTP 端口，使用 SSL |
| `SMTP_USER` | 空 | 发件邮箱账号 |
| `SMTP_PASSWORD` | 空 | 邮箱授权码，非登录密码 |
| `SMTP_FROM_NAME` | `AI猎头助手` | 发件人显示名称 |
| `DAILY_SEND_LIMIT` | `100` | 单日发送上限 |
| `QCC_API_KEY` | 空 | 企查查开放平台密钥，可选 |
| `QCC_API_SECRET` | 空 | 企查查开放平台密钥，可选 |
| `DEMO_PASSWORD` | `demo123` | 演示账号密码，可覆盖 |

> `LLM_API_KEY` 留空或填写占位文字时，系统不会报错，而是自动切换至规则模板模式：推荐理由由模板生成，邮件文本程式化，业务流程不受影响。

### 关键运行参数

以下参数位于源码中，修改后需重启服务：

| 参数 | 位置 | 当前值 |
| --- | --- | --- |
| 监听地址与端口 | `web/server.py` 第 820 行 | `127.0.0.1:7862` |
| 令牌有效期 | `web/auth.py` 第 17 行 `TOKEN_TTL` | 7 天 |
| 密码哈希轮数 | `web/auth.py` 第 16 行 `PBKDF2_ROUNDS` | 120000 |
| 默认匹配权重与阈值 | `core/matcher.py`，运行期由 `match_settings` 表持久化，界面可调 | 技能 85% / 经验 15%，阈值 75 分 |

---

## 常见问题

本节收录部署与使用过程中出现频率较高的问题。

**服务启动后浏览器显示旧界面。** 两个页面入口的响应已带 `Cache-Control: no-cache`，静态资源以查询串标注版本号。若仍为旧样式，请硬刷新（`Ctrl+F5`）或使用无痕窗口；修改前端文件后，须同步递增 `web/static/index.html` 中的版本号（当前 `styles.css?v=22`、`app.js?v=19`）与 `login.html` 中的（当前 `auth.css?v=17`、`auth.js?v=9`）。

**推送测试卡在「生成中」。** 单次模型调用同时生成理由与邮件，实测耗时 20 至 45 秒。`tools/push_ui_test.py` 的等待超时为 120 秒，属正常范围，非功能故障。

**测试脚本报 `upstream connect failed`。** 本机配置了 `HTTP_PROXY` 时会拦截回环请求。脚本内部已通过 `ProxyHandler({})` 直连；手工使用 curl 时须加 `--noproxy "*"`。

**没有模型密钥能否使用。** 可以。系统会自动降级至规则模板模式，推荐理由与邮件文本均由模板生成，其余功能不受影响。

**推送被拦截。** 依次核对四道校验：推荐是否已标记「邀约」、职位是否仍在招聘、收件域名是否为 `example.com` 等保留域名、当日发送是否已达 `DAILY_SEND_LIMIT`。

**决策台的旧决策被清空。** 权重调整导致分数变化时，原有 HR 决策会自动作废并退回「待审阅」，以避免依据过期分数作出判断。

**`security_test.py` 中断后公司库出现异常条目。** 该脚本会写入含注入载荷的测试公司，正常结束时自行清理；中断残留可按名称删除，不影响业务数据。

**服务重启后需要重新登录。** 会话令牌存放于进程内存，重启即失效。当前为单进程设计，多进程部署须先将会话迁移至共享存储。

---

## 测试

本节列出可执行的验证方式，用于确认部署完整性与功能可用性。

```bash
pytest tests/ -v                     # 单元测试，55 个用例
python tools/push_reason_test.py     # 推送与理由全链路，31 项
python tools/api_full_test.py        # 接口功能与边界，46 项
python tools/security_test.py        # SQL 注入、XSS、越权，19 项
python tools/ui_smoke_test.py        # 业务函数层边界，40 项
python tools/e2e_check.py            # 端到端自检，加 --fresh 可清库重来
```

浏览器验收需安装 Playwright：

```bash
python tools/push_ui_test.py         # 推送弹窗，21 项
python tools/ui_full_test.py         # 七个视图与关键交互
python tools/check_login_page.py     # 登录页
python tools/xss_verify.py           # 验证注入脚本不执行
```

所有测试脚本使用独立账号，结束后清理自身数据并复原权重。

---

## 适用范围与限制

本节说明系统已知的能力边界，供评估适用性时参考。

- 匹配依据为简历标签而非完整履历，标签抽取偏差会直接反映在分数上；
- 简历解析基于公开语料与规则，冷僻写法可能抽错标签，导入后建议人工复核；
- 公司背调以百科与企业信息数据源为准，未收录公司由模型兜底，结论不构成尽职调查依据；
- 邮件为单向发送，候选人回信不会回流至系统；
- 会话存放于内存，仅支持单进程部署。

---

## 许可证

〔待补充〕

---

## 附录：接口一览

除登录与注册外，全部接口须在请求头携带 `X-Auth-Token`。

| 分类 | 接口 |
| --- | --- |
| 账号 | `POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`、`POST /api/auth/profile`、`POST /api/auth/password`、`POST /api/auth/logout-others`、`GET /api/auth/security`、`GET /api/auth/stats` |
| 概览 | `GET /api/overview`、`GET /api/selfcheck` |
| 候选人 | `GET /api/candidates`、`GET /api/candidate/{id}`、`GET /api/export.csv` |
| 职位 | `GET /api/positions`、`POST /api/positions`、`POST /api/positions/{id}/close` |
| 公司 | `GET /api/companies`、`GET /api/companies/{id}`、`POST /api/companies/lookup`、`GET /api/analyses`、`POST /api/analysis` |
| 匹配 | `GET /api/match-weights`、`POST /api/match-weights`、`POST /api/match/recalc` |
| 决策与推送 | `GET /api/records`、`POST /api/records/{id}/decision`、`GET`/`POST`/`PUT /api/records/{id}/reason`、`GET /api/records/{id}/email`、`POST /api/records/{id}/push` |
| 对话 | `POST /api/chat`、`POST /api/reset` |
