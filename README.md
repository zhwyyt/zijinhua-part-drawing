# 紫金华零件图

Tekla 2017 原生零件图。代码从 `I:\zijinhuatekla` 的 `exp/tekla-native-part-drawing` 提交 `1f9f3a2` 拆出。识别、配送和 BOX 工序图仍在原仓库。

当前接通的是平直板：API 读取、几何事实、类型、专用核验、加工表达、布局和写图。折弯板、曲面板、折梁以后在本仓库做，现在还不会出图。

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m zijinhua_tekla.cli draw-native-parts --ownership "I:\zijinhuatekla\outputs\m4-implementation-20261023\t1-ownership.json" --drawing-attributes "标准零件图10(按1倍导出)" --out outputs/native-drawing/new-batch
```

省略 `--ids` 时读取当前选择。`--out` 必须是新目录。`--ownership` 指向本机已有台账，才能更新已经画过的自有图，而不是另建一套。运行前请自行保存并关闭当前图纸。

1:1 放不下时，按 1、2、2.5、5、10、20、50 选用最小可用比例。属性名里的「按 1 倍导出」只决定图框。加 `--preview-overflow` 时，仍放不下也写出预览，不换图幅，审计不把这张图当成合格。

同一包里还留着离线 `draw-parts` 和 `plan-expressions`，因为表达和几何模块的既有测试还调用它们。新的零件类型不从这两个入口扩展。

读取器和写图器在 `tools/tekla2017_native_reader/`、`tools/tekla2017_native_drawing/`。规则见 [平板技能](skills/tekla-flat-plate-drawing/SKILL.md)。

独立布局新路线已开始阶段0实施，另有[独立工具入口](src/zijinhua_tekla/part_drawing/independent_layout/README.md)，用于来源冻结、尺寸与语义覆盖适配、模板研究和受控测试页验证。尚未提供新布局求解或生产写图能力，`draw-native-parts` 仍运行原实现。

独立内口扩展已支持经完整实体证明的轴向矩形/圆角矩形加工表达；54真实样例离线来源/262条语义责任通过。新增输入准备检查继续保留模板/原生能力门，尚未生成新图。见[实施验证](docs/verification/2026-10-31-independent-cutout-implementation.md)。

模板加载核验已加入独立入口。20/50原属性的SDK自动改写问题已用唯一名称的属性副本避开：10/20/50均完成两次正确加载及安装前/移除后负对照，现场358个原属性文件保持不变。见[隔离加载记录](docs/verification/2026-10-31-isolated-template-bundles.md)。

三规格已在584独立测试页上验证实际图框/材料表、16条文字试样及两次重开；新单主视图副本完成10的1:2.5、20/50的1:1固定放置和关闭缩短，视图框均在试验区域内。原生文字遮罩已按对象ID核验；官方PDF嵌入字体后仍有字体替换/中文缺字，候选现代仿宋/宋体的测试文字完整，但真实墨迹超出SDK框。见[基础控制验证](docs/verification/2026-10-31-native-foundation-controls.md)。

后续已实现独立`glyph-study`真实字形读回占用及图框单位映射、`primitive-study`短尺寸/箭头/引线核验。三规格18条字形试样、30个短尺寸、6个引线完成有限验证，全量570项通过；保留短尺寸占用相交和表格原字体缺口，仍不提供生产CalibrationProfile或新布局求解。见[本轮记录](docs/verification/2026-10-31-glyph-primitive-occupancy.md)。
