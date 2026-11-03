# 原生尺寸夸大能力验证

**试验后的验收口径更新（2026-11-03）**：用户明确确认“没有关系的，这点小问题不用在意。就这样了”。当前已展示的约8%轻微箭头削边按可接受外观记录，停止修复研究，不再因该项阻断。下文保留当时测量和结论，不表示已消除削边或已实现夸大；原定为修复该外观而开展的后续视图夸大试验取消。线穿文字、严重遮蔽及数值/锚点正确性另行核验。

## 结论

本机Tekla 2017已检查的公开接口没有单独移动StraightDimension数值文字的坐标/偏移成员。另有公开Exaggeration属性，但本轮18个启用该属性的sheet空间尺寸均只保存了属性，没有拉宽实际显示。最短尺寸问题仍未解决，不能推广为“真实零件视图中也不支持”。失败归因为原语可控性证据缺失，不调整评分或按零件号补规则。

新增三页完成保存、两次重开、原生PDF和实际墨迹/笔画核验；全量595项测试通过。只改独立探针和研究适配，未接正式入口。设计见[试验设计](../design/2026-11-03-dimension-exaggeration.md)。本机完整证据目录：`outputs/independent-dimension-exaggeration-20261103/`。

## 接口证据和限定

- `public-api.json`记录本机Drawing程序集身份、SHA-256和公开成员。StraightDimension/StraightDimensionSet及继承成员、DimensionTextAttributes均没有数值文字坐标属性。
- `sdk-evidence-files.json`绑定Drawing/DrawingExtensions程序集、XML及附带宏的文件哈希。DrawingExtensions提供符号位图绘制；普通Text.MoveObjectRelative不是尺寸移动。LeftTagLineOffset/RightTagLineOffset属于尺寸标签线，未当数值偏移使用。
- 附带ExaggerateSelectedDimensions.cs使用界面回调，本轮只读源码，没有执行。未调用内部QueryOperation、写内部结构或修改全局选项。
- 实测的是sheet空间、单段尺寸。Exaggeration各数值字段的视图单位和生效条件尚未证明；属性名不能当作几何公式。真实目标视图及原锚点路线是下一项限定实验。

## 页与样本

| 模板 | 584新页 | 对象ID |
|---|---:|---:|
| 零件图10 | 26 | 557647280 |
| 零件图20 | 27 | 557653028 |
| 零件图50 | 28 | 557658724 |

每页8个尺寸、2个引线和2条字形试样。尺寸包括Inside/Outside跨度比0.5基线，Both方向Inside跨度比0.5/1/2，LeftDown/RightUp的Inside0.5，以及Both的Outside0.5。AboveDimensionLine、固定放置、仿宋字高25/50/125、实心箭头高宽10/20/50保持；数值样式仍为毫米、可选一位小数。箭头数值来源沿用[上一轮模板候选](2026-11-03-dimension-text-controls.md)，不是缩小箭头求通过。

夸大输入为NearDimensionLine、Width=4字高、Position=0.5字高、Height=1字高，尺寸距离4字高。它们是有限对照参数，不构成生产规则。`capture/*-before/reopen1/reopen2.json`保留父集与子尺寸实际属性、原测量点、文字及显示图元。

## 图面结果

1. 18个启用样本的父集、子尺寸Exaggeration属性与请求一致，原锚点/数值/字体/箭头样式保存重开稳定；实际两箭头尖端跨度仍等于原测量跨度。
2. 三规格共12组最短对照，把全部实际线、填充多边形、遮罩和文字位置减去各自起点后比较，坐标取8位小数，结果完全相同。Both/LeftDown/RightUp的Inside0.5均与Inside基线相同；Both的Outside0.5与Outside基线相同。证据：`display-comparison.json`。
3. 30个原语共54个箭头中，42个被白遮罩削去约8%面积，分布在24个尺寸中；其中启用夸大的18个尺寸有33个受损箭头。6个直/折引线箭头完整，最大浮点面积尾差2.23e-16。
4. 本组可见线/箭头没有与自身逐字墨迹包络相交。它不抵消箭头削边，也不代表全页障碍或真实字形轮廓布尔碰撞通过。
5. 1200 DPI原生PDF局部渲染确认削边和夸大未生效，见`control-comparison.png`、`crop-*.png`；4200像素整页及`whole-pages.png`确认试样位置。原表格仍有既知缺字，这些页面不是合格生产图。

`*-ink.json`为ACTUAL_INK_BOUND，`*-primitives.json`为PRIMITIVES_OBSERVED，`*-visible.json`为VISIBLE_STROKES_MEASURED，以上只表示读回/测量成立。新增`details.exaggeration.capabilityStatus=ATTRIBUTE_ONLY_NO_EXPANSION`，明确不能把PRIMITIVES_OBSERVED读成夸大生效。`displayExpanded=false`限定当前可绑定的未拉宽几何；一旦箭头离开原端点，当前适配会以PRIMITIVE_ARROW_TIP阻断，需补真实折线与锚点证明后才能支持。

## 实现与验证

新页dimension可选`exaggeration`对象，含direction/origin/width/position/height。探针连接前校验枚举、有限正数宽高及非负位置；引线拒绝此控制。省略字段保持旧行为。离线同时核对父集/子尺寸属性，几何和文字仍从原生显示及PDF绑定，不从请求生成“移动成功”。

- 新增3项Python回归：属性匹配不能冒充显示拉宽、非法字段/数值、引线误带尺寸控制；相关16项通过。
- C#编译通过、无编译警告；最终可执行文件按登记ID的只读检查通过。原现场创建使用`bin/`版本，`bin-final/`补充拒绝遗漏position，未重写已创建页。
- `probe-validation.json`记录最终C#探针对非法方向、负位置和缺位置的拒绝，均在创建输出和连接前失败。验证脚本首次因本机默认GBK解码stderr失败，明确改为UTF-8后重跑通过；不是现场创建失败。
- 历史修正组、文字控制组、模板数值组共9份原语报告重新运行，与原报告包括digest逐项相同，见`legacy-replay.json`。
- `python -m unittest discover -s tests`：595项通过，63.065秒，退出码0，见`full-tests.log`；导入明确来自本仓库`src`。现有Shapely参数弃用警告保留，没有跳过测试。
- 质量扫描0错误、50条警告，数量与上一轮一致；未顺手重构既有长函数。`git diff --check`通过。文档脚本返回沿用既有状态系统的豁免，不视为内容审核；已人工核对设计、状态、任务和验证记录。变更分析脚本返回空变更，与实际Git状态矛盾，未把该结果当审核通过，改用实际diff/status及原文件哈希核对范围。

## 现场保护和后续

`preservation.json`核对：原125文件哈希无变化；442份属性全目录与字节一致；原台账字段和此前24条试验登记精确保留；现27张测试页，既有84份资产复用。fresh前后来源均SOURCE_READY，完整rawPart精确相同。创建/PDF/最终按ID检查的模型身份、工作平面及活动状态保持，结束无活动图。没有覆盖305/584验收页、101人工标注或旧试验页，没有枚举全目录。

下一步应把限定对照移至真实目标视图，先证明视图坐标/单位、实际测量锚点和显示路径，再核实原生夸大的生效条件；不能继续在sheet试样上换宽高值推断生产方案，也不能因本轮未生效就静默换成普通文字叠加。短尺寸能力门仍阻断。
