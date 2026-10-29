# Tekla 2017 原生证据读取器

独立查询模型并保存 `teklaNativeEvidence.v1`。用于原生零件图的新读取路线，不依赖旧导出器，不生成图纸。Python `read-native-facts` 消费该证据，建立带来源的事实目录。

当前读取器版本r2b.11、几何审计r2b.13。目标和 Boolean 操作体的 `rawSolid`（SDK RAW、不含 Boolean）及操作体 ID，与 `finalSolid` 分别保存。隐式当前选择中的非Part对象保留为`SKIPPED_NON_PART`，关联孔组仍从Part完整读取；全为非Part/空选择及显式ID中的非Part仍报错。

r2b.13 在逐操作证明失败后，对能读出两块厚度盖、厚度、外轮廓和孔的平直板改用终态实体。斜穿厚度的圆孔仍是普通 `nΦd`，孔心取盖上环的投影中心。单独碰不到实体的切面、部分厚度坡口、矛盾来源和已提供但对不上的 NC 仍阻断。读取器和 schema 不变。

Python审计r2b.12先生成通用几何事实和 `partType`，再选择专用核验。平直/折弯形态不由runtimeType或profile决定；非平直及缺证在 `TYPE_ROUTING` 阶段明确停止，来源异常另为 `SOURCE`。已识别平直板仍需完整加工终态证明。原始读取器和schema不变，无需为本次分层重新采集已有完整证据。见[设计](../../docs/design/2026-10-26-geometric-part-types.md)。

r2b.9 不再仅因 Beam/PolyBeam 类型拒绝平板：没有板轮廓语义时，要求完整 RAW 直边平面壳，寻找唯一薄向并与最终实体核对；支持贯穿 CutPlane。曲边缺参数、非共面、变厚或未知加工仍阻断。旧 ContourPlate 证据可回放，其他类型缺 RAW 时需重采。见[建模命令无关设计](../../docs/design/2026-10-26-modeling-independent-flat-plate.md)及实测。

r2b.11 增加 `boltGroups[].axisIntersections`：特殊槽孔螺栓组逐轴读取实际硬件和当前目标板的 `Solid.Intersect` 交点及有限查询端点。关联板数不设限制；孔形归属另由本次精确编号NC逐孔唯一对应证明，不根据Hole标志/关联列表次序猜测。实际穿透不冒充槽形证据，NC也不替代三维坡口证明。见[设计](../../docs/design/2026-10-26-target-hole-penetration.md)及现场验收。

## 依赖与使用

Windows x64、.NET Framework 4、安装版 Tekla 2017 SDK。读取时 Tekla 必须打开目标模型，并与读取器运行于可通信的用户会话。build.ps1 的 `-SdkDir` 和 exe 的 `--sdk-dir` 可指定安装目录，默认 `D:\Program Files\Tekla Structures\2017\nt\bin\plugins`。

在仓库根执行：

```powershell
& tools/tekla2017_native_reader/build.ps1
& outputs/native-reader-build/TeklaNativeReader.exe --self-test
# 未提供 --ids 时只读当前模型选择；必须用尚不存在的输出路径。
& outputs/native-reader-build/TeklaNativeReader.exe --out outputs/native-reader/selected.json
# 明确模型 ID 可用 --ids 参数，值为逗号分隔的数字。
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m zijinhua_tekla.cli read-native-facts --evidence outputs/native-reader/selected.json --out outputs/native-reader/facts.json
```

读取器退出码：0 本次采集无异常；1 部分采集失败；2 参数/连接/输出失败。`read-native-facts` 退出码：0 原始采集状态完整；1 不完整；2 输入非法或拒绝覆盖。**当前事实目录的 manufacturingStatus 均为 BLOCKED**。退出码 0 不表示解析几何准确性、出图或生产验收通过。

## R2a 来源与 NC 见证

