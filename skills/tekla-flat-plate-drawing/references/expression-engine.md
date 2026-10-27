# 可执行加工表达引擎

在 Zijinhua Tekla 项目中已实现 `flat-plate-expression.r1`，不调用大模型。代码随项目维护，不复制到 skill。定位用户当前 checkout 或已安装的 zijinhua_tekla 包，核对实际入口可用；默认工作目录曾为 `I:\zijinhuatekla`，不要据此假定其他机器也存在。

## 调用

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m zijinhua_tekla.cli plan-expressions --snapshot path/to/part.json --context path/to/context.json --out outputs/requirements.json
python -m zijinhua_tekla.cli plan-expressions --snapshot path/to/part.json --context path/to/context.json --observations path/to/observations.json --out outputs/expression-audit.json
```

输入是现有 PartDrawingSnapshot.v1/v2，不是忽略目录的 batch-plan.json 或原始 Tekla 探针日志。先用可靠适配器转换数据并保留来源；不要硬套样例坐标。完整契约位于项目 `docs/design/2026-10-17-expression-engine-contract.md`，实现方案位于 `docs/design/2026-10-17-manufacturing-expression-engine.md`。找不到源码/契约时报告当前不能运行，不编造工具成功。

新[原生路线](native-reader.md)使用`plan-native-expressions --evidence <teklaNativeEvidence.v1> --out <新JSON>`，或`--capture <teklaNcCapture.v1> --nc-tolerance-mm 0.01 --max-resolution-mm 0.01`。它重新审计原始输入，不接受外部宣称通过的审计文件，不转换旧snapshot或伪造VERIFIED。输出nativeExpressionBatch.v1，parts中逐件expressionRequirements.v1可直接交给既有audit_expression_coverage；退出码0全READY、1存在BLOCKED、2非法输入/拒绝覆盖。可选--specifications只提供工艺要求，不能覆盖证据门。

`manufacturingContext.v1` 需要绑定 modelIdentifier、partId、partPosition 和由 `snapshot_digest(snapshot)` 计算的摘要。checks 中 final_contour、all_holes、uniform_thickness、all_operations 都要有 VERIFIED 状态和证据引用。没有真实检查证据不能填写 VERIFIED；省略 context 可以先得到候选要求和源摘要，但会 BLOCKED。

板件级规范可在 specifications 指定 cutting_method、hole_method、tolerance、edge_finish，每项含明确 value 和 evidence；requiredSpecifications 指定必须补齐的项。未指定的规范标 NOT_SPECIFIED，不由几何推断。当前不支持逐孔/局部面不同工艺，不能用全板默认值替代。

## 使用结果

- requirements.status=READY 仅表示本层要求可生成；BLOCKED 列证据或不支持特征，不能删除失败项后直接出完整图。
- audit.status=PENDING 表示尚无观测；PLAN_COMPLETE 表示规划表达覆盖完整；PASS 仅表示所提交 READBACK 观测的表达覆盖通过。
- 退出码 0 可能只是 READY/PENDING，1 表示审计失败，2 表示输入/证据阻断。总是读取 JSON，不只看退出码。
- 独立检查中的 visualStatus=PENDING、persistenceStatus=NOT_ASSESSED、drawingStatus=NOT_ASSESSED 不随表达 PASS 自动变成合格。

PLAN/READBACK 的 objects 要提供真实坐标、实测值、显示值、孔组成员、孔形、中心线和弧段等具类型的数据，不接受 covered=true 作为完成证据。维度链可共享相同源坐标；只有孔间距而无真实基准的孤立孔排不能满足定位。

## 当前边界

旧snapshot入口支持完整直线/可靠圆弧外轮廓和显式ROUND/SLOT贯通孔；未知孔型、重复孔源、未解析曲线、未映射innerLoops、cuts/operations、坡口等阻断。新原生入口先统一消解孔槽与切割，STUD作为无孔表面附件不阻断；非圆/非槽内环按每段真实线弧及端点定位生成要求，不猜成规则孔。v2 主面配对器当前只解析直边，带弧主面可能阻断；不能把原始证据清空或降级契约绕过。复杂平面轮廓可列要求，但复杂长板仍未形成已认可自动布局规则。

新原生适配已保留源摘要、模型身份、规则版本、材料证据和sourceMappings；原生写图/读回与图面避让已接入下述独立流水线。模型核验预算默认0.1 mm；原生共享坐标按不累积的代表坐标分桶，实际导出边界核验不超过来源预算；锚点计算仍核对1e-6，显示1位小数与原始数值分离。旧snapshot入口仍沿用1e-6默认计算预算。仅消除1e-6内浮点计算噪声导致的同规格分组/孔排拆分，不把模型预算当制造公差。合成夹具不能冒充现场采集。

## 通用原生写图入口（2026-10-20）

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m zijinhua_tekla.cli draw-native-parts --out outputs/native-drawing/new-batch
```

