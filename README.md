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

独立布局新路线已开始阶段0实施，另有[离线工具入口](src/zijinhua_tekla/part_drawing/independent_layout/README.md)，用于来源冻结、尺寸与语义覆盖适配、模板采样解析和最小几何验证。尚未提供新布局求解或现场写图能力，`draw-native-parts` 仍运行原实现。

独立内口扩展已支持经完整实体证明的轴向矩形/圆角矩形加工表达；54真实样例离线来源/262条语义责任通过。新增输入准备检查继续保留模板/原生能力门，尚未生成新图。见[实施验证](docs/verification/2026-10-31-independent-cutout-implementation.md)。
