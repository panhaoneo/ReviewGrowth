# ReviewGrowth · 项目记忆（Agent 工作流）

给在本项目中工作的 Agent 的持久约定。目标：为任意 A 股生成「以当时视角（as-of）的当期财报与研报做超级成长股定性分析」的复盘站点，部署于 GitHub Pages。

## 会话开始时先读

- `docs/` 是**产物**（Pages 根目录），由 `scripts/build_site.py` 生成，勿手改；前端源码在 `site/`。
- 单只股票的全流程：`config/stocks/{code}.json` → 抓取 → 分析 → 构建 → 部署。

## 运行一只新股票（标准 9 步）

1. 新建 `config/stocks/{code}.json`（字段参照 300750：thscode/start_date/end_date/warmup_start/extend_after_days/benchmarks/earnings_window/pdf_per_window/refresh_days）。
2. `python3 scripts/fetch_all.py --code {code}`（顺序：kline → events → earnings → valuation → reports；`--force` 全量刷新，`--only fetch_x` 单步）。耗时：300750 约 20 分钟（含 126 篇研报 PDF）。
3. 检查各输出 JSON 的 `gaps`/`confidence` 字段；缺失项记录待写入 `analysis/{code}/gaps.json`。
4. 读 `data/{code}/reports_txt/*.txt`（关键研报 PDF 前 3 页文本），为每篇写核心逻辑/风险（逐字引用）→ 合并为 `analysis/{code}/research_notes.json`（格式：{infoCode: {core_logic, risks, quotes, ...}}）。同时从「盈利预测」段落抽取 EPS → `analysis/{code}/consensus.json`（points: [{d, fy, eps, org, infoCode}]）。
5. 按 **9 问财报模板**（见 `.qoder/skills/review-growth/SKILL.md`）为每个财报期写 `analysis/{code}/earnings_analysis.json`（key=period）。
6. 写 `analysis/{code}/phases.json`（潜伏/确认/主升/高位震荡/证伪，`evidence_refs` 挂 timeline id）、`timeline.json`（每条含基础字段 + `as_of{summary,logic_state,evidence}` + `hindsight{summary,verification}`；**as_of 内禁止出现未来信息与价格反馈**；高重要性条目可加 `kline_label`（K线图上的短标注文本，≤12 字，约 15 条以内避免拥挤））、`logic_validation.json`（假设 × 状态变迁）、`summary.json`、`review.json`、`gaps.json`。
7. 写 `report.md`（人读复盘）与 `confidence.md`（数据缺失与置信度）；数字必须与 `docs/data/{code}/site.json` 中机械计算值一致（先跑一次 build_site 取真实值再引用）。
8. `python3 scripts/build_site.py --code {code}`——校验不过必须修复，禁止绕过。
9. 本地验收（`cd docs && python3 -m http.server 8765`）→ commit & push（Pages workflow 自动部署）。

## 数据源与已知坑（实测结论，勿重蹈覆辙）

| 数据 | 源 | 坑 |
|---|---|---|
| 财报三表/指标 | 同花顺 fuyao `X-api-key` | **`report_date_ms`（披露日）系统性偏移约 1 年，已弃用**；披露日一律用巨潮公告（`fetch_earnings.py` 已实现） |
| 个股/指数 K 线 | **baostock 前复权**（adjustflag=2） | fuyao `adjust=forward` 复权因子在 2019-2021 呈漂移误差（应为常数），已弃用；baostock 指数覆盖：399006/000300/399808 可用（980032 不可用） |
| 估值历史 | baostock peTTM/pbMRQ/psTTM | 前复权口径变化点：2023-04-26 宁德时代 10 转 8（此前价格约 0.51 倍） |
| 研报 | 东财 reportapi | `predictThisYearEps` 等字段已停更（全空）→ 盈利预测改从 PDF 抽取；PDF 批量下载需限速（`download_pdf` 内置退避）；`infoCode` 拼详情页 `data.eastmoney.com/report/info/{infoCode}.html` |
| 公告 | 巨潮 hisAnnouncement/query | **pageSize 必须 ≤30**（>30 时 pageNum 失效返回重复页）；orgId 走动态映射表 |
| 新闻 | 东财搜索 | 仅近期，历史缺失（写入 gaps，不虚构 N 类事件） |

## 硬性规则（违反即返工）

1. **严禁未来函数**：as-of 视图只渲染 `available_at <= cursor`；`as_of` 对象内不得出现 T+N/超额/事后结论；build_site 有机械校验（黑名单键名 + 可见集单调性抽样测试）。
2. **每条数据可追溯**：timeline 每条必有 source_url；E 类挂巨潮公告、R 类挂东财研报、A/C 类挂巨潮。
3. **不编造**：数据缺就写进 gaps，置信度降级；所有引用（研报核心逻辑/电话会 Q&A）必须来自 PDF 抽取文本。
4. **依赖最小**：仅 requests / pypdf / baostock；禁止 pandas/numpy。
5. 密钥：`data/THS_API_KEY`（不入库）；HTTPS 页面资源的 CDN 一律自托管（`site/vendor/`）。

## 常用命令

```bash
python3 scripts/fetch_all.py --code 300750 --only fetch_earnings --force   # 单步重抓
python3 scripts/build_site.py --code 300750                                # 校验+构建 → docs/
cd docs && python3 -m http.server 8765                                     # 本地预览
```
