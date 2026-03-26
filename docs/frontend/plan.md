# 前端开发计划

> 本文档基于 `docs/frontend/design.md`、`docs/frontend/mas_system_overview.md` 及后端代码库实际结构生成。
> 仅描述意图与规划，不修改任何现有文件。

---

## 1. 页面清单与职责

### 单样本诊断报告页（主页 `/`）

**定位**：系统核心展示页，面向非技术背景的教育学者或项目管理者。
**职责**：

- 顶部工具栏：Essay 选择器（下拉，列出所有已评估的 essay_id）
- 左侧面板：展示原始学生作文（Read 模块），支持文本高亮联动
- 右侧面板：三段式展示 MAS 认知工作流
  - Think 模块：流水线执行时间轴 + 双评审卡片
  - Write 模块：六维度评分手风琴，含偏差对比与 rationale 展示
  - Publish 模块：MAS vs 人类均分的重叠雷达图

---

## 2. 后端接口需求

### 2.1 现状说明

**当前后端无 HTTP API**。数据以静态文件形式存储：

| 数据 | 路径 |
|------|------|
| 评分结果 | `artifacts/eval/{essay_id}/feedback.json` |
| 运行追踪 | `artifacts/eval/{essay_id}/run_trace.json` |
| 双评审假设 | `artifacts/eval/{essay_id}/hypotheses.json` |
| 原始作文 & 人工评分 | `data/training_set_8.tsv`（`essay` 列 + `raterN_traitN` 列） |

### 2.2 方案：新建最小 FastAPI 服务

新建 `api/server.py`，提供以下 6 个只读接口。前端统一请求 `http://localhost:8000`，Vite 通过代理转发。

---

#### GET `/api/essays`

**职责**：返回所有已完成评估的 essay_id 列表。

**实现逻辑**：扫描 `artifacts/eval/` 目录，收集同时存在 `feedback.json` 和 `run_trace.json` 的子目录名。

**响应**：
```json
{
  "essay_ids": ["20716", "20717", "20718", "..."]
}
```

---

#### GET `/api/essays/{essay_id}/text`

**职责**：返回指定 essay 的原始作文文本。

**实现逻辑**：从 `data/training_set_8.tsv` 的 `essay` 列中按 `essay_id` 匹配提取（无需依赖 txt 文件）。

**响应**：
```json
{
  "essay_id": "20716",
  "text": "One day I was thinking about my mom..."
}
```

---

#### GET `/api/essays/{essay_id}/feedback`

**职责**：返回 `feedback.json` 全量内容。

**响应**：
```json
{
  "dimensions": {
    "ideas_content": {
      "dimension_name": "Ideas and Content",
      "canonical_score": 4,
      "display_score": "4",
      "display_annotation": null,
      "descriptor_refs": ["clear main ideas", "..."],
      "evidence_span_ids": ["span-real-xxx", "..."],
      "feedback_text": "Your essay received a score of 4...",
      "confidence": 0.85
    }
  },
  "summary": "Real provider feedback for 6 dimension(s).",
  "provider": "openai_compatible"
}
```

---

#### GET `/api/essays/{essay_id}/trace`

**职责**：返回 `run_trace.json` 全量内容。

**响应**（关键字段）：
```json
{
  "run_id": "run-cb058861ea90",
  "bundle_version": "2026-03-12",
  "bundle_id": "asap_set8_baseline",
  "status": "completed",
  "started_at": "2026-03-16T12:10:31.028584+00:00",
  "finished_at": "2026-03-16T12:18:09.850256+00:00",
  "terminal_validation_passed": true,
  "replay_metadata": { "provider": "openai_compatible" },
  "node_traces": [
    {
      "node_id": "node_extractor",
      "node_type": "extract",
      "status": "success",
      "started_at": "...",
      "finished_at": "...",
      "output_ref": "spans:52",
      "fallback_history": [],
      "error_message": null
    }
  ]
}
```

