# 02 — 确定性单元切分（segment）

**What to build:** 一个 .md/.txt 文件经 `read_text_file()` → `DataPackage`，其中文本被内容类型感知地切成带**全局连续编号**的单元。零 LLM、确定性、可复现。这是数据流最上游，独立可验证。替代旧的 `chunker.py` + `quote_matcher.py`（旧文件本票不删，留到 09）。

**Blocked by:** 01（需要 DataPackage / Unit 契约）

**Status:** done

- [x] `read_text_file()` 从 .md/.txt 直接构造 `DataPackage`（一期不建 ingest 实现）
- [x] 新增 `src/segment.py`：按内容类型切分——散文按句（。？！；+换行）、代码块（``` 围栏）整块、表格（`|`）每行、标题（`#`）成单元并携带层级、图片（`![alt](src)`）用其 caption/描述文本
- [x] 编号全局连续；多文件共享同一编号空间；每个单元带 `source_file`
- [x] 对话轮次单元携带 `speaker`（复用 `utils/dialogue_sources.py`）
- [x] 短文档（未超"上下文安全余量"，语义替代旧的 4000 阈值、取如 48000）不切分、整篇进入下游
- [x] 超上下文安全余量时才按预算丢弃单元，且丢弃的单元被显式记录（不静默丢弃）
- [x] 每个单元的 `char_range` 能确定性映射回原文（供前端高亮）
- [x] 纯函数测试：各 kind 切分正确、编号全局连续、跨文件共享编号、短文档不切分、超预算丢弃被记录、编号→字符偏移映射正确

## Comments

实现落在 `src/segment.py`（公开 API：`segment()` / `read_text_file()`）+
`tests/unit/test_segment.py`（20 个纯函数测试，segment.py 覆盖率 99%，唯一
未覆盖分支是 `_trim()` 里一个当前调用路径下不可达的防御性空白判断）。

经过一轮 `/code-review`（Standards + Spec 双轴并行子代理）后修复：
- 短文档路径中，空文件会白白消耗一个编号导致编号出现空洞——改为先过滤空文件再编号。
- 预算丢弃原先是贪心 first-fit（可能在超预算单元之后仍保留更小的后续单元），
  与文档字符串宣称的"按顺序尾部截断"不符——改为真正的尾部整体截断。
- 测试原先直接调用私有的 `_segment_file`，违反"只断言外部可观察行为"的测试
  规范——改为全部经公开的 `segment()` 驱动（用一个远超预算的填充块把
  `context_budget` 检查逼进细粒度切分路径）。
- `_estimate_tokens` 原先照搬旧 chunker.py 的中英文分别估算（含未命名的
  魔法数 0.2/1.5/1.3），规格并未要求区分语种——简化为单一字符数公式。
