# STATUS

更新时间：2026-10-27

## 当前阶段

目录 `I:\zijinhua-part-drawing`。原生零件图从 `I:\zijinhuatekla` 试验分支 `exp/tekla-native-part-drawing` 提交 `1f9f3a2` 复制而来。识别、配送和 BOX 工序图留在原仓库。

## 当前能力与验证

- 六层链在本仓库：Tekla API 读取、通用几何事实、几何类型、专用核验、加工表达、布局与写图。正式入口 `draw-native-parts`。平直板这条线在原仓库已经接通；折弯板、曲面板、折梁还没有出图路线。
- 模板出图优先 1:1。放不下时按 1、2、2.5、5、10、20、50 取最小可用比例。原仓库已保存证据重放 T2-10B-584 为 1:2。自有图 `[T2-10B-.584 - 1]` / `453663074` 仍是 PREPARED，标注未写。
- 所有权登记留在本机 `I:\zijinhuatekla\outputs\m4-implementation-20261023\t1-ownership.json`，不入库。更新已有自有图时用 `--ownership` 指向它。
- 复制后的测试结果见 [拆分记录](docs/verification/2026-10-27-project-split.md)。

## 下一步

1. 用本仓库编译的写图器，把 T2-10B-584 的标注写入已准备的自有图。
2. 在本仓库做折弯板、曲面板、折梁的专用核验和出图。
3. T2-10B-304 的工程可读性仍待复核；P6 和中文字形见 TASKLIST。
