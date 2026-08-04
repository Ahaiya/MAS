# 04 — Document Mind 客户端

**What to build:** `src/parse/docmind.py`。一个文件进、完整 layouts 出。本层最容易出错、错了最贵的部分。

**Blocked by:** 03

**Status:** done

- [x] `SubmitDocParserJobAdvance` 本地文件流直传（**不用** URL 版本，避免引入 OSS 与第二套凭证）
- [x] `QueryDocParserStatus` 轮询：间隔与上限从配置读，默认 10s / 120 分钟
- [x] `GetDocParserResult` 用 `LayoutNum` / `LayoutStepSize` **分页拉全**；拉全之前不算成功
- [x] 分页的多个响应**合并成一份**：`layouts` 按 `LayoutNum` 顺序拼接，其余字段取最后一次
- [x] 错误分类：配额 / 格式不支持 / 服务失败 / 网络 / 超时，各自可区分（不要统一吞成一个 Exception）
- [x] **超时不算永久失败**：抛出的错误里带 job id，CLI 打印它，结果 24 小时内可续查
- [x] **接缝**：客户端接收一个"发起调用"的函数作为参数，默认真 SDK。**不抽接口、不做工厂、不做 FakeParser 类**
- [x] 测试（注入假调用函数，零网络）：轮询直到完成、轮询超时保留 job id、`Status: Fail` 正确分类、分页拼接完整、分页中途失败不产出半份结果、"处理中"不被误判为失败

## Comments

轮询提前退出和分页少拉一段这两种 bug 在纯函数侧不会暴露，而后果是拿着半份材料评分。测试必须覆盖到。