当前 readerVersion 为 `native-reader.r2b.11`；原始 schema 仍为 v1，保留 R2a 的 GUID、读取前后修改时间、DSTV 坐标系和面来源 ID。R2b 修正厚度倒角属性为 SDK 的 `DZ1/DZ2`；历史 `Dz1/Dz2=UNAVAILABLE` 不补零。`sourceIdentity=READ_CONSISTENT` 只表示读取期间身份/版本一致。旧 R1 输入仍可读，但没有这些字段时标 UNAVAILABLE。

NC 采集必须显式给出已授权 ID、已有 `.ncfs` 文件和新输出目录，例如：

```powershell
& outputs/native-reader-build/TeklaNativeReader.exe --capture-nc --ids 71362196,71089199,71362228,70992794,71285999 --nc-settings-file 'D:\Program Files\Tekla Structures\2017\Environments\china\system\NC file settings 1.ncfs' --out outputs/native-source-gate/capture
python -m zijinhua_tekla.cli audit-nc-witness --capture outputs/native-source-gate/capture/capture.json --max-resolution-mm 0.01 --out outputs/native-source-gate/audit.json
```

示例 ID 为本会话已授权样本，其他模型必须替换。`--max-resolution-mm` 是显式 NC 核验分辨率预算，不是制造公差，也不是默认几何容差。

`capture.json`（teklaNcCapture.v1）保存导出前/后原始 JSON、设置、累计日志前/后及 NC 文件的原字节/base64/SHA256，记录导出时间、编号和选择恢复。采集会暂时通过 API 选择指定零件，finally 恢复；编号过期即阻断，不自动编号。不修改模型实体、图纸或 NC 设置。

审计输出 `ncWitnessAudit.v1`：退出码 0 表示全部 CANDIDATE；1 表示有 BLOCKED；2 为输入非法/拒绝覆盖。CANDIDATE 仅通过当前来源与语法检查；全部制造状态仍 BLOCKED，geometryStatus=NOT_PROVEN、settingsBinding=UNCONFIRMED。API 按模板名加载，观察到的文件摘要不能证明实际生效配置。未知块、量化过粗、错号/过期编号、文件/日志不一致等仍阻断；只分析本次累计日志的新增部分。同编号代表实例 GUID 可以不同，KA 标记展开范围而不否定 NC。

## R2b 解析与证据对账

`audit-native-geometry` 算法 r2b.7 独立检查原生来源，再解析轮廓/孔槽、实体、拓扑和操作覆盖。无需 NC 时直接消费原始读取：

```powershell
python -m zijinhua_tekla.cli audit-native-geometry --evidence outputs/native-reader/selected.json --out outputs/native-reader/geometry.json
```

已有完整 capture 时还检查其中 NC 的适用性与可解释字段：

```powershell
python -m zijinhua_tekla.cli audit-native-geometry --capture outputs/native-source-gate/capture/capture.json --nc-tolerance-mm 0.01 --max-resolution-mm 0.01 --out outputs/native-source-gate/geometry.json
```

`--evidence` 与 `--capture` 二选一；模型数值预算默认采用用户确认的0.1 mm（含），可通过 `--model-tolerance-mm` 显式覆盖，capture 还要求 NC 字段预算与最粗分辨率。以上不是制造公差。输出 `nativeGeometryAudit.v1`，含原始证据摘要、逐特征 sourceRefs、双精度候选、实体覆盖和适用 NC 行匹配。`featureEvidence` 列出每段/每孔/厚度/操作的证明义务，`evidenceSufficiency` 聚合充分性。退出码 0 为全部 EVIDENCE_MATCHED、1 为至少一件 UNRESOLVED、2 为非法输入或拒绝覆盖。**EVIDENCE_MATCHED 仍非精确成品证明，制造状态始终 BLOCKED。**

`nativeSource` 与 `ncSource` 分开。当前 r2b.4 的 NC 来源按精确零件编号关联：同号多个实例可共享文件，日志代表 GUID 只作追溯；KA 展开 NC 也可为 CANDIDATE。`ncHoleInventory` 独立保留孔形/尺寸/NC 行号与坐标域；展开到原形的对应待建立时仅跳过原形对账。NC 文件/导出失败为 UNAVAILABLE，不直接否定目标；已解释数值矛盾仍阻断。该规则覆盖早期严格 GUID/KA 拒绝逻辑，详见 r2b.4 方案。

