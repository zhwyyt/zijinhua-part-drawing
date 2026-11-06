# 紫金华零件图

Tekla 2017 原生零件图。代码从 `I:\zijinhuatekla` 的 `exp/tekla-native-part-drawing` 提交 `1f9f3a2` 拆出。识别、配送和 BOX 工序图仍在原仓库。

当前接通的是平直板：API 读取、几何事实、类型、专用核验、加工表达、布局和写图。折弯板、曲面板、折梁以后在本仓库做，现在还不会出图。

2026-11-04工作方向调整：以Claude意图说明的“两层指标 → 人工/自动图对比 → 结构化候选 → 目标优化与原生闭环”为主线，前置跨类型几何事实与原设计中的加工表达扩展。计划收集50份人工图纸及对应模型；加工表达由项目整理，无需另备加工说明文件。当前[阶段主规划](docs/design/2026-11-04-facts-and-two-layer-quality.md)已整合，每阶段提交结果后再确认下一步，暂缓逐张出图推进；本轮仅改规划，新能力尚未实现。

2026-11-05：阶段0改为用户在Tekla中选件、逐件解析几何事实、零件特征和加工表达，按折板 → 直型材 → 折梁 → 曲面板逐类推进；每件交付解析卡和简图，放在`outputs/part-facts/`。

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m zijinhua_tekla.cli draw-native-parts --ownership "I:\zijinhuatekla\outputs\m4-implementation-20261023\t1-ownership.json" --drawing-attributes "标准零件图10(按1倍导出)" --out outputs/native-drawing/new-batch
```

省略 `--ids` 时读取当前选择。`--out` 必须是新目录。`--ownership` 指向本机已有台账，才能更新已经画过的自有图，而不是另建一套。运行前请自行保存并关闭当前图纸。

1:1 放不下时，按 1、2、2.5、5、10、20、50 选用最小可用比例。属性名里的「按 1 倍导出」只决定图框。加 `--preview-overflow` 时，仍放不下也写出预览，不换图幅，审计不把这张图当成合格。

同一包里还留着离线 `draw-parts` 和 `plan-expressions`，因为表达和几何模块的既有测试还调用它们。新的零件类型不从这两个入口扩展。

读取器和写图器在 `tools/tekla2017_native_reader/`、`tools/tekla2017_native_drawing/`。规则见 [平板技能](skills/tekla-flat-plate-drawing/SKILL.md)。

独立布局新路线已有首张完整原生预览：584 第37页按新候选和 CP-SAT 联合选择六组尺寸及编号，采用零件图10、1:2，完成保存重开及实际图面核验。用户已认可这张布局。已扩展到孔槽、圆弧及R说明的实际新页验证，标题栏仍有字体缺字；尚未接生产入口，`draw-native-parts` 仍运行原实现。见[首张完整图记录](docs/verification/2026-11-03-first-complete-drawing.md)及[独立工具入口](src/zijinhua_tekla/part_drawing/independent_layout/README.md)。

独立内口扩展已支持经完整实体证明的轴向矩形/圆角矩形加工表达；54真实样例离线来源/262条语义责任通过。新增输入准备检查继续保留模板/原生能力门，尚未生成新图。见[实施验证](docs/verification/2026-10-31-independent-cutout-implementation.md)。

原有11件清单已完成本轮验证：584/9P-15沿用已有页，305因剖面排除，剩余8件均完成原生新页与PDF。225、618主图文字清晰；其余6件因表格/说明区避让或短尺寸穿字等问题未通过。用户指出102中间孔远置后，修正页已将三条竖向定位链放回各孔旁，端部短尺寸仍待修复。没有按零件号写特例，正式入口尚未切换。见[批次图面及验证记录](docs/verification/2026-11-03-existing-parts-batch.md)。

内部独立特征孔旁标注已提升为共同规划优先规则，覆盖圆孔、长圆孔以及矩形/倒角等内闭合轮廓的归属，不再靠普通评分偏好。矩形等内口目前只完成共同策略/候选验证，原生整页路线仍待接通；放不下不自动拉到远端。见[规则边界](docs/design/2026-11-03-internal-feature-locality.md)。

模板加载核验已加入独立入口。20/50原属性的SDK自动改写问题已用唯一名称的属性副本避开：10/20/50均完成两次正确加载及安装前/移除后负对照，现场358个原属性文件保持不变。见[隔离加载记录](docs/verification/2026-10-31-isolated-template-bundles.md)。

三规格已在584独立测试页上验证实际图框/材料表、16条文字试样及两次重开；新单主视图副本完成10的1:2.5、20/50的1:1固定放置和关闭缩短，视图框均在试验区域内。原生文字遮罩已按对象ID核验；官方PDF嵌入字体后仍有字体替换/中文缺字，候选现代仿宋/宋体的测试文字完整，但真实墨迹超出SDK框。见[基础控制验证](docs/verification/2026-10-31-native-foundation-controls.md)。

后续已实现独立`glyph-study`真实字形读回占用及图框单位映射、`primitive-study`短尺寸/箭头/引线核验。三规格18条字形试样、30个短尺寸、6个引线完成有限验证，全量570项通过；保留短尺寸占用相交和表格原字体缺口，仍不提供生产CalibrationProfile或新布局求解。见[本轮记录](docs/verification/2026-10-31-glyph-primitive-occupancy.md)。

新增`visible-stroke-study`离线核对裁剪、白遮罩和实际笔宽。36个原语的可见线/箭头未碰逐字墨迹包络，但20个箭头被遮罩削边，短尺寸布局仍未放行；全量587项通过，本轮未操作现场图纸。见[可见笔画记录](docs/verification/2026-11-03-visible-stroke-clearance.md)。

后续已完成两组新页文字控制/模板箭头尺寸候选对照。确认On与搜索间距不是通用修复；列明的部分Outside及直/折引线样例完整，最短尺寸仍削边，独立布局未放行。全量592项通过，既有页/属性/模型来源保持。见[文字控制记录](docs/verification/2026-11-03-dimension-text-controls.md)。

公开尺寸夸大接口已完成新页对照：18个sheet尺寸属性保存但显示未拉宽，本组42个箭头仍削边约8%；报告明确区分属性与显示能力，真实目标视图待验证。全量595项通过，既有保护项保持，见[夸大记录](docs/verification/2026-11-03-dimension-exaggeration.md)。

2026-11-03用户已接受当前短尺寸箭头轻微削边，本项外观不再阻断，也不继续文字移位/夸大修复。以上历史测量保留；该确认不代表独立布局其他能力门已经通过。

真实584目标视图已完成10/20/50两组比例的坐标、实际轮廓锚点及保存重开验证；新增`primitive-study --source`，全量600项测试通过。1:5反例仍有严重遮箭头和引线碰字，尚不放行生产布局。见[真实视图验证](docs/verification/2026-11-03-model-view-primitives.md)。

新增离线`page-obstacle-study`，核验全页文字、遮罩、线和箭头，生成保持真实锚点的有限引线候选及具体冲突。六页432个候选中33个避开已知障碍，但尚无完整组合，仍需尺寸组、编号共同参与布局及原生回读；全量619项测试通过。本轮未调用Tekla，见[障碍与候选验证](docs/verification/2026-11-03-page-obstacles-candidates.md)。

后续新增`joint-candidate-study`，实测尺寸移层、编号平移和引线参与有限联合选择；尺寸锚点及界线近端保持。六页606个候选中97个避开已知障碍，仍需适配其余模板尺寸才能继续共同避让；未宣告整页合格。全量640项测试通过，本轮未操作Tekla，见[联合候选验证](docs/verification/2026-11-03-joint-measured-candidates.md)。


2026-11-03六件修复：新增实际模板避让、拥挤链无损拆段/同侧约束和原生短线延长占用。六件主图已测文字清晰，111/101/102/451/73同时通过模板避让；450材料表自身仍超框，紧凑TPL试验不能按页持久化，原模板已恢复。全量678项测试通过，未接正式入口；实际图集及限制见[修复验证](docs/verification/2026-11-03-six-failure-repairs.md)。


2026-11-04更正：用户否决101/102拆散尺寸链的方案，撤销这两件通过结论。自动拆链代码已删除，完整链成为规划/回读硬约束；678项回归通过。用户关闭活动图后，两件第5页已按完整链重新生成实际图片，原生回读及模板避让通过；仍有80/360文字碰线，未宣布全部修复通过。见[撤销与恢复记录](docs/verification/2026-11-04-dimension-chain-restoration.md)。
