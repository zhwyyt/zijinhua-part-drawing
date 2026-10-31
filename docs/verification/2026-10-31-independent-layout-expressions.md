# 独立布局阶段0续作：尺寸与语义覆盖适配

日期：2026-10-31。状态：本次语义适配已实现并验证；阶段0整体仍未通过。

## 1. 实际完成范围

根据用户“那继续吧”的指令，落实上一轮下一步中的尺寸/覆盖适配。代码新增在独立模块，不修改稳定上游或原布局。新增 `expressions.py`、`semantic_coverage.py`，独立CLI增加 `expressions` 子命令；测试新增 `tests/test_independent_expressions.py`。

稳定上游负责“标什么”：`plate_features.build_feature_model`、`dimension_scheme.build_scheme` 和 `flat-plate.v1` 配置。新适配器保留方案的点ID、侧别、角色、参考、孔带和局部许可，补原精度坐标、实际值、显示值、来源和语义责任。没有运行旧布局作为答案或兜底。

本次冻结的是 `LayoutExpressions.v1` 语义子契约，不是完整 `LayoutInput.v1`。完整布局输入还需要已验证的模板、内容来源、单位、占用、原语能力和策略数据。`LayoutCandidate.v1`、`LayoutPlan.v1` 也未冒充已经完成。

## 2. 已验证的行为

- 全部上游要求和方案组保留；每个物件有稳定ID。要求与物件是多对多关系，不错误约束为一项要求对应一个标注。
- 覆盖核验不依赖适配器构造结果：重新计算稳定上游方案并核对来源/方案/规则，随后逐物件检查实际事实；删除组及对象后重新计算摘要也不能绕过。
- 各轴通过真实尺寸链、来源基准、共享坐标及已证明轴向圆弧关系建立定位路径。共享坐标比较每簇首点，0.09+0.09不能在0.1预算下累积成0.18的相等关系。来源证据不会伪装成图元ID。
- 静态责任包括所有外/内边界线弧、圆孔/槽孔形状及中心轴/方向；孔形责任统一，Symbol2与显式几何实现留待原生能力绑定，不能重复绘制。
- 螺栓孔说明绑定长向最外侧代表孔及孔心策略；普通孔保留合法代表孔候选及近侧孔壁策略，实际布局候选必须选定孔和真实边界参数。槽孔尺寸与方向严格对照来源。
- 圆弧半径、完整圆外轮廓直径、编号、厚度和规格分别承担明确责任；完整圆不重复生成半径注记。厚度当前只是待模板内容绑定的责任，不证明标题栏已经显示正确文字。
- 同列只承接显式column，检查共同轴、允许侧交集和成员齐全。当前真实上游不生成column，默认明确为空，不按坐标接近猜同列；独立夹具验证了列成员和轴冲突。
- 组合只承接上游明确combined，检查同组孔、同列、连续等距、间隔数与文本，含螺栓孔拒绝组合。旧布局中的额外局部combineRuns预测没有被默认为新语义。
- 未覆盖要求、未知物件、重复ID/语义对象、错误尺寸/孔径/半径、坐标、编号、来源或版本均不能得到语义成功。范围外/输入阻断单独保留，不混入成功计数。

## 3. 真实回放

本地证据：`outputs/independent-layout-expressions-20261031-01/`，包含三批完整结果及 `summary.json`。每批重复适配结果完全一致，封装摘要复核通过。

| 输入封装 | 语义完整 | 范围排除 | 契约缺口/输入阻断 |
|---|---:|---:|---:|
| 阶段0 selection9 | 8 | 1（305） | 0 |
| 阶段0 20261028-101-584 | 2 | 0 | 0 |
| 阶段0 20261029-225-preview | 1 | 0 | 0 |

共12份历史实例快照、11个不同实例。584重复快照保留为不同证据，不重复计算实例。语义完整的11份快照对应10个不同实例，合计610条要求全部绑定；610不是测试数量，也不是冻结发布分母。305仍保留完整剖面要求并报告OUT_OF_SCOPE。`verifiedDrawingCount=0`。

