# MAS (Multi-Agent System) 自动化评估系统展示页 - 前端开发需求文档

## 1. 项目背景与目标
本项目旨在为非技术背景的专家（如教育学者、项目管理者）展示 MAS 系统在评估“学生解决复杂工程问题能力”时的有效性与严谨性。
界面的核心目标是打造一个**“白盒化展示厅”**，拒绝黑盒打分。需要通过可视化系统的认知工作流，证明 AI 的评估过程与人类专家双盲评审一样严密。

## 2. 技术栈要求
- **构建工具**: Vite
- **框架**: React (TypeScript)
- **样式**: Tailwind CSS
- **UI 组件**: Shadcn UI (仅引入需要的组件，如 Card, Accordion, Progress, Badge, ScrollArea)
- **依赖库**: `react-resizable-panels` (左右分栏), `recharts` (雷达图), `lucide-react` (图标), `react-markdown` (解析富文本)

## 3. 页面布局与核心模块设计
页面采用全屏设计，使用 `react-resizable-panels` 实现经典的左右 Split-pane 布局（默认比例 4:6 或 5:5）。
整体功能模块基于 **Read - Think - Write - Publish** 认知工作流进行拆分：

### 3.1 左侧视图：Read 模块 (原始案卷区)
- **功能**: 纯净展示学生的原始作答文本（Essay Text）。
- **UI**: 类似高级阅读器，使用白底黑字，舒适的行距 (`prose` class)。
- **交互要求 (Highlighting)**: 监听右侧组件的 Hover 事件。当用户在右侧鼠标悬停某段引用的反馈文本时，通过简单的字符串匹配，将左侧对应的文本行用 `<mark>` 标签高亮显示（带 CSS 过渡动画）。

### 3.2 右侧视图上部：Think 模块 (MAS 思考剧场)
- **功能**: 以动态时间轴/步骤条的形式，展示 `run_trace.json` 中的关键推理阶段。
- **UI**: 垂直的 Steps 或 Timeline 样式。
- **核心展示步骤**:
  1. **通读与多维度规划 (Preprocess)**: 瞬间完成，提示构建了 6 个评估维度的量规。
  2. **全维度证据提取 (Extract)**: 提取文本特征。
  3. **双盲专家独立评审 (Double-Blind Scoring)**: 结合 `hypotheses` 数据，展示 Rater 1 和 Rater 2 背靠背打分的卡片（如有分歧则红灯提示，无分歧则亮绿灯通过）。
  4. **一致性裁决 (Consistency Check)**: 触发主裁判逻辑，得出最终维度分数。

### 3.3 右侧视图中部：Write 模块 (分歧判定与证据溯源)
- **功能**: 详细展示 AI 的给分依据，打破黑盒。
- **UI**: 使用 Accordion (手风琴/折叠面板) 列表，按 6 个维度（如 organization, content 等）排列。
- **内容要求**:
  - 折叠状态：显示维度名称、MAS 得分、人类均分、以及 **偏差值 (Deviation)**。偏差大于 1 的用显眼颜色（如橙色/红色）标记。
  - 展开状态：使用 `react-markdown` 渲染 `feedback.json` 中的富文本评语。评语中的 `**加粗文本**` 必须绑定 `onMouseEnter` 和 `onMouseLeave` 事件，用于触发左侧 Read 模块的高亮。

### 3.4 右侧视图底部：Publish 模块 (最终评估报告)
- **功能**: 直观的机人对比数据总结。
- **UI**: 使用 `recharts` 绘制一张**重叠雷达图 (Radar Chart)**。
- **数据映射**: 图表包含两条多边形数据线，一条半透明蓝色代表“MAS 系统得分”，一条半透明红色代表“人类专家均分”。6 个顶点对应 6 个 Canonical Score 维度。

## 4. 开发步骤建议 (请按此顺序执行)
1. **初始化与基建**: 使用 Vite 创建 React+TS 项目，配置 Tailwind CSS，安装基础依赖。
2. **布局搭建**: 实现无数据的全屏左右拖拽布局结构。
3. **数据 Mock 接入**: 我会在项目根目录或 `src/mock` 文件夹下提供真实的 `sample.txt`, `run_trace.json`, `feedback.json`, `hypotheses.json`，请编写简单的 Service 去读取并解析这些静态数据。
4. **组件开发 - 左侧阅读器**: 实现文本展示与暴露被外部触发的高亮方法。
5. **组件开发 - 右侧面板**: 依次开发雷达图 (Publish)、折叠评语面板 (Write)、以及动态流程条 (Think)。
6. **状态联动**: 使用 React Context 或简单的状态提升，把右侧 `**加粗文本**` 的 Hover 状态传递给左侧的文本匹配高亮函数。



