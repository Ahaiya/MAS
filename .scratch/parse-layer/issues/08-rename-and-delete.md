# 08 — 术语改名与旧路径删除

**What to build:** 收尾。样本→提交全仓库改名，删掉被解析层取代的模块。

**Blocked by:** 07

**Status:** done

## 改名：样本（sample）→ 提交（submission）

- [x] `CONTEXT.md` 词条
- [x] 产物路径层 `artifacts/{task}/{sample}/{dim}/` → `{task}/{submission}/{dim}/`
- [x] `src/artifacts.py`（`sample_dir` 等）
- [x] `src/engine.py` / `scripts/cli.py` / `scripts/server.py` 的文档字符串与变量名
- [x] `.scratch/engine-v2-refactor/spec.md`、`README.md`
- [x] 测试里的相应命名

## 删除

- [x] `src/segment.py`
- [x] `tests/unit/test_segment.py`
- [x] `src/utils/dialogue_sources.py`（只为历史训练数据的对话日志格式服务）
- [x] `src/utils/` 若因此变空则一并删

所有文件统一走 parse，不按后缀分路——保留本地直读会让同一份材料改个后缀就得到另一套 `unit_ids`。

## 验证

- [x] `grep -rn "sample" src scripts tests` 无残留（除非确实指统计学意义上的样本）
- [x] `grep -rn "segment\|dialogue_sources" src scripts tests` 无残留
- [x] 全量测试通过
