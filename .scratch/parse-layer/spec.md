# 解析层（parse）：文档接入

Status: done

> 本 spec 是一次 grilling 逐项拍板的结果。凡与既有代码、既有 spec、`docs/REFACTOR_DESIGN.md` 冲突处，**以本 spec 为准**——既有实现只是参考，不是权威。

---

## Problem Statement

引擎目前只吃 `.md/.txt`，靠 `src/segment.py` 用正则把整篇文本切成单元（散文按句、围栏整块、`|` 每行、`#` 一行）。真实的评价对象是学生提交的报告 PDF、Word、PPT、图片，它们进不来。

而一旦引入文档解析服务，`segment.py` 那条路径就不该继续存在了：解析服务返回的 layouts **已经是切好的块**，每块自带类型、页码、坐标、markdown 内容。把它们拼成一根字符串再用正则切回块，是把服务已经算好的结构信息扔掉再猜一遍，且猜得更差。

## Solution

新增一层**解析（parse）**，作为独立于评价的前置步骤：

- 用阿里云 Document Mind **文档解析（大模型版）**把任意格式的源文件解析成 layouts。
- **一个进白名单的 layout = 一个 Unit**，不做字符串拼接、不做句级细分。
- 解析结果**原件（raw）原样落盘**，派生的数据包另存——前者让字段取舍可逆，后者让编号锚点不随代码改动漂移。
- `parse` 与 `eval` 是两条命令：解析一次付一次钱，评价可以反复迭代。

系统抽象不变：引擎仍然只认「量规 + 数据包 → 评价」，不关心数据包从哪来。

## User Stories

1. 作为教师，我想直接提交 PDF/Word/PPT/图片而不必先转成 markdown，以便用学生实际交上来的材料评价。
2. 作为教师，我想解析出的每个单元带页码，以便核验证据时知道去原始材料的第几页找。
3. 作为教师，我想图片、公式、表格的增强识别是可开关的配置，以便按材料实际情况决定要不要为它们付费。
4. 作为教师，我想知道解析时有哪些内容被剔除了、各多少个，以便判断材料是否被完整地读进来了。
5. 作为调用方，我想解析和评价是两条独立命令，以便调 prompt、换模型、改量规时不必重新付费解析同一份材料。
6. 作为调用方，我想一次提交的多个文件被解析进同一个数据包、共享同一编号空间，以便证据能跨文件统一引用。
7. 作为调用方，我想一次提交里任何一个文件解析失败时整体失败，以便不会拿着残缺材料评出一个看不出问题的低分。
8. 作为调用方，我想解析失败重跑时已经成功的文件不重新解析，以便不重复付费。
9. 作为调用方，我想轮询超时后能拿 job id 续查，以便不必重新提交重新付费。
10. 作为审计者，我想解析服务的完整响应原样落盘，以便将来需要坐标、层级、置信度等字段时直接从它重新推导，不必重新解析所有材料。
11. 作为审计者，我想派生的数据包也落盘，以便历史产物里的 `unit_ids` 永远指得准，不随白名单或映射规则的改动而漂移。
12. 作为维护者，我想所有文件走同一条解析路径、不按后缀分路，以便 `unit_ids` 的语义只有一种。
13. 作为测试者，我想轮询状态机、分页拼接、错误分类都能在不打网络的情况下测到，以便"付了钱拿到半份材料"这类 bug 在测试里暴露。

## Implementation Decisions

### 用哪套 API

**文档解析（大模型版）**，三步异步：

- `SubmitDocParserJobAdvance` —— 本地文件流直传。**不走** URL 版本 `SubmitDocParserJob`：URL 版要求材料先落到公网可达的 OSS，而学生材料隐私敏感，为此引入一个 OSS 桶和第二套凭证不值得。
- `QueryDocParserStatus` —— 轮询。
- `GetDocParserResult` —— 取结果，靠 `LayoutNum` / `LayoutStepSize` **分页**把 layouts 拉全。

