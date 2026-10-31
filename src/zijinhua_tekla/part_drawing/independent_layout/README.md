# independent_layout

独立布局系统的阶段 0 离线工具。重新审计原生输入、适配尺寸方案并核验语义覆盖、整理三种模板采样及验证明确给定的几何占用，供后续新求解器使用。当前没有布局求解、Tekla 写图或正式入口接线能力。

## 核心职责

| 命令 | 输入 | 输出及限制 |
|---|---|---|
| `sources` | `teklaNativeEvidence.v1` | 冻结原始实例、重新计算的几何和完整表达要求；附加视图保留并报告 `OUT_OF_SCOPE` |
| `expressions` | `NativeLayoutSources.v1` | 承接尺寸方案，生成物件/要求多对多映射；只证明语义责任完整，不证明图面可见 |
| `template-study` | 只读采样目录及可选 `.wd` | 实测图幅、角色样式、字段差异、未完成能力；始终为 `STUDY_ONLY` |
| `check-observation` | `LayoutObservation.v1` | 检查线宽、多边形、边界、相交和最小净距；只返回几何结论 |
| `environment` | 当前 Python 环境 | 固定 OR-Tools 版本，10 次单线程确定性小模型冒烟检查 |

`SOURCE_READY` 和 `SEMANTICS_COMPLETE` 均不是完整 `LayoutInput`；`GEOMETRY_CLEAR` 不是 `PLAN_VALID` 或 `VERIFIED`。实际文字、箭头、符号的占用必须在后续标定与原生适配中提供，本模块不按字体名称猜测。

## 依赖与安装

阶段 0 已验证平台为 Windows x64、Python 3.12.9。完整环境版本记录在 `requirements-stage0.lock`，包含父包初始化所需的项目依赖及全量测试所需的 pypdf。安装到单独虚拟环境，勿覆盖现有运行环境。

```powershell
python -m venv outputs/independent-layout-env
& outputs/independent-layout-env/Scripts/python.exe -m pip install -r src/zijinhua_tekla/part_drawing/independent_layout/requirements-stage0.lock
$env:PYTHONPATH = "$PWD/src"
& outputs/independent-layout-env/Scripts/python.exe -m zijinhua_tekla.part_drawing.independent_layout environment --out outputs/layout-environment-new
```

如当前 Windows 沙箱不能完成 venv 的 ensurepip，可用基础 Python 的 `pip --python <虚拟环境python路径>` 安装同一锁定文件。验证导入路径必须落在本仓库 `src`，不能误用其他 checkout 的 editable 安装。

## 使用示例

在仓库根执行；所有 `--out` 必须是新目录。

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m zijinhua_tekla.part_drawing.independent_layout sources --evidence outputs/native-drawing/20261029-selection-images/before.json --out outputs/layout-sources-new
python -m zijinhua_tekla.part_drawing.independent_layout expressions --sources outputs/layout-sources-new/report.json --out outputs/layout-expressions-new
python -m zijinhua_tekla.part_drawing.independent_layout template-study --study outputs/layout-study-20261031-template50-01 --attributes "outputs/layout-study-20261031-template50-01/attribute-snapshots/标准零件图50(按1倍导出).wd" --out outputs/layout-template50-new
python -m zijinhua_tekla.part_drawing.independent_layout check-observation --observation observation.json --out outputs/layout-check-new
```

每次输出 `report.json`。退出码 0 只表示离线命令完成，包括 `STUDY_ONLY` 和仅有范围排除的来源批次；必须检查报告状态、范围与待完成门。退出码 1 表示几何冲突、`UPSTREAM_CONTRACT_GAP` 或存在 `INPUT_BLOCKED`；2 表示输入、依赖、输出路径等错误。已有输出不会被覆盖。

## 测试

测试位于仓库 `tests/test_independent_layout.py` 和 `tests/test_independent_expressions.py`，沿用项目根测试目录。使用同一 `PYTHONPATH` 运行：

```powershell
& outputs/independent-layout-env/Scripts/python.exe -m unittest discover -s tests -p test_independent_layout.py
& outputs/independent-layout-env/Scripts/python.exe -m unittest discover -s tests
```

沙箱拒绝临时目录访问时，应修正测试运行权限或设置可写测试临时目录，不跳过测试。

## 相关文档

- [模块设计](DESIGN.md)
- [总体设计与阶段计划](../../../../docs/design/2026-10-30-independent-layout-system.md)
- [阶段 0 实施记录](../../../../docs/verification/2026-10-31-independent-layout-stage0.md)

语义续作的范围、回放与缺口见 [尺寸与覆盖适配记录](../../../../docs/verification/2026-10-31-independent-layout-expressions.md)。`expressions` 的范围排除会单独列出，不能把退出码0理解为每个实例均通过；正式图面验收仍未接通。
