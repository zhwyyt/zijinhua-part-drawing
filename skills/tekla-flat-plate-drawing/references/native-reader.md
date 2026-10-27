# 原生证据读取路线

项目 tools/tekla2017_native_reader 已实现 Tekla 2017 R1 读取及 R2a 来源/NC 采集，Python CLI read-native-facts 已实现事实目录，audit-nc-witness 已实现来源检查。先定位当前 checkout 并读模块 README/DESIGN，不假设其他机器有同一 SDK 路径。

```powershell
& tools/tekla2017_native_reader/build.ps1
& outputs/native-reader-build/TeklaNativeReader.exe --out outputs/native-reader/selected.json
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m zijinhua_tekla.cli read-native-facts --evidence outputs/native-reader/selected.json --out outputs/native-reader/facts.json
```

未指定 --ids 时读当前选择；r2b.8将混选的非Part对象保留在selection并标SKIPPED_NON_PART，孔组仍从每块板完整读取；全为非Part或空选择报错，显式--ids中的非Part仍报错。会话已授权 ID 可通过 --ids 逗号分隔读取。输出用新路径。只用查询 API，临时全局平面需恢复，不需要鼠标键盘控制。运行权限必须能与 Tekla 会话通信。

- COMPLETE 只代表当前能力内采集完成；仍要查看 API_UNAVAILABLE 和制造缺口。退出码 0 不是出图准入。
- DIRECT 是原始读取值，DERIVED 是计算/候选，UNKNOWN 不补默认值。保留 rawEvidence、摘要、sourceRef，不将舍入、近点清理或合并曲边后的结果当成原始事实。
- SDK 实体枚举未必提供精确解析曲线。当前无可靠圆弧参数/面类型；chamfer 不直接等于成品半径，端点唯一匹配也仅是候选。
- 孔组全部保留，Hole1..5=false 不删除；STUD 分类为附件而非钻孔。孔径与 slot 公式只作候选，结合目标、方向、作用和实体证明后才能使用。
- R1/R2a 全部 manufacturingStatus=BLOCKED。R2 未完成时，不强转旧 snapshot、不生成伪 VERIFIED context，不进入自动写图。

## R2a 可执行检查

先取得当前模型已授权 ID、实际存在的 NC 设置路径及新输出目录，再运行；不要照搬其他模型 ID：

```powershell
# $partIds 为已授权 ID 的逗号分隔字符串；$ncSettings 为已核查的 .ncfs 路径。
& outputs/native-reader-build/TeklaNativeReader.exe --capture-nc --ids $partIds --nc-settings-file $ncSettings --out outputs/native-source-gate/capture
python -m zijinhua_tekla.cli audit-nc-witness --capture outputs/native-source-gate/capture/capture.json --max-resolution-mm 0.01 --out outputs/native-source-gate/audit.json
```

采集保存 before/after 原始 JSON、设置文件、累计日志前后和全部 NC 原字节与摘要；会临时通过 API 选择授权 ID 并恢复，不需要桌面控制。编号过期不自动编号。日志必须使用本次新增部分，不能将旧成功记录归到新采集；采集失败不用旧包冒充成功。

核对 GUID/修改时间/DSTV 坐标系及前后原始字段。READ_CONSISTENT 不是事务锁；NC 按精确 PART_POS 归属，日志编号、文件名和 ST 编号须一致；编号最新且采集有效时，同号实例共享文件，日志 GUID 可为组内代表实例。审计退出码 0 表示全为 CANDIDATE、1 为至少一件 BLOCKED、2 为非法输入/拒绝覆盖；前两种结果均未证明成品。`--max-resolution-mm` 是显式见证分辨率预算，不是制造公差，0.01 只是本机两位小数输出的试验值。

API 按模板名加载；设置文件保存成功不代表实际生效配置已绑定。settingsBinding=UNCONFIRMED、geometryStatus=NOT_PROVEN 不构成制造放行。未知块/修饰符、量化超预算、错编号、日志变化等限制 NC 的适用性；同号其他代表实例不再阻断，KA 仅标记展开表达；目标原生身份变化单独阻断目标。不要把 R2a 对 NC 的 BLOCKED 直接传播为目标几何失败。

## R2b 解析候选与证据核验

当前读取器版本 r2b.8，厚度倒角必须读取 SDK 的 `DZ1/DZ2`。旧 R1/R2a 证据的 `Dz1/Dz2=UNAVAILABLE` 不能按 0 使用，应重新采集。审计算法 r2b.8 先运行纯原生证据核验，不要求先导出 NC：

