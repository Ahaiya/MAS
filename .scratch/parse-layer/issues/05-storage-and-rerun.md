# 05 — 落盘布局与重跑语义

**What to build:** 把 04 的响应和 02 的包写到磁盘，并定义重跑时的行为。

**Blocked by:** 02, 04

**Status:** done

```
packages/{task}/{submission}/
├── raw/<源文件名>.json    # 完整响应，分页已合并
└── package.json           # 派生的编号空间
```

- [x] raw 原样落盘，不裁剪、不改写
- [x] `package.json` 由 raw 派生
- [x] **不写进 `artifacts/`**（那是可随时删掉重跑的产出目录，解析结果是花钱买来的输入）
- [x] **不在 `artifacts/` 里另存副本**
- [x] 重跑：raw 已存在则跳过该文件、只重建 package
- [x] `--force` 强制重解析
- [x] `packages/` 加进 `.gitignore`（学生材料隐私敏感，与 `data/` `artifacts/` 同）
- [x] 测试：重跑跳过已有 raw、`--force` 覆盖、raw 与 package 都落地

## Comments

raw 与 package 都存的理由在 spec 里：raw 让字段取舍可逆，package 让 `unit_ids` 不随白名单改动漂移。二者缺一不可，不要在实现时"优化"掉其中之一。