可复现入口：

```powershell
$env:PYTHONPATH = "$PWD/src"
& outputs/independent-layout-env/Scripts/python.exe -m zijinhua_tekla.part_drawing.independent_layout expressions --sources outputs/independent-layout-stage0/selection9/report.json --out outputs/layout-expressions-new
```

输出目录必须新建。退出码1表示含契约缺口或输入阻断，2表示契约/路径等输入错误；0只说明命令完成，仍须检查范围排除及各项状态。

## 4. 反例与回归

新增24项测试覆盖：确定性/输入不变、物件与要求映射、删除尺寸、删除孔形、尺寸/坐标/显示/侧别/参考被改、静态几何、孔径、编号/厚度/孔说明、未知与重复对象、来源/单位/版本、螺栓与普通孔锚点、同列丢成员及轴冲突、附加视图、内切口缺定位、完整圆直径、共享坐标误差累积、组合等距/螺栓禁组合、槽孔方向、独立圆弧半径、平移以及删组后重算摘要。另补规格说明来源反例，发现上游specifications的Python元组不能直接进入严格JSON契约；在独立边界无损转换为数组后通过，未改上游。

最终在锁定隔离环境执行全量测试：**476项通过，44.869秒**。导入路径明确为 `I:/zijinhua-part-drawing/src/zijinhua_tekla/__init__.py`。Windows沙箱临时目录权限问题沿用既有解决方式：沙箱外运行离线测试，临时文件限本仓库outputs。

```powershell
$env:PYTHONPATH = "$PWD/src"
$env:PYTHONIOENCODING = "utf-8"
& outputs/independent-layout-env/Scripts/python.exe -c "import tempfile,unittest; tempfile.tempdir=r'I:/zijinhua-part-drawing/outputs/independent-layout-test-temp'; result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover('tests')); raise SystemExit(not result.wasSuccessful())"
```

原有src/tests/tools共125个文件SHA-256与阶段0前基线全部一致。没有执行Tekla写图、保存重开或模型写操作。

## 5. 技术缺口与剩余门

`INNER-CUTOUT-LOCATION-01`：用独立合成闭合内切口反例证明，原尺寸方案可能只覆盖外轮廓及孔，而没有内切口的两向定位。新适配保留其全部边界与要求；shape责任BOUND，缺失location责任MISSING，输出UPSTREAM_CONTRACT_GAP。该缺口是上游尺寸契约前置任务，未修改原几何/表达/尺寸方案，也未把实例从已准入集合移除。需要有来源的真实基准和完整定位方案后再设计扩展，不能在布局器中临时加规则。

其余未关闭项：模板实际加载/依赖及内容绑定、源图单位/缩短/多比例、字体/原语占用与角色净距、原生孔符号策略、真实图元覆盖、584新测试页保存重开及源噪声/恢复、完整布局三契约、案例发布分母冻结。语义核验只在已审计模型平面运行，尚未证明纸面位置、文字可读性或原生持久化。

阶段0下一优先是把语义责任接到实际模板与原语能力，补完整布局输入；任何未验证能力都不能成为候选生成器的默认假设。下一步不需要用户补算法参数。

## 6. 静态与文档检查

质量扫描通过（0错误、5项复杂度警告，其中来源/模板2项延续上轮，新增语义校验3项）；安全扫描0项发现。较长的CLI与语义入口已拆开，保留的校验条件不为降低计数而移除。模块检查通过，2项目录约定提示：代码已经在仓库src、测试统一在根tests，不另复制模块内src/tests。文档门沿用STATUS/TASKLIST豁免体系并通过；模块相对链接检查、git diff --check和旧125文件哈希核对通过。变更分析识别12项路径，其行数统计不可靠，已用git status/diff独立核对。
