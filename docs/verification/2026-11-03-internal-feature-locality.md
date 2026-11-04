# 内部独立特征孔旁优先验证

## 变更依据与范围

用户要求孔旁标注是第一优先级，并推广到矩形、带倒角矩形等内部孔。原实现仅识别 `localAllowed && 单点band`，属于共同规划规则缺口；失败依据是102首张新页中间孔定位远置。设计见[共同规则](../design/2026-11-03-internal-feature-locality.md)。

新增 `feature_locality.py`，按真实孔中心和内闭合轮廓身份绑定独立尺寸。r4使用同一绑定生成局部候选和检查位置；远端不进入独立特征候选池，普通成本仅比较局部方案。实际回读通过已核验的原生方向和Distance重建位置，不能只信计划placement。没有修改测量锚点/值，也没有向历史表达封装增加字段。

## 已执行检查

- `python -m unittest discover -s tests -p 'test_independent_feature*.py'`：18项通过，8.611秒。新增8项共同策略测试，原10项实采特征测试继续通过。
- 圆孔、长圆孔、矩形、倒角矩形在禁用localAllowed时仍绑定局部义务；仅生成孔旁候选，远端检查拒绝。平移/镜像保持归属和局部性，输入事实/锚点不变。
- 已有真实形状证明流程生成的锐角/圆角矩形表达：大小和两向定位均绑定自身内口。两个同形内口分别归属；错误内口/外轮廓绑定拒绝。
- 多孔共用投影站点通过完整band识别为多孔链，不误当独立单孔；overall不抢占局部责任。
- 即使远端普通代价更低，也不进入局部候选求解；空局部池为INFEASIBLE，不扩张成“全域无解”结论或自动远置。
- 102既有首张远端页重放拒绝，修正页回读通过局部门；将旧页placement元数据换成修正页的位置仍拒绝。225既有原生曲线/说明/尺寸回读继续通过。
- 未支持类型的门保持：r4拒绝矩形/圆角矩形整页路线；倒角矩形加工语义仍为UPSTREAM_CONTRACT_GAP。
- 全量 `python -m unittest discover -s tests`：**671项通过，69.359秒**。日志：`outputs/independent-existing-batch-20261103/full-tests-feature-locality.log`。使用既有隔离依赖和仓库输出临时目录；Python模块导入路径实测为当前 `I:/zijinhua-part-drawing/src/.../feature_locality.py`。

首次针对性测试的求解器夹具使用空LineString，Shapely距离为NaN导致候选全部被过滤；已将测试夹具改为远处真实线段，生产逻辑未为测试作豁免。

## 边界与交接

本次无Tekla调用、无新图、无模型/台账修改，回读测试使用此前实际保存的数据。策略/候选通过不代表矩形或倒角内口已完成原生整页出图；既有短尺寸穿字、材料表避让和模板缺字继续保留。

变更分析脚本在本机仍错误报告0变更，未以其passed作为依据；实际Git diff和新增文件已人工核查。README、DESIGN、主方案、STATUS、TASKLIST及CHANGELOG同步，沿用仓库现有状态体系。