注意：常被搜到的 `SubmitDocStructureJob` / `GetDocStructureResult` 是**文档结构化（标准版）**，返回的 `layouts` 只有 `text` 没有 `markdownContent`，不是本层用的 API。

### layout → Unit

一个进白名单的 layout 就是一个 Unit。不拼接 markdown 字符串，不做句级细分。

- **白名单**：`title` / `text` / `table` / `table_name` / `table_note` / `figure` / `picture` / `formula` / `contents`
- **剔除**：`head` / `foot` / `page` / `logo` / `corner_note` / `side` / `cate`
- 白名单外的 type（含 API 将来新增的）默认**不进**，按 type 计数记入 `provenance.excluded_layouts`——挡住噪音，但绝不静默丢弃。
- **白名单写死在代码里，不进配置**。它决定的是编号身份而非内容质量：开关调错得到的是质量差些的材料，看得出来；白名单调错得到的是编号与历史不可比的材料，看不出来。这两件事不该由同一个文件交给同一个人调。

被剔除的是页眉、页脚、页码块、水印/校徽、侧边栏、目录条目——它们每页一个、内容重复，进了单元会占满 select 阶段的预览，而且是合法的 `unit_ids`，模型完全可以引一个页脚当证据，越界校验拦不住。

### 契约（`src/contracts/package.py`，重写）

```python
@dataclass(frozen=True)
class Unit:
    id: int           # 全局连续编号，跨文件共享同一编号空间
    markdown: str     # 取 layout.markdownContent
    type: str         # layout.type 原值，不做翻译
    source_file: str  # 该单元来自提交里的哪个文件
    page: int         # layout.pageNum，0 起

@dataclass(frozen=True)
class DataPackage:
    package_id: str          # "{task}/{submission}"
    units: list[Unit]
    provenance: dict         # 见下
```

字段命名的依据：

- `markdown` 而非 `text`——raw 里每个 layout **同时有 `text`（纯文本）和 `markdownContent`（带格式）**，我们取的是后者。叫 `text` 会让任何对着 raw 排查问题的人认错字段。
- `type` 而非 `kind`——取值就是 API 的 type 原值，叫 `type` 就不需要一张翻译表。
- `page` 而非 `page_num`——API 的 `pageNum` 是 **0 起**的，`page_num` 读起来像 1 起的页码。

**不存**的 layout 字段：`uniqueId`（我们的 `unit.id` 已是权威编号，多一个 id 只会引发"引哪个"的歧义）、`layoutConf`（没有任何逻辑会因置信度改变行为）、`subType`（20+ 取值的 API 私有词汇，`type` 已够分派）、`pos` / `level`（暂无消费方）。要它们的那天从 raw 现推即可，不必重新解析任何材料——这正是存 raw 的意义。

`provenance`：

```
{
  parsed_at: <ISO 时间戳>,
  source_files: [...],
  options: {llm_enhancement, enhancement_mode, formula_enhancement},
  excluded_layouts: {<type>: <count>}
}
```

**删除 `metadata` 字段**：它现在只有写入方没有读取方（`segment.py` 透传进去，全仓库无一处读），文档字符串说它透传学号/任务，而两者都已在路径里。`metadata` 是个没有主语的袋子，什么都能叫 metadata，所以必然长成杂物间。真有前端要透传的字段时再加一个有名字的字段。

**删除 `char_range` / `speaker`**：layout 世界里 `char_range` 没有真实来源（要算就得维护一根拼接串，那是被否掉的方案的尾巴）；`speaker` 是为历史训练数据的对话日志格式服务的。两者同样零消费方——五个 prompt 一律只取 `{id, kind, text}`，`rater.py` / `adjudicator.py` 只用 `unit.id` 做越界校验，前端一次都没读过。

### 边界：parse 与 eval 分离

解析是异步的（官方口径 10s 一轮、最长 120 分钟），花真实时间和真实钱；而评价要在同一份材料上反复迭代（调 prompt、调量规、换模型）。绑在一起就是每次迭代重付一次。

```
python scripts/cli.py parse <files...> --task T --submission S
python scripts/cli.py eval --task T --submission S --dim a1
```