---

#### GET `/api/essays/{essay_id}/hypotheses`

**职责**：返回 `hypotheses.json` 全量内容（双评审原始假设）。

**响应**：
```json
{
  "run_id": "run-xxx",
  "hypotheses": [
    {
      "hypothesis_id": "hyp-real-xxx",
      "dimension_id": "ideas_content",
      "rater_id": "rater_1",
      "score": { "canonical_score": 4, "display_score": "4" },
      "descriptor_refs": ["..."],
      "rationale": "The essay has an easily identifiable purpose...",
      "confidence": 0.85
    }
  ]
}
```

---

#### GET `/api/essays/{essay_id}/human-scores`

**职责**：从 `data/training_set_8.tsv` 中提取指定 essay 的人类评分，并按维度对齐返回。

**实现逻辑**：
1. 以 `essay_id`（数字）匹配 TSV 第 1 列 `essay_id`
2. 按 trait 编号映射到 dimension_id
3. 返回 rater1、rater2 每维度的原始分及均值

**Trait 到 dimension_id 映射**（固定于 API 服务层）：
```
trait1 → ideas_content
trait2 → organization
trait3 → voice
trait4 → word_choice
trait5 → sentence_fluency
trait6 → conventions
```

**响应**：
```json
{
  "essay_id": "20716",
  "dimensions": {
    "ideas_content":    { "rater1": 4, "rater2": 3, "mean": 3.5 },
    "organization":     { "rater1": 4, "rater2": 4, "mean": 4.0 },
    "voice":            { "rater1": 4, "rater2": 4, "mean": 4.0 },
    "word_choice":      { "rater1": 4, "rater2": 4, "mean": 4.0 },
    "sentence_fluency": { "rater1": 4, "rater2": 3, "mean": 3.5 },
    "conventions":      { "rater1": 3, "rater2": 3, "mean": 3.0 }
  }
}
```

---

## 3. 组件拆分建议

### 3.1 布局组件

```
AppShell
├── TopBar                    # Essay 选择器下拉
└── ResizablePanels           # react-resizable-panels 左右分栏（默认 4:6）
    ├── LeftPanel             # 左侧（可拖拽调整宽度）
    └── RightPanel            # 右侧三段式，垂直滚动
```

### 3.2 左侧：Read 模块

```
EssayReader
├── EssayHeader               # 显示 essay_id、字数统计
└── EssayText                 # 作文正文，prose 样式
    └── HighlightableText     # 接收 highlightPhrase: string | null，用 <mark> 标记匹配片段
```

**高亮机制**：全局状态（React Context）存储 `activePhrase: string | null`。
`HighlightableText` 订阅此状态，通过字符串匹配将文本分段，对命中段落包裹 `<mark>` 并添加 CSS 过渡动画。

### 3.3 右侧：Think 模块（流水线时间轴 + 双评审）

```
PipelineTimeline
└── PipelineStep[]            # 每个 node_trace 对应一行
    ├── StepIcon              # success=绿色对勾 / failed=红色叉 / skipped=灰色
    ├── StepLabel             # node_type 中文化
    ├── StepDuration          # finished_at - started_at（秒）
    └── StepOutputRef         # output_ref 字符串（如 "spans:52"）

DualRaterCard                 # 嵌套在 Think 模块，展示双评审结果
├── RaterColumn × 2           # Rater 1 / Rater 2 并排
│   ├── DimensionScoreRow[]   # 每维度：分数 + 一致性状态图标（绿/红）
│   └── RationaleAccordion    # 可折叠展示完整 rationale 文本（不截断）
└── ConflictSummary           # 整体冲突数量徽章
```

**node_type 中文映射**：

