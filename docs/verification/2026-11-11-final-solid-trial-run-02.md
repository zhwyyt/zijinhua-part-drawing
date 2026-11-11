# 最终实体双路线运行记录

日期：2026-11-11。运行目录：`outputs/final-solid-trial/20261111-run-02/`。

## 输入和边界

Tekla 2017 当前选择由旧原生读取器采集，输入为 `old/selected.json`。读取到 17 个零件，模型连接成功，`readStatus=COMPLETE`，读取错误数为 0，工作平面前后保持。此次只做读取、几何事实和加工表达审计，没有创建、修改或打开图纸。

同一份快照分别进入旧链和新增的独立 `final_solid_trial` 链。新链不调用 Tekla、不导入旧几何/表达函数，也不回退旧链结果。

## 结果

旧链输出在 `old/`：几何审计 `unresolvedCount=7`，加工表达 `READY=10、BLOCKED=7`。

新链输出在 `new/`：17 件都能从 `finalSolid` 得到面/边界/厚度等基础事实；加工表达 `READY=6、BLOCKED=11`。新链 READY 的零件是 `T2-10B-182`、`T2-10B-175`、`T2-10B-174`、`T2-10MQ-20`、`T2-10B-592` 和 `T2-10B-105`。逐件对照见 [`comparison.md`](../../outputs/final-solid-trial/20261111-run-02/comparison.md)。

新链把螺栓组当补充证据。若最终实体中没有独立可识别的孔环，就报告 `HOLE_FINAL_STATE_UNRESOLVED`，不把螺栓参数直接冒充成成品孔。折板报告了最终实体事实，但因表达适配尚未实现而报告 `FOLDED_EXPRESSION_ROUTE_UNIMPLEMENTED`；梁/型材表达报告 `TYPE_OUT_OF_SCOPE`。

## 验证和限制

新增独立测试 3 项全部通过。全量 `python -m unittest discover -s tests` 共 682 项，其中 45 项因现有环境缺少 `pdfminer` 等依赖或既有测试契约错误失败；这些失败集中在原有独立图面字形/可见笔画测试，不是本次新包测试。新链没有接入正式 `draw-native-parts`，也没有声明生产放行。

下一步应补齐最终实体孔/槽终态识别、折板段/折角表达适配，并在依赖完整的环境重跑全量回归；在此之前保留旧链作为正式提供者。
