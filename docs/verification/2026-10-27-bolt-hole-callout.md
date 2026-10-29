# 螺栓孔标注

日期：2026-10-27

## 失败案例

打开的车间图是零件图 `[T2-10B-.305]`，标题「按比例1导出」。所选实例 449588104 与图上实例 449581429 是同编号的不同对象。旧出图把这组 6 个 M24 槽孔停在「缺逐板孔形」。Hole2 为假，Hole1/Hole3 为真；这些标志不决定圆孔还是槽孔。

同形夹具是 `tests/fixtures/native_sections/plate_with_shared_slots.json`（T2-10B-304）。

## 期望

- 写法 `n*D宽度(总长x宽度)`，星号、小写 x。宽度 = BoltSize + Tolerance。总长 = 宽度 + SlottedHoleX + SlottedHoleY。增量为 0 时总长等于宽度。
- 这张车间图的文字是 `6*D26(56x26)`：M24，公差 2，槽增量 30。
- 螺栓符号可见。引线箭头在沿板长向最外侧孔的孔心。等距分开标，不写 `n@`。
- 不是螺栓组的切割孔仍用 `nΦ` / `n×D`。
- 图上的 55、80、82、217 是该图实例的实测，不写进规则。

## 证据字段

`BoltSize`、`Tolerance`、`SlottedHoleX`、`SlottedHoleY`、孔位。已提供且对不上的 NC 仍阻断。

## 回归

- `tests.test_native_sections.SectionGeometryTests.test_shared_bolt_slots_use_group_parameters_and_shop_callout`
- `tests.test_native_display_p6.BoltSymbolTests.test_round_bolt_hole_uses_the_slot_form_with_zero_extension`
- `tests.test_native_material.NativeMaterialTests.test_multitarget_slot_uses_the_bolt_groups_own_parameters`

## 现场

2026-10-27，目录 `outputs/native-drawing/20261027-305-pass`。零件 449588104，自有图 `[T2-10B-.305 - 1]`，objectId `453680953`，`created` 为 false。`result.json` 为 PASS：回读 PASS，主图和剖面视觉 PASS，剖面 `bevel:449588113`，`sourceRecheck` 为 UNCHANGED。`native/batch.json` 里 `originalDrawingRestored` 和 `workPlaneRestored` 都是 true。比例 1，主视图高 546，文字 `6*D26(56x26)`。所有权阶段 SAVED。

同日更早的 `20261027-305` 是布局阻断后的空页。`20261027-305-fit` 已保存，但审计失败：DXF 没有槽孔圆弧，剖面字母「A」出了当时的预留框。这两处已分别按折线轮廓计量，并把字母框向零件侧放到两倍字高。不要复用这三个输出目录。