| node_type | 显示文字 |
|-----------|---------|
| preprocess | 通读与维度规划 |
| coverage | 量规覆盖确认 |
| extract | 全维度证据抽取 |
| observe | 证据整理归类 |
| score | 双盲专家独立评审 |
| check_consistency | 一致性验证 |
| adjudicate | 争议裁决 |
| feedback | 反馈文本生成 |

### 3.4 右侧：Write 模块（维度手风琴）

```
DimensionAccordion
└── DimensionItem[]           # 6 个维度，按固定顺序 I/O/V/W/S/C
    ├── AccordionTrigger      # 折叠头：维度名 + MAS分 + 人类均分 + 偏差徽章
    │   └── DeviationBadge    # |偏差| > 1 显示橙色，|偏差| > 1.5 显示红色
    └── AccordionContent      # 展开内容
        ├── DescriptorList    # descriptor_refs 列表（评分依据描述语）
        ├── FeedbackMarkdown  # react-markdown 渲染 feedback_text
        │   └── BoldSpan[]    # ** 加粗文本绑定 onMouseEnter/Leave，触发左侧高亮
        ├── EvidenceCountBadge# 证据数量（evidence_span_ids.length）
        └── ConfidenceBar     # 置信度进度条（confidence × 100%）
```

### 3.5 右侧：Publish 模块（雷达图）

```
PublishModule
├── CompositeScore            # 合计分展示（MAS合计 / 人类合计 / 满分60）
└── DimensionRadarChart       # recharts RadarChart
    ├── MASPolygon            # 半透明蓝色，数据来自 canonical_score
    └── HumanPolygon          # 半透明红色，数据来自人类 trait 均值
```

**6 个轴标签**：Ideas, Organization, Voice, Word Choice, Sentence Fluency, Conventions

### 3.6 共享工具层

```
services/
└── essayService.ts           # 封装全部 API 请求，统一错误处理

context/
└── HighlightContext.tsx      # activePhrase 全局状态 + setActivePhrase

hooks/
└── useEssayReport.ts         # 并行 Promise.all 拉取 text/feedback/trace/hypotheses/human-scores

lib/
├── dataTransforms.ts         # 后端 JSON → 前端 ViewModel
├── compositeScore.ts         # 2×I + 2×O + 2×S + 4×C 公式
└── constants.ts              # 维度顺序、颜色、node_type 中文映射
```

---

## 4. 技术栈选型及理由

| 技术 | 选型 | 理由 |
|------|------|------|
| 构建工具 | **Vite** | design.md 明确要求 |
| 框架 | **React 18 + TypeScript** | design.md 明确要求；类型安全契合后端强类型契约 |
| 样式 | **Tailwind CSS** | design.md 明确要求；`prose` 类天然适配文章阅读器 |
| UI 组件 | **Shadcn UI** | design.md 明确要求（Card, Accordion, Progress, Badge, ScrollArea） |
| 分栏 | **react-resizable-panels** | design.md 明确要求 |
| 图表 | **recharts** | design.md 明确要求；RadarChart 内置支持 |
| 图标 | **lucide-react** | design.md 明确要求；与 Shadcn UI 配套 |
| Markdown 渲染 | **react-markdown** | design.md 明确要求；支持自定义组件给 `strong` 绑定高亮事件 |
| HTTP 客户端 | **原生 fetch** | 接口数量少，无需额外依赖 |
| 后端 API 服务 | **FastAPI + uvicorn** | 项目已用 Python；纯只读，不触发 LLM，不修改 `src/` |
| 包管理 | **npm** | 与 Shadcn UI CLI 兼容 |
| 运行方式 | **本地双进程** | `vite dev`（5173）+ `uvicorn`（8000），Vite 代理转发 `/api` |

---

## 5. 文件目录结构草案