```powershell
python -m zijinhua_tekla.cli audit-native-geometry --evidence outputs/native-reader/selected.json --out outputs/native-reader/geometry.json
```

已有完整 NC capture 时可核验其中的可用见证：

```powershell
python -m zijinhua_tekla.cli audit-native-geometry --capture outputs/native-source-gate/capture/capture.json --nc-tolerance-mm 0.01 --max-resolution-mm 0.01 --out outputs/native-source-gate/geometry.json
```

`--evidence` 和 `--capture` 二选一。模型核验默认采用用户确认的0.1 mm（含），CLI可用 `--model-tolerance-mm` 显式覆盖，API的audit_native_geometry/load_geometry_audit同样默认0.1；原始双精度不舍入，输出记录实际预算。capture 另需显式 NC 核对预算和 NC 最粗分辨率，示例0.01仅为见证试验值，不是制造公差。读取器与解析器不依赖旧导出器。

核查源参数的直线/直倒角/凹圆弧/圆角/三点弧候选，双厚度层及所有侧面、逐段覆盖，圆孔/单轴槽的参数/轴向/实体与 NC 一一对应，并排除自交、孔越界、重复与重叠。CHAMFER_NONE 的非零 X/Y 是未生效参数；三点弧不用 X 充当半径。NC 槽端定位不能当孔中心。孔模式实体与紧固件实体分开；`Bolt=true`按用户确认的贯穿孔规则处理，仍核对参数和目标关联；多目标特殊槽孔/扩孔需明确逐板孔形。STUD在孔公式前作为表面焊接附件排除，不生成孔。

输出 nativeGeometryAudit.v1 / r2b.8：退出码 0 为全部 EVIDENCE_MATCHED、1 至少一件 UNRESOLVED、2 输入非法/拒绝覆盖。EVIDENCE_MATCHED只表示可用证据匹配；`featureEvidence`逐项给出sourceRefs、proofStatus、missingProofs和ncRole，`evidenceSufficiency`聚合SUFFICIENT/INSUFFICIENT/CONFLICT。全部已确认孔槽与Boolean切割统一求解后可在模型预算内SUFFICIENT；`materialFinalState`记录完整材料结果、每源贡献、工程规则或实读孔壁依据。manufacturingStatus仍BLOCKED，不得传给旧引擎伪装为VERIFIED。

`nativeSource` 检查目标来源，r2b.2 的 `sourceStatus/sourceIssues` 指向其状态/问题，不再指向 NC；`ncSource` 为 NOT_REQUESTED/CANDIDATE/UNAVAILABLE。同号代表实例通过编号归属后为 CANDIDATE，`associationBasis=EXACT_PART_POSITION`、`instanceRelation=NUMBERED_GROUP_REPRESENTATIVE`，允许继续对账。KA 同样可为 CANDIDATE，`geometryRepresentation=UNFOLDED`；仅原形对账 `ncCheck=SKIPPED/UNFOLDED_COORDINATE_MAPPING_REQUIRED`。`ncHoleInventory` 独立保留圆孔/槽孔和 NC 行号、坐标域；NC 导出/文件/配置失败标 UNAVAILABLE。适用且可解释的 NC 数值矛盾仍输出 CONFLICT；不同分段或未支持参考语义只记 NC 对应未解决。先看原生缺口；NC 可补参数核对，不能独自证明最终曲面、含孔实体或操作效果。有效配置绑定只属于所用 NC 的证明义务，不是全体零件的固定前置项。

支持范围内现可运行`plan-native-expressions --evidence <原始JSON> --out <新报告>`，直接重新审计原始证据后生成expressionRequirements.v1，不需要旧snapshot或VERIFIED context。完整材料、来源和厚度充分才READY；未知操作/子对象仍阻断。后续是写图前重查、原生布局/写图、保存重开和图面验收；旧样例脚本不是通用执行器。规格与验证见docs/design/2026-10-20-simple-plate-closure.md、docs/verification/2026-10-20-simple-plate-closure.md。

## r2b.3 孔作用组合证据

可选 `--hole-observations <独立观察JSON>`；这是旧r2b.3保留的补充路径。r2b.8不提供该观察也可依确认螺栓贯穿规则或已核验仅开孔体解决孔作用，不要求先导出NC。输入为本项目 `nativeHoleObservations.v1`，并非 SDK 原生返回类型。根字段：`schemaVersion`、`origin=TEKLA_NATIVE_DRAWING_GEOMETRY`、`nativeEvidenceDigest`（对应 raw nativePartFacts 的 canonical rawDigest）、`observations`。

