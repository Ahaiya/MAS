# MAS Frontend

这是把 `docs/mas_review_workbench.html` 拆成正式前端结构后的桌面版代码入口。

## 目录

- `index.html`: 静态入口页面
- `styles/app.css`: 全局样式与视觉系统
- `src/main.js`: 启动与加载态
- `src/app.js`: 审核台渲染与交互
- `src/state.js`: 前端状态初始化
- `src/data/loadReviewData.js`: 自动发现 `artifacts/{task}/{sample_name}/{dim}` 并做结构映射
- `src/data/parseSampleMarkdown.js`: `sample.md` 原文读取为单一 Markdown 文档条目

## 运行

需要从仓库根目录启动 MAS 审核台服务器。不要使用 `python3 -m http.server`，它只能提供静态文件，不支持 `POST /api/corrections`，点击 `Release` 时会返回 501。

```bash
cd /Users/ahai/Code/MAS
python scripts/server.py
```

然后访问：

```text
http://127.0.0.1:8000/frontend/index.html
```

## 当前实现

- 自动扫描 `artifacts/{task}/{sample_name}/{dim}`，只展示真实存在的维度
- 自动关联 `data/training/{task}/{sample_name}.md`
- 对已发现维度统一展示证据链、评分过程和裁决信息
- 支持证据联动、人工补充证据、编辑反馈与 `Release`
- 当前样式仅针对桌面网页，不再提供移动端布局

## 下一步建议

- 把 `app.js` 继续拆成更细的视图模块
- 接入更多真实学生样本
- 决定是否迁移到 `React` 或 `Vue`