```
MAS/
├── api/                          # 新建：最小 FastAPI 服务
│   └── server.py                 # 6 个只读路由
│
├── frontend/                     # 新建：Vite + React 前端
│   ├── index.html
│   ├── vite.config.ts            # 配置 /api 代理到 localhost:8000
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── src/
│       ├── main.tsx
│       ├── App.tsx               # 单页入口，渲染 ReportPage
│       │
│       ├── pages/
│       │   └── ReportPage.tsx    # 主页：全屏分栏布局
│       │
│       ├── components/
│       │   ├── layout/
│       │   │   ├── TopBar.tsx
│       │   │   └── ResizablePanels.tsx
│       │   ├── read/
│       │   │   ├── EssayReader.tsx
│       │   │   └── HighlightableText.tsx
│       │   ├── think/
│       │   │   ├── PipelineTimeline.tsx
│       │   │   ├── PipelineStep.tsx
│       │   │   └── DualRaterCard.tsx
│       │   ├── write/
│       │   │   ├── DimensionAccordion.tsx
│       │   │   ├── DimensionItem.tsx
│       │   │   ├── FeedbackMarkdown.tsx
│       │   │   └── DeviationBadge.tsx
│       │   └── publish/
│       │       ├── PublishModule.tsx
│       │       └── DimensionRadarChart.tsx
│       │
│       ├── context/
│       │   └── HighlightContext.tsx
│       │
│       ├── hooks/
│       │   └── useEssayReport.ts
│       │
│       ├── services/
│       │   └── essayService.ts
│       │
│       └── lib/
│           ├── dataTransforms.ts
│           ├── compositeScore.ts
│           └── constants.ts
│
├── docs/frontend/
│   ├── design.md
│   ├── mas_system_overview.md
│   ├── plan.md
│   └── 设计草图.png
│
└── ...（现有后端代码不变）
```

---

## 6. 执行顺序及阶段划分

### Phase 1：基建

1. 在 `MAS/` 根目录创建 `frontend/` Vite 项目
   ```bash
   npm create vite@latest frontend -- --template react-ts
   ```
2. 配置 Tailwind CSS、安装 Shadcn UI（`npx shadcn@latest init`）
3. 安装依赖：`react-resizable-panels recharts lucide-react react-markdown`
4. 配置 Vite 代理：`/api` → `http://localhost:8000`

### Phase 2：后端 API 服务

1. 新建 `api/server.py`，实现 6 个 GET 接口
2. 使用 `uvicorn api.server:app --reload` 启动，逐接口验证响应
3. 确认 CORS 配置（允许 `http://localhost:5173`）

### Phase 3：布局与数据层

1. 实现 `AppShell` + `ResizablePanels`（无数据的空壳布局）
2. 实现 `essayService.ts`（6 个接口的 fetch 封装）
3. 实现 `useEssayReport`（并行 `Promise.all` 拉取所有数据）
4. 实现 `HighlightContext`（全局高亮状态）
5. 实现 `lib/dataTransforms.ts`（后端 JSON → 前端 ViewModel）

### Phase 4：左侧 Read 模块

1. 实现 `EssayReader` 文章展示
2. 实现 `HighlightableText`（订阅 Context，文字匹配高亮，CSS 过渡动画）

### Phase 5：右侧三段式组件

**5a. Publish 模块（先做，反馈最直观）**
1. 实现 `DimensionRadarChart`（recharts，MAS + 人类双多边形）
2. 实现 `CompositeScore` 展示

**5b. Write 模块**
1. 实现 `DimensionItem`（折叠头：维度名/分数/偏差徽章）
2. 实现 `FeedbackMarkdown`（react-markdown，自定义 `strong` 组件绑定 hover）
3. 验证 hover → `HighlightContext` → 左侧高亮联动

**5c. Think 模块**
1. 实现 `PipelineTimeline`（node_traces 时间轴）
2. 实现 `DualRaterCard`（双评审卡片：分数对比 + rationale 可折叠展示）

### Phase 6：打磨与验证

1. 加载状态（Skeleton / Spinner）
2. 错误状态（API 失败提示）
3. 跨 essay 切换的状态清理
