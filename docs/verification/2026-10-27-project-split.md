# 2026-10-27 零件图拆成独立仓库

原生零件图从 `I:\zijinhuatekla` 试验分支 `exp/tekla-native-part-drawing` 提交 `1f9f3a2` 复制到 `I:\zijinhua-part-drawing`。没有改 Tekla 模型，没有写图纸。

带走 `part_drawing`、两个 Tekla 2017 工具、平板技能、这条链的 36 个测试文件和夹具，以及仍在生效的设计文档。识别、配送、BOX 工序图、根目录图纸和所有权登记没有带走。

复制后在新仓库运行 `python -m unittest discover -s tests`，403 项，53.337 秒，通过。导入来自新仓库的 `src`。