省略`--ids`读取当前选择；显式`--ids`只用于用户已授权的ID。`--out`须新目录，已有证据不覆盖；`--ownership`默认outputs/native-drawing-ownership.json，重跑须沿用。Windows、Tekla 2017当前模型和项目依赖须可用；入口自动编译SDK读取/写图程序。0全PASS、1含FAIL/BLOCKED、2输入/执行错误，必须查看result.json而不只看退出码。

顺序：新采原始证据→重审几何/材料→要求→布局/PLAN→原生写图→保存关闭重开→SDK实际对象读回+Tekla实际DXF→表达/图面门→源重新采集比对。输出before/after.json、expressions.json、job.json、native下JSON/DXF/PNG、audit-<id>.json和result.json。每批次前后来源必须一致；禁止把job里的期望值填成READBACK。

只有名字NATIVE-PLATE-R1、目标ID/GUID及登记页号一致的图可更新，发现人工标注/锁定/冻结/下发停止。圆弧/Boolean使用原生解析曲线并保留隐藏Part关联；模型改变须重建。短间距分层、近侧引出点、板内编号、中心十字线均由算法计算。必需文字无空位即BLOCKED，当前没有自动重排重试。

图面门检查实际字形/SDK文字框、文字重叠、穿字线和纸面范围；不覆盖任意模板、全部线线交叉和工程审签。预览中文缺字须单列，默认模板未确认，不把PASS当生产放行。具体契约/边界见项目tools/tekla2017_native_drawing/README.md、DESIGN.md及docs/verification/2026-10-20-native-layout-write-readback.md。

规划R2（native-layout.r2）增加轮廓特征链、R关系推导和附近真实孔基准，替代每个坐标统一拉线；局部肩宽、缩短界线及板内孔说明均由算法选择。`suppressWitnesses`须从SDK保存重开读到extension=Yes，并核对自有Line端点/样式和DXF可见线。新图面门从实际对象检测LEADER_CROSSES_DIMENSION，不把无压字等同于无交叉。P-116同页13→9组、421项测试及七件PLAN回放记录见项目docs/verification/2026-10-20-native-planning-r2.md；其他六件未冒充本轮现场重画。

历史native-layout.r2.1补单排孔真实两端边距、板身宽外层和R旁直接直边；等距组合仅显示两端界线，全部孔心锚点及段数仍独立读回。SDK读回增加sourceBolts（modelId/hidden/color/representation/hole/axis）与objectInventory；必须确认源Bolt隐藏、Invisible/Symbol且孔/轴符号关闭，旧hidden+Solid不可通过。该阶段现场及426项测试见项目docs/verification/2026-10-20-native-review-r21.md，旧读回缺字段不能伪造补齐。

后续native-layout.r2.2取消R旁直边的12纸面mm省略门槛。短局部区间存在唯一共享端点的真实相切直边时，以真实两端直接尺寸替换综合投影；P-116的18改为8是几何规则应用，不是固定编号特例。轮廓与R、整体定位仍由PLAN/READBACK独立核验。

native-layout.r2.3将直接替换应用到长短所有轮廓区间，追加实际相邻圆弧投影证据；不连续区间拆成独立尺寸集，以columnGroup绑定同列布局，整组避让而不跨间隔补线。孔链优先、板身及总体在外。READBACK核对实际Distance及Up与计划（距离使用模型核验预算、方向按数值精度核对），防止保存后列位置漂移。P-116不再保留120，110与66/30同列且在R10处断开。