`eval` 不再吃文件路径，改吃 `--submission`，按约定去 `packages/` 找包——与 `--task` 的既有取向一致（路径由约定固定，不做引用解析）。

**不做**"`eval` 吃原始文件时自动跑一次 parse"的便利路径：它会把网络/配额失败请回评价链路，并藏起"这一步要花钱"这个事实。

### 落盘

```
packages/{task}/{submission}/
├── raw/<源文件名>.json    # 完整响应，分页合并成一份
└── package.json           # 派生的编号空间
```

- 一次提交的所有东西自包含在一个目录里，删掉重来就是删一个目录。
- **不放进 `artifacts/`**：`artifacts/` 是"觉得不对就整个删掉重跑"的产出目录，而解析结果是花钱买来的**输入**。
- **不在 `artifacts/` 里另存运行时快照**：同一份包多次评价会存出多份一模一样的副本。
- **raw 和 package 都存**。raw 管"这份材料到底被识别成了什么"，package 管"这次评价所依据的编号空间"。只存 raw 每次现推的话，白名单加一项就会让全体编号平移，历史 `feedback.json` 里的 `unit_ids` 悄悄指向别的内容——而 `unit_ids` 是本系统整条证据链的锚，锚点的定义不能随代码改动漂移。
- 分页拉回来的是多个响应，**合并成一份**存：`layouts` 按 `LayoutNum` 顺序拼全，其余字段取最后一次。分页是传输层的事，跟"材料被识别成了什么"无关，存成 N 份只会让每个读它的人先自己拼一遍。

### 配置

**新文件 `configs/parse.yaml`**，不进 `model_config.yaml`：后者的 `providers` 按词汇表是「**角色**→模型端点」，角色名只有 rater_1/2/3/feedback，解析服务不是评委角色；且 parse 是引擎外的一步，不该由引擎的唯一配置来源养着。

装：endpoint、connect/read timeout、轮询间隔与上限，以及三个增强开关——

| 开关 | 默认 | 作用 |
|---|---|---|
| `llm_enhancement` | **开** | 版面还原质量（多栏、跨页表格） |
| `enhancement_mode: VLM` | **开** | 多模态图片理解 |
| `formula_enhancement` | **开** | 公式识别 |

三个全开的理由不是"功能多多益善"：不开 VLM，`figure` / `picture` 的内容就只剩 caption（学生截图常常没有图注，那就是空壳单元），那还不如把它们移出白名单；量规里 B1 明确评"概念模型或**数学模型**"，公式识别退化会直接让证据不可读。代价是按页计费更高、解析更慢，但一份材料只付一次——这正是把 parse 拆成独立一步的收益兑现处。

开关进配置是为了让教师按材料实际情况关掉不需要的部分。

