# independent_layout

独立布局系统的阶段 0 离线工具。重新审计原生输入、适配尺寸方案并核验语义覆盖、整理三种模板采样及验证明确给定的几何占用，供后续新求解器使用。当前没有布局求解、Tekla 写图或正式入口接线能力。

## 核心职责

| 命令 | 输入 | 输出及限制 |
|---|---|---|
| `sources` | `teklaNativeEvidence.v1` | 冻结原始实例、重新计算的几何和完整表达要求；附加视图保留并报告 `OUT_OF_SCOPE` |
| `expressions` | `NativeLayoutSources.v1` | 承接尺寸方案，生成物件/要求多对多映射；只证明语义责任完整，不证明图面可见 |
| `prepare-input` | 单个source/expression及模板采样 | 重审语义并列出逐物件原生绑定/标定门；不生成可执行LayoutInput |
| `template-study` | 只读采样目录及可选 `.wd` | 实测图幅、角色样式、字段差异、未完成能力；始终为 `STUDY_ONLY` |
| `template-load-study` | SDK加载探针目录及单个模板研究报告 | 核对名称/路径加载、负对照与文件指纹；属性自动改写为INPUT_BLOCKED |
| `template-bundle` | 原采样、转换副本及依赖清单 | 保留原值并构建唯一别名属性包；只写新的本地输出目录 |
| `template-bundle-study` | 三套属性包/采样及隔离探针目录 | 核对备份、逐调用目录/状态、加载与移除负对照；不授予图页生产资格 |
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

2026-10-31独立内口扩展：`sources --extend-cutouts` 对原INPUT_BLOCKED尝试独立切割/完整实体证明，成功封装为NativeLayoutSource.v2，保留旧失败并在使用时重放证明。`expressions --complete-cutouts` 对原成功来源补轴向矩形/圆角矩形内口尺寸；v2来源自动使用此扩展。未开启的r1行为保持不变。

```powershell
python -m zijinhua_tekla.part_drawing.independent_layout sources --evidence evidence.json --extend-cutouts --out outputs/cutout-source-new
python -m zijinhua_tekla.part_drawing.independent_layout expressions --sources outputs/cutout-source-new/report.json --complete-cutouts --out outputs/cutout-expression-new
# source-entry.json / expression-entry.json 是上面各批次entries中的同一个实例封装。
python -m zijinhua_tekla.part_drawing.independent_layout prepare-input --source source-entry.json --expression expression-entry.json --template-study template10-report.json --template-study template20-report.json --template-study template50-report.json --out outputs/input-preparation-new
```

输入准备当前输出INPUT_BLOCKED及退出码1，因为模板研究报告仍无生产资格；这是明确保留原生能力门，不代表已完整的加工语义失败。几何扩展范围、数值预算和限制见[实施验证](../../../../docs/verification/2026-10-31-independent-cutout-implementation.md)。

### 模板加载核验

`TemplateLoadProbe.cs` 是独立的受监测加载探针，不是原生写图器。现场发现 Tekla 会自动转换20/50旧格式 `.wd`，最终探针在连接前阻断这类文件；未命中已知风险也须逐调用核对文件摘要与目录新增/消失项。它不调用 Insert/Modify/Save/Close，不能因此推断 SDK 没有文件副作用。实际结果及两份属性恢复记录见[模板加载验证](../../../../docs/verification/2026-10-31-independent-template-load-probe.md)。

构建使用同目录 `build-template-probe.ps1 -OutputDirectory <新目录>`。运行参数为 `job.json <新输出目录> <SDK目录>`；job包含明确的modelPath、partId和templates，每个模板包含templateId、attributePath及files（主属性、四份伴随文件、被引用布局）。前置发现旧格式时退出1并写preflight.json，尚未调用原生API。不要对现场原件去前缀来绕过此门。

已采证据可离线核验：

```powershell
python -m zijinhua_tekla.part_drawing.independent_layout template-load-study --probe probe-directory --template-study template-study.json --out outputs/load-study-new
# prepare-input 可重复追加 --load-study load-study-report.json；省略时原输出保持不变。
```

TemplateLoadStudy只增加诊断证据，不关闭LayoutPreparation原有7类门。实际加载解析路径、二进制布局传递依赖、图页内容/有效样式、单位/占用和保存重开仍需实证；20/50自动转换失败不能被相同图幅掩盖。

### 隔离副本加载

10/20/50已通过同一隔离加载流程，每套18份文件；20/50使用归档的SDK转换副本，既有2755项值逐字段保持。原件仍受旧格式禁用门保护。见[设计](../../../../docs/design/2026-10-31-isolated-template-bundles.md)与[现场验证](../../../../docs/verification/2026-10-31-isolated-template-bundles.md)。

`template-bundle --manifest <清单> --template-study <报告> --out <新目录>`生成report.json、files目录及template-job-entry.json。清单字段为attributeName、original、converted、dependencies；dependencies是`[{"name":"依赖文件名","source":"来源路径"}]`，路径相对清单目录或使用绝对路径。这里只显式封装绘图/视图/尺寸/布局文件；未启用外部视图列入unresolvedDisabledViews，完整表格/字体依赖仍待核验。

三份template-job-entry.json作为job.templates，另指定modelPath和partId。用`build-template-probe.ps1 -Bundle -OutputDirectory <新目录>`构建TemplateBundleProbe.exe，参数仍为`job.json <新输出目录> <SDK目录>`。运行前必须用verify_template_bundle重放三包并核对job中的别名/摘要/文件哈希；探针只接受三种规格齐全且唯一的ASCII别名。它锁住模型attributes内全部既有文件，备份、负对照、CreateNew安装、两次加载、仅清理哈希未变的自建副本，再做移除负对照。不调用图纸Insert/Modify/Save/Close。若中断或有清理错误，按journal.json/install-intent.json核验现场；不要自动删除未知文件。

```powershell
python -m zijinhua_tekla.part_drawing.independent_layout template-bundle-study --probe capture-directory --bundle bundle10.json --bundle bundle20.json --bundle bundle50.json --template-study study10.json --template-study study20.json --template-study study50.json --out outputs/bundle-study-new
# prepare-input 可追加 --bundle-load-study outputs/bundle-study-new/report.json。
```

三种规格均ISOLATED_LOAD_MATCHED时顶层仍STUDY_ONLY，productionEligible=false。54的输入准备已绑定此证据，7类原生能力门全部保留；只证明本次唯一副本的加载路径和图幅/标题，不证明字体、内容或实际视图已应用。

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
