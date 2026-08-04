# 07 — CLI：`parse` 子命令，`eval` 改吃 `--submission`

**What to build:** `scripts/cli.py` 的入口调整。

**Blocked by:** 05, 06

**Status:** done

```
python scripts/cli.py parse <files...> --task T --submission S [--force]
python scripts/cli.py eval --task T --submission S [--dim a1]
```

- [x] 新增 `parse` 子命令：位置参数为一次提交的**全部源文件**（不是单个文件）
- [x] `eval` **不再接受文件路径**，改吃 `--submission`，按约定去 `packages/{task}/{submission}/package.json` 找包
- [x] 包不存在时报人话错误，提示先跑 `parse`
- [x] `--submission`，**不带 `-id` 后缀**
- [x] 解析类错误纳入 `_USER_FIXABLE_ERRORS`（打一行人话，不甩 traceback）
- [x] `prompt` 子命令同步改用 `--submission`
- [x] README 的快速开始与配置表格同步更新
- [x] 测试：`parse` 参数解析、`eval` 找不到包时的错误、两条命令串起来跑通（注入假调用函数）

## Comments

**不做** "`eval` 吃原始文件时自动跑一次 parse" 的便利路径——它会把网络/配额失败请回评价链路，并藏起这一步要花钱的事实。