每条观察带目标 `targetPartId`、孔组 `modelGroupId`，实际图纸/平面孔边界的 `drawingId/viewId/objectId`，另一个剖面及对象的 `sectionViewId/sectionObjectId`，`kind`、全局三维 `center`、`diameter`、`extension`、全局单位 `direction`（槽长轴）、全局三维 `entry/exit`。孔径、槽增量和入口/出口必须独立读取，不能复制候选参数。模型主关联或 otherParts 中必须明确含目标 ID；对象身份字符串须保留适配器的原始关联。

校验器核对目标、摘要、数量、一一对应、中心、形状、槽长轴及两厚度面贯穿。完整通过输出 `effectEvidence.status=COMPOSITE_CONFIRMED`，对应孔的 proofStatus 可为 SUFFICIENT；缺证为 INSUFFICIENT，明确矛盾为 CONFLICT。旧切孔体诊断保留在 solidCheck/solidDiagnostics；可用切孔体不匹配仍须解释，不被新观察抹去。孔清单完整性、精确轮廓、附件和切割效果仍独立判定，不能因此放行整件。

`spatialDomainCheck` 独立检查 finalSolid 的等厚挤出域、全部侧面/边、profile 厚度、孔轮廓内含及互不重叠；仅标 OBSERVED_PRISM_NOT_EXACT_SURFACE。因此可核验平面 PolyBeam 的空间作用域，但不宣称已恢复精确曲线或完成 PolyBeam 原形解析。

**可选独立孔几何采集尚无通用适配器；已确认贯穿孔不再以此为前置条件。** Drawing.Bolt、ModelIdentifier、SymbolContainsHole 不是实际孔形/贯穿深度；已有文字、人工圆、中心线及标注计划不满足输入契约。digest 仅绑定版本，不认证数据真实性。合成测试通过仅验证校验器，历史试验图不能自动充当独立观察。2026-10-20实读六个平板实例无观察输入，均SUFFICIENT/表达READY，制造仍BLOCKED；未把符号孔当孔壁证据。规格/验证：docs/design/2026-10-19-composite-hole-evidence.md、docs/verification/2026-10-19-composite-hole-evidence.md。

## 精确来源验证经验（本机 Tekla 2017）

- Edge 没有解析曲线参数；CalculatePolygon 文档明确是近似多边形，HIGH_ACCURACY 也不等于返回解析圆弧。不能仅因 FacetedBrep 名字带 BREP 就认作精确曲面。
- 官方 NC 可提供带符号半径、IK 内轮廓和 BO 孔槽，但本次坐标只输出两位小数；不覆盖模型双精度。孔可能在 IK，不只统计 BO；槽的导出定位字段不能直接当中心。
- 所选五件的两份 NC 日志 GUID 为同号其他代表实例；按用户确认的加工编号口径接受，精确编号/文件/ST/最新编号状态与本次日志仍要核查。KB-7、P-116 的 NC 已参与对账并匹配。
- PolyBeam NC 可能有 KA 和展开轮廓，保留为有效加工信息。P-3429 的两个 IK 是两 Φ30 圆孔（每孔两 R15 半圆）；可读数量/孔径，展开孔心不能直接当原形孔心。
- NC API 输出目录需带末尾分隔符，核对实际文件和日志；临时 API 选择必须恢复。编号不足不自动编号。本次 ModelInternal IFC 调用失败，不复用为生产路径。
- 项目来源规格为 docs/design/2026-10-17-exact-geometry-source-strategy.md；R2a 来源自动核验和 R2b 有限类型对账已实现，r2b.8已闭合支持范围内简单平板的材料与表达要求，未覆盖类型及通用写图仍待实现。R2a 历史记录见 docs/verification/2026-10-17-native-source-gate-r2a.md。

## r2b.4 编号归属与展开范围

以上来源策略按用户确认的同编号共享 NC 口径修正，覆盖早期“同号不同 GUID/KA 一律 INAPPLICABLE”的规则。相同文件可关联多个所选同号实例；同次导出的多条一致日志全部保留，不能挑掉冲突行。目标原生身份/版本检查独立保留。

`ncHoleInventory.status=READ` 表示支持的 NC 孔信息可解释；`coordinateScope` 区分 NON_UNFOLDED/UNFOLDED，`modelPositionMapping` 只有 ncCheck 完成对账才为 MATCHED。未实现的展开对应不等于模型或 NC 错误。规格/验证：docs/design/2026-10-19-nc-numbered-part-scope.md、docs/verification/2026-10-19-nc-numbered-part-scope.md。

## r2b.5 常规平板特征规则

