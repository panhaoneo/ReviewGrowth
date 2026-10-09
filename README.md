# ReviewGrowth · 超级成长股复盘系统

以**当时视角（as-of）的当期财报与研报做定性分析**为核心的 A 股复盘系统：日K线 × 财报 × 研报 × 事件 × 估值的融合展示，由 Agent 自动生成数据与结论，部署于 GitHub Pages。

**在线站点**：https://panhaoneo.github.io/ReviewGrowth/

## 已覆盖标的

| 股票 | 区间 | 报告 |
|---|---|---|
| 宁德时代 300750 | 2019-01-01 ~ 2022-12-31 | [复盘页](https://panhaoneo.github.io/ReviewGrowth/review.html?code=300750) · [Markdown 报告](https://panhaoneo.github.io/ReviewGrowth/data/300750/report.html) · [置信度报告](https://panhaoneo.github.io/ReviewGrowth/data/300750/confidence.html) |

## 核心特性

- **双视角**：当时视角（光标日只显示 `available_at <= 光标` 的信息，K线同步截断）⇄ 事后视角（价格反馈 T+1/5/20/60 + 相对基准超额）。
- **无未来函数**，机械保证：`as_of` 与 `hindsight`/`price_reaction` 物理分离；构建时执行键名黑名单 + 可见集单调性抽样测试。
- **全程可追溯**：每条财报/研报/事件/公告均附原始出处链接（巨潮公告 / 东财研报 PDF / 电话会记录表）。
- **机械计算**：阶段涨跌幅与回撤、事件价格反馈与超额收益、PE 历史分位（as-of 扩展窗口）、PEG——全部由脚本计算，避免人工数字错误。
- **研报精读**：每期财报窗口内关键研报 PDF（前 3 页）抽取核心逻辑/风险提示/盈利预测，构成一致预期曲线。

## 系统架构

```
数据抓取（scripts/fetch_*.py）        分析层（analysis/{code}/*，Agent 撰写）      构建（build_site.py）
fuyao(同花顺) ─ 三表/指标     ┐        9问财报模板 × 每期财报                      校验（含防未来函数测试）
baostock ─ 前复权K线/估值/指数 ├───→   相位划分 / timeline(as_of+hindsight)  ───→  合并 → docs/data/{code}/site.json
巨潮 ─ 公告/披露日(权威)      │        逻辑验证矩阵 / 复盘结论                        渲染 → report.html / confidence.html
东财 ─ 研报元数据+PDF/新闻    ┘        人读复盘报告 report.md                         生成 → docs/（GitHub Pages）
```

## 快速开始

```bash
pip3 install -r requirements.txt
echo "<你的 fuyao API key>" > data/THS_API_KEY   # 同花顺 fuyao 代理，免费申请见 https://fuyao.aicubes.cn/admin

# 1) 抓取数据（以 300750 为例，约 20 分钟含 126 篇研报 PDF）
python3 scripts/fetch_all.py --code 300750

# 2) 由 Agent 撰写 analysis/300750/*（见 AGENTS.md 的 9 步工作流；本仓库已含 300750 完整样例）

# 3) 构建站点（含校验）并本地预览
python3 scripts/build_site.py --code 300750
cd docs && python3 -m http.server 8765
```

新股票：在 `config/stocks/` 增加配置，然后对 Agent 说「复盘 {code}」即按 `.qoder/skills/review-growth` 工作流执行。

## 目录说明

| 目录 | 说明 |
|---|---|
| `tools/` | 数据客户端（fuyao / baostock / 东财 / 巨潮），纯 requests |
| `scripts/` | 抓取（fetch_*）与构建（build_site，含全部校验与机械计算） |
| `config/stocks/` | 每股配置（区间/基准/窗口参数） |
| `data/{code}/` | 原始数据（K线/三表/公告/研报元数据 + reports_txt 研报文本；PDF 不入库） |
| `analysis/{code}/` | **Agent 分析产物**（9 问财报分析/相位/时间轴双视角/逻辑矩阵/复盘结论/报告） |
| `site/` | 前端源码（原生 JS + 自托管 ECharts，无构建步骤） |
| `docs/` | GitHub Pages 产物（由 build_site 生成，勿手改） |

## 数据来源与置信度

巨潮资讯网（公告/披露日，权威源）· 同花顺 fuyao（三表/指标）· baostock（行情/估值）· 东方财富（研报/新闻）。置信度分级与全部缺口见站点内「数据源与缺口清单」与各标的 confidence 报告。

## 免责声明

本项目仅供学习研究使用，不构成任何投资建议。数据来自公开渠道，请以巨潮资讯网原始公告为准。