**凭证**：`.env` 新增 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`（`alibabacloud_credentials` 的默认变量名）。阿里云 AK/SK 是两个值，不是单个 API key；已有的 `DASHSCOPE_API_KEY` 是百炼账号，**不是**同一套凭证，不要复用。

### 失败处理

**一次提交里任何一个文件解析失败 → 整个 submission 失败，不产出 package，非零退出。**

引擎内部的失败隔离（一个观测点挂了不拖垮其余）是对的，因为那是同一份材料上的多个判断，缺一个仍然诚实。但材料本身缺一块，后面每一个判断都建立在残缺输入上——那不是隔离，是污染。报告 PDF 挂了只剩 PPT 却照样评出分，教师看到的是一个偏低的分数，而看不出材料缺了一半。

配套：

- **成功文件的 raw 照常落盘**——整体失败不等于把已付费的结果扔掉。
- **轮询超时（120 分钟上限）既不算成功也不算永久失败**——打印 job id 退出，结果 24 小时内可凭它续查，不重新提交重新付费。
- **分页拉全后才算这个 job 成功**——拉了一半就当成功，会得到一份静悄悄缺尾的材料。
- 一次提交的 N 个文件**并发**提交 N 个 job、统一轮询。轮询是纯等待，串行等于把等待时间乘 N。
- 重跑时发现 raw 已存在则**跳过**该文件、只重建 package；`--force` 强制重解析。

### 代码组织

- `src/parse/docmind.py` —— 网络侧：提交、轮询、分页、错误分类。
- `src/parse/package.py` —— 纯函数：layouts → 白名单过滤 → 编号 → `DataPackage`。

拆两个文件是因为失败模式不同：前者全是网络/配额/超时，后者给定输入必然给定输出；混在一起，纯函数部分的测试也得搭 mock。

命名用 `parse` 而非 `ingest`：前者说它**做什么**（把文档变成结构化单元），后者说它在管道里的**位置**；产出物的性质是"解析结果"而非"被摄入的东西"。且 `ingest` 是旧设计文档里留了坑没实现的名字，复用会带进历史包袱。也不按供应商命名——不是为了留抽象层（按定论这是唯一实现，**不做 provider 接口、不做工厂**），纯粹是因为目录名写死供应商，将来换一家要改一堆 import 路径，而这个成本可以零代价避免。

### 删除

- `src/segment.py`、`tests/unit/test_segment.py`
- `src/utils/dialogue_sources.py`

所有文件统一走 parse，不按后缀分路。保留本地直读的话，同一份材料改个后缀 `unit_ids` 就不是一回事了——证据链的可核验性建立在"unit_ids 指的东西是确定的"之上。

### 术语改名

**样本（sample）→ 提交（submission）**，全仓库连带改：`CONTEXT.md`、产物路径层 `{task}/{submission}/{dim}/`、`src/artifacts.py`、CLI 参数 `--submission`（不带 `-id`）、既有 spec。

## Testing Decisions

- **接缝：`src/parse/docmind.py` 接收一个"发起调用"的函数作为参数**，默认是真 SDK，测试传假的。**不抽 `DocumentParser` 接口 + `FakeParser`**——`providers/` 那套接口+fake 有真实理由（多厂商多实现），解析只有一个实现，为它造接口和工厂纯属仪式。一个函数参数拿到同样的可测性，不新增任何类型。
- **必须测到的不纯逻辑**（这些 bug 不会在纯函数侧暴露，且错了最贵）：轮询提前退出、分页少拉一段、把"处理中"误判为失败、超时后是否保留 job id。
- **纯函数直测**（`src/parse/package.py`）：白名单过滤正确、被剔除的按 type 计数、编号全局连续、多文件共享编号空间、`markdown` 取的是 `markdownContent` 而非 `text`、`provenance` 内容完整。
- **失败路径测试**：N 个文件中一个失败 → 不产出 package、非零退出、成功文件的 raw 仍在。
- 遵循全局规则：TDD（红 → 绿 → 重构），覆盖 80%+。

## Out of Scope

- **上下文预算与丢弃策略**——`segment.py` 现有的"超预算从尾部丢单元"逻辑不迁移。parse 产出的是**完整**包、不做任何裁剪；预算裁剪归引擎侧（预算值耦合模型上下文窗口，属于引擎；且这样换模型不必重新付费解析）。**当前的丢弃做法本身不合理，留到解析层之后的 segment 讨论里重新设计。**
- **多供应商解析**：不做 provider 接口、不做工厂、不做注册表。
- **`pos` / `level` / `subType` 等字段进 Unit**：raw 已存，要时现推。
- **`eval` 自动触发 parse** 的一站式命令。
- **前端对接**：页码、坐标高亮等前端契约不在本 spec。

## Further Notes

- grilling 过程中纠正的一个事实错误：用户给出的 SDK 指南链接指向的是**文档结构化（标准版）**，它不返回 markdown。返回 `markdownContent` 的是**文档解析（大模型版）**，接口名不同。
- 支持格式与上限（API 侧）：PDF / Word / PPT / Excel / 图片 / markdown / HTML / EPUB / RTF / TXT；最大 15000 页、150MB，单张图片 20MB。
- 本 spec 的字段取舍全部建立在"raw 原样落盘"之上——正是它让字段选择变成可逆的、零成本的决定，因此不值得反复纠结。
