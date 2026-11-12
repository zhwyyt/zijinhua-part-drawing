# 最终实体修复样本

`profile-selection.json.gz`固定2026-11-12当前选择的BOX T2-9P-1及角钢T2-10B-189，来源outputs/final-solid-trial/20261112-profiles/selection-read.json；保留实体、孔/焊钉组和必要圆弧来源，只保留零件号及宽度报告，未包含模型路径。BH23/190复用real-parts.json.gz。型材独立预期及合成截面在test_final_solid_profiles.py。

`real-parts.json.gz`固定2026-11-11选择快照17件及2026-11-05折板快照6件。保留实体、坐标、孔组及圆弧来源，去除无关报告和模型路径。gzip仅用于减小体积。原文件分别在 `outputs/final-solid-trial/20261111-run-02/old/selected.json` 和 `outputs/part-facts/20261105-probe-01/evidence.json`。

`old-flat-expectations.json`固定旧路线10件READY平板事实及坡口，仅用于测试对照，不作为新实现输入。105完整投影及斜端剖面差异单列，不强行匹配旧结果。

折板中面展开预期来自先前独立探查 `outputs/part-facts/20261105-probe-01/bent-facts.txt`：399/180/194/402/581/409分别为12387.2、5875.6、6086.9、11984.4、4736.2、1521.2 mm，容差0.1 mm。不代表制造下料核定。另复用已有native_sections坡口/槽孔夹具；损坏和坐标变体在内存构造，不改原文件。
