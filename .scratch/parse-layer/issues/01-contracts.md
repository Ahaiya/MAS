# 01 — 契约重写：Unit / DataPackage

**What to build:** `src/contracts/package.py` 按解析层的口径重写。数据流最上游，无外部依赖，可独立 TDD。

**Blocked by:** —

**Status:** done

- [x] `Unit = {id: int, markdown: str, type: str, source_file: str, page: int}`
- [x] 删除 `kind` / `text` / `char_range` / `speaker`（零消费方；`char_range` 在 layout 世界里没有真实来源）
- [x] `type` 不做校验白名单——白名单过滤发生在映射层（02），契约只存值
- [x] `page` 是 0 起（对齐 API 的 `pageNum`），文档字符串写明
- [x] `DataPackage = {package_id: str, units: list[Unit], provenance: dict}`
- [x] 删除 `metadata`（只有写入方没有读取方；学号/任务已在路径里）
- [x] `package_id` 取 `"{task}/{submission}"`
- [x] `to_dict` / `from_dict` 同步更新——`from_dict` 是把产物里的 `unit_ids` 解读回原文的唯一入口
- [x] `DataPackage` 仍校验 unit id 不重复
- [x] 测试：字段往返序列化、重复 id 报错、`get_unit` 命中与未命中

## Comments

`UNIT_KINDS` 那个 frozenset 一并删除——`type` 现在是 API 原值，本系统不维护取值表。