支持已证明等厚原板的有限平板类型（不以建模命令区分）、直倒角/圆弧、圆孔/单轴槽，以及完整三维核验的整边平面坡口。未知几何和孔作用证据缺口仍阻断；STUD 单独保留。精确BREP和通用写图尚未完成；r2b.8已接入支持范围内的材料终态与表达引擎，见 R2b 设计 和 实测。

r2b.5 增加 Boolean 的 `operativeGeometry`（自身坐标系、规格、完整轮廓），支持零尺寸圆角与扩孔解析，输出 `finishedHoleCandidates` 来源映射及 `circularCuts` 两面边界核查。圆弧不共圆时保留参数/残差，不自动扩大预算；候选不等于成品证明。见 特征规则。

r2b.6审计修复仅开孔实体的微小轴偏斜误阻断，在自身坐标系核验后按长度预算检查目标板厚两面及孔边界；读取器仍r2b.5，无需重编。该历史切孔体检查不冒充孔壁观察；r2b.8允许据此结合完整Boolean壳确认孔作用，见核验记录。

r2b.7增加`planarCuts`、`cutFinalState`、`finalOutline`：支持等厚单块ContourPlate上直线/圆弧贯穿Boolean切割的解析组合和全部终态面边核验。内圆孔、槽孔、闭合切口及边缘缺口均按最终边界表达，原outline和全部操作保留。真实圆环78面228边完整匹配，可在模型预算内逐特征SUFFICIENT；制造仍BLOCKED；当时的螺栓孔独立观察/表达适配缺口已由r2b.8工程规则及原生表达入口处理，通用写图仍待补。见终态验证。

## 原生加工表达入口

`python -m zijinhua_tekla.cli plan-native-expressions --evidence <原始JSON> --out <新报告>`重新核验来源与完整材料，输出`nativeExpressionBatch.v1`。确认螺栓孔按BOLT-THROUGH-01贯穿处理，STUD-NO-HOLE-01不产生孔；仅开孔体保留形状/厚度检查。READY仅表示可规划表达，不等于已写图或制造放行。见方案和验证。

## 核心职责

- 记录编号、坐标系、实体边/面/环、建模轮廓及 chamfer、全部孔组及作用零件、Boolean operative solid、Fitting/CutPlane 平面和子对象。
- 保存公差、标准、Hole 开关与 slot 参数；不把 STUD 变成孔，不按 false 开关过滤组。
- 不舍入坐标、不删近点、不合并同端点边、不从端点猜圆弧。采用 round-trip double JSON；SDK 本身的几何表示不因此变为精确解析曲线。
- 临时切全局工作平面并在 finally 恢复，记录前后值。不插入/修改/删除模型实体，不提交模型，不操作图纸或鼠标键盘。
- 输出不覆盖旧文件。原始证据、模型路径和编译产物留在忽略的 outputs/，不提交现场全量数据。

## 文件与验证

Program.cs 为读取器及离线数值/大字符串自检，NcCapture.cs 为显式 NC 采集；build.ps1 编译到忽略目录。Python 来源层为 [native_facts.py](../../src/zijinhua_tekla/part_drawing/native_facts.py) 和 [nc_witness.py](../../src/zijinhua_tekla/part_drawing/nc_witness.py)，R2b 入口为 [native_geometry.py](../../src/zijinhua_tekla/part_drawing/native_geometry.py)，解析公式和 NC 对账分别在同目录 analytic_geometry.py/nc_geometry.py。测试见根目录 tests/test_native_facts.py、test_nc_witness.py、[test_native_geometry.py](../../tests/test_native_geometry.py)。本目录无独立 tests 子目录：使用 exe --self-test、根 tests 与 API 实读验证。

见 [DESIGN](DESIGN.md)、阶段路线、R1 验证、R2a 验证。
