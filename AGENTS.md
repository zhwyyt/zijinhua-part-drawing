# 零件图项目

本仓库只做 Tekla 2017 原生零件图。识别、配送和 BOX 工序图留在 `I:\zijinhuatekla`。

## 工作约定

- `STATUS.md` 写当前阶段、已验证的事实和下一步，下一步不超过三条。
- `TASKLIST.md` 只保留还没做的事。
- 业务设计放 `docs/design/`。验证记录放 `docs/verification/`。
- 代码在 `src/zijinhua_tekla/part_drawing/`，测试在 `tests/`。
- 不把一次性脚本当成生产逻辑。
- 不默认调用 superpowers，只有明确要求时才调用。
- 根目录的图纸、表格、`cache/`、`outputs/` 是本地资料，不入库。
- 所有权登记留在本机 `I:\zijinhuatekla\outputs\m4-implementation-20261023\t1-ownership.json`。更新已有自有图时必须用 `--ownership` 指向它。

## 出图边界

- 正式入口是 `draw-native-parts`。省略 `--ids` 时读取当前选择。`--out` 必须是新目录。
- 只更新名称为 `NATIVE-PLATE-R1` 且登记在所有权文件里的图。不改车间图、模型和工作平面。
- 当前接通的是平直板。折弯板、曲面板、折梁在本仓库继续，但现在还没有出图路线。
- 1:1 放不下时按 1、2、2.5、5、10、20、50 取最小能放下的比例。50 仍放不下则阻断，不换图幅。
- 禁止按单个构件号、零件号或项目名写死规则。

## 失败归因

每个失败先归到数据问题、特征缺失、评分问题或新类型。数据问题进质量门，不靠分类规则硬凑。缺特征先补读取或事实，再写判断。评分问题要留下回归案例。新类型要补定义、样例和测试。

规则变更同时留下失败案例、期望口径、证据字段和回归测试。

## Git

- 提交标题用 `<type>: <summary>`。
- 不执行 `git reset --hard`、`git clean -fd`、`git push --force`，除非明确要求。
- 改代码后运行相关测试。发布前运行 `python -m unittest discover -s tests`，并确认导入来自当前 `src`。