r2b.5阶段读取器和审计算法增加特征规则，原schema仍v1。Boolean操作增加 `operativeGeometry`，包含切割体类型、坐标系、profile和完整建模轮廓；旧证据缺字段仍可读取，但不能补猜切割圆。

既有 `audit-native-geometry` 输出增加 `finishedHoleCandidates` 与 `circularCuts`。前者保留 sourceCount、candidateCount、sourceMappings、relations、inputComplete；状态CANDIDATE仅说明参数归并有解，不是实际孔作用证明。所有源孔仍在holeGroups。以上为r2b.5历史候选口径；r2b.8材料匹配后按最终孔清单对账NC，组合边界交由materialFinalState核验，未知来源仍阻断。

后者在切割体自身坐标系解析完整圆弧，核对共圆、实体、目标投影、贯穿范围及两面内环。`MATCHED_BOUNDARIES`只证明已核验的两面边界，侧面/操作交互另证；不共圆则保留operativeContour和circularityResiduals，不自动放宽显式预算。整件制造状态和正式写图准入仍独立保留。

规则同时支持X=Y=0的未生效ROUNDING及Y=0的OVERSIZED扩孔，仍须终态及逐目标作用核验。实现和验证见项目 docs/design/2026-10-19-flat-plate-feature-rules.md、docs/verification/2026-10-19-flat-plate-feature-rules.md。

r2b.6仅升级审计算法：Bolt=false且单一目标的孔组实体在自身坐标系做棱柱核验，再逐端面验证目标板厚和投影孔边界。solidCheck增加coordinateSystem、targetLevels和targetCapEvidence（实读端面映射及maxThicknessResidualMm）；basis=HOLE_ONLY_CUTTER_NOT_FINAL_HOLE_SURFACE。该检查能接受预算内微小偏斜，不冒充最终孔壁观察；r2b.8允许该证据与完整Boolean壳共同确认孔作用，不把Bolt=true紧固件当切孔体。

## r2b.7 简单平板切割终态

新增planarCuts（每个贯穿切割候选及切割体实体检查）、cutFinalState（解析材料差集、最终全部面边覆盖及operationMappings）和finalOutline（切割后外轮廓）。原outline保留建模轮廓，不直接用于切割后尺寸规划。最终内环进入finishedHoleCandidates，以sourceOperationIds追溯；圆/槽保留几何参数，其他闭合切口为CUTOUT并保留解析线弧。

circularCuts仍是旧的圆切割候选/两面环诊断；对于非圆或相交切割，其局部未匹配不能替代cutFinalState的组合结果。只有完整终态覆盖才能解除操作缺口；独立圆盘面不能替代带孔主面。所有操作和子对象都必须解释；分裂、多余/缺失边面、盲切、增材、明显斜切等返回UNRESOLVED。

真实圆环78面228边全覆盖，逐特征SUFFICIENT的依据是MODEL_TOLERANCE_NOT_EXACT_BREP。旧cutFinalState在螺栓孔与切割并存时保留HOLE_CUT_INTERACTIONS_UNPROVEN诊断；r2b.8完整materialFinalState匹配后解除整件对应缺口。支持的纯切割圆/槽及最终外轮廓可对账NC，明确冲突保留；其他NC组合未覆盖时报BOOLEAN_FINAL_NC_MAPPING_REQUIRED。实现/验证见项目docs/design/2026-10-19-flat-plate-cut-final-state.md、docs/verification/2026-10-19-cut-final-state.md。

## r2b.8 完整材料与工程口径

`materialFinalState`在孔槽与Boolean组合后给出最终外环/内环及逐源映射；`finishedHoleCandidates.status=MATCHED`时应消费其最终结果，不能再按sourceCount当孔数。`finalHoleSurfaceObservation=MATCHED`表示完整含孔壳实读匹配；`NOT_EXPOSED_BY_PART_SOLID`表示完整Boolean壳已核验，孔作用采用BOLT-THROUGH-01或仅开孔体证据，二者不能混称。STUD-NO-HOLE-01不产生材料移除。

当前新选择的两块T3-PX-258各8Φ30；P-116为2Φ26、P-4233为6槽+2Φ30、PR-15为2Φ22+1Φ40、圆环为1内孔。均已由原生事实直接生成要求；数字仅为验收样例，代码不按编号分支。

## 已接通原生图流水线

2026-10-20起`draw-native-parts`自动新采上述原始证据并生成要求，随后布局、写图、保存重开和独立SDK/DXF验收；最后再采原始证据对比模型/零件字段。默认不采NC。详见[执行入口](expression-engine.md)。来源通过和图纸通过仍为不同状态；图纸自定义曲线只作表达，不反过来充当成品孔作用证明。
