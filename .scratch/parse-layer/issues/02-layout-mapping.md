# 02 — layouts → DataPackage 的纯函数映射

**What to build:** `src/parse/package.py`。给定 N 个源文件各自的 layouts，产出一个 `DataPackage`。零网络、零 LLM、确定性。

**Blocked by:** 01

**Status:** done

- [x] 白名单常量**写死在代码里**：`title / text / table / table_name / table_note / figure / picture / formula / contents`
- [x] 白名单外的 layout 全部剔除，**按 type 计数**记入 `provenance.excluded_layouts`（挡噪音但不静默丢弃）
- [x] 每个进白名单的 layout → 一个 Unit：`markdown` 取 `markdownContent`（**不是** `text`），`type` 取原值，`page` 取 `pageNum`
- [x] 编号全局连续，多个源文件共享同一编号空间，每个 Unit 带 `source_file`
- [x] layouts 按 `index`（阅读顺序）排序后再编号
- [x] `provenance` 完整：`parsed_at` / `source_files[]` / `options{...}` / `excluded_layouts{}`
- [x] **不做任何预算裁剪**——产出完整包（见 spec 的 Out of Scope）
- [x] 测试：白名单过滤、剔除计数、编号连续、跨文件共享编号、取 `markdownContent` 而非 `text`、`provenance` 内容、空 layouts 不产生编号空洞

## Comments

这是本层唯一的纯函数模块，测试不需要任何 mock。
