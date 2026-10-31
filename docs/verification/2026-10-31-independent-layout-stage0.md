# 独立布局阶段0：离线基础实施记录

日期：2026-10-31。状态：阶段0进行中，离线基础可运行；没有完成布局求解或原生出图验收。

## 1. 授权、执行计划与改动范围

用户确认“如果开发计划已经写好，直接开干”。按 [主方案第7节](../design/2026-10-30-independent-layout-system.md) 从阶段0开始，新增 `src/zijinhua_tekla/part_drawing/independent_layout/` 和 `tests/test_independent_layout.py`。没有修改旧求解器、表达/几何规则、正式 CLI 或已有原生工具；没有调用 Tekla 写图、保存、重开或修改模型。

实施前后对原有 `src/`、`tests/`、`tools/` 共125个文件比对 SHA-256，全部相同。记录：`outputs/independent-layout-original-files.json`、`outputs/independent-layout-stage0/original-code-check.json`。文档更新包括模块说明/设计、主方案、STATUS、TASKLIST、README及CHANGELOG。

## 2. 已实现的可验证行为

- `sources` 从原始 `teklaNativeEvidence.v1` 重新调用稳定上游几何/表达门，保留原始实例、来源、全部要求及摘要，不读取旧布局作为答案。重复实例、摘要/身份不一致、无要求等拒绝；附加视图完整保留并在求解前排除。
- `template-study` 读取既有采样的 capture/objects/presentation，提取实际尺寸、比例和角色样式；候选属性按混合编码解析，通用样式与内容格式分开。报告绑定三份采样摘要及属性原始字节摘要，始终 `STUDY_ONLY`。
- `check-observation` 检查明确输入的线宽包络、填充多边形、可用区域和最小净距；同对象不整体豁免。支持受限的两条零宽直线共同端点连接。未知图元、空图和非法几何拒绝。报告绑定完整观测摘要，输出 `GEOMETRY_CLEAR` 也不代表图纸合格。
- `environment` 用4个布尔变量的小型冲突选择模型验证 OR-Tools 可运行，固定单线程、种子0和确定性预算，10次均为同一最优解 `[0,1,1,0]`。这不是布局求解器或性能验收。
- 独立入口只向新目录写 `report.json`，拒绝覆盖旧报告。完整状态和退出码定义见模块 README。

## 3. 真实来源回放及设计纠偏

本地证据目录：`outputs/independent-layout-stage0/`。

| 回放 | 来源 | 结论 |
|---|---|---|
| `selection9/report.json` | `outputs/native-drawing/20261029-selection-images/before.json` | 8件 SOURCE_READY、1件 OUT_OF_SCOPE、0件 INPUT_BLOCKED |
| `20261028-101-584/report.json` | 同名旧出图目录的 before.json | 101、584均 SOURCE_READY |
| `20261029-225-preview/report.json` | 同名旧预览目录的 before.json | 225为 SOURCE_READY；原图失败不因此转为合格 |
| `case-inventory.json` | 上述封装汇总 | 12份实例快照、11个不同实例；84待绑定、77/494/94仅模板证据；草稿，不是发布准入集 |

305实际表达为 `expressionRequirements.v2`，要求 `main` 和 `section:0`。原设计把它列作首版单主视图整页纵向验证样例有矛盾。本次保留全部要求并报告 `ADDITIONAL_VIEWS_REQUIRED`，不删除剖面。后续纵向切片改用已重审的584完整输入及新授权测试页；305仍保留范围排除回归与人工认可局部关系。原305、584已验收页和101人工保留页禁止覆盖。

案例清单按模型身份/实例ID区分实例，并保留不同来源摘要的历史快照；584的历史快照不重复计入实例数。旧布局/图片/DXF文件指纹、84绑定、完整覆盖和人工偏好还未冻结，因此不能据此宣布发布分母或最终通过率。

## 4. 三种模板属性与解析回放

10属性已从当前模型 attributes 目录只读复制6份原始文件：`.wd`、`.wd.more`、`.wd.copt`、`.wd.filter`、`.wd.npfilter`、`single 10.lay`。存于 `outputs/layout-study-20261030-77-01/attribute-snapshots/`，清单在该目录上级 `attribute-manifest.json`。三种规格各6份快照均与清单哈希一致。

最终解析报告为 `template10-final/report.json`、`template20-final/report.json`、`template50-final/report.json`；此前探针结果保留，不覆盖。

| 规格 | 实测尺寸 | 尺寸／编号／孔说明字高 | 属性尺寸对照 | 未观测角色 |
|---|---|---|---|---|
| 10 | 2980×2110 | 25／25／25 | 一致 | 无上述角色缺失 |
| 20 | 5950×4210 | 50／50／未采 | 一致 | 孔说明 |
| 50 | 14860×10510 | 125／125／125 | 一致 | 无上述角色缺失 |

三种样本的尺寸字体均为romsim、编号Arial；已观测孔说明字体为宋体。每种属性有4项非空 dpm/dsm 内容格式解析结果，不代表4种角色或全部依赖均可用。

10属性没有20/50的 `wdraw_dial.` 前缀。首次通用解析误报宽高字段不匹配，核对原字节后确认实际尺寸相同；已新增两种字段格式支持和混用冲突拒绝测试。视图标签中的 `<< Mark >>` 不是合法独立XML，原样保留；只解析相关标记内容的XML，不宽松修复全部文件。

仍待：真正加载身份及完整依赖、表格/备注等当前内容绑定、逐对象SDK—显示对应、单位/多比例/缩短、字体/箭头/符号占用、保存重开稳定性。未把观察值直接当生产配置。

## 5. 环境与测试

隔离环境：`outputs/independent-layout-env/`；Windows x64、Python3.12.9、OR-Tools9.14.6206、Shapely2.1.2、GEOS3.13.1。精确依赖锁：`src/zijinhua_tekla/part_drawing/independent_layout/requirements-stage0.lock`。父包既有初始化导入项目依赖，因此隔离环境同时安装当前版本 ezdxf/matplotlib/reportlab；全量测试另需 pypdf6.14.2。没有修改全局环境或 pyproject。

所有测试明确设置 `PYTHONPATH=I:/zijinhua-part-drawing/src`，打印导入路径确认来自当前仓库。默认Python曾指向其他checkout，未将那条路径下的结果当本次证明。

最终锁定隔离环境全量结果：**452项通过，43.750秒**，其中新模块27项。覆盖重复键、有限数值、摘要篡改、来源缺证、附加视图、样式显示差异、两种属性格式、同对象交叉、旋转文字、线宽越界、净距临界、端点例外、未知/空图及输出覆盖保护。当前基础环境在此前版本全量450项通过；最终452项以上述隔离环境为准。

执行形式：

```powershell
$env:PYTHONPATH = "$PWD/src"
$env:PYTHONIOENCODING = "utf-8"
& outputs/independent-layout-env/Scripts/python.exe -c "import tempfile,unittest; tempfile.tempdir=r'I:/zijinhua-part-drawing/outputs/independent-layout-test-temp'; result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover('tests')); raise SystemExit(not result.wasSuccessful())"
```

Windows沙箱曾间歇性拒绝 TemporaryDirectory 新目录访问，即使位于workspace也出现；最终在沙箱外执行同一离线测试，临时文件仍放本项目outputs。测试未跳过或放宽。隔离环境首次全量测试缺pypdf，安装与原测试环境一致的版本后重跑通过。

## 6. 静态检查与文档核验

- `verify-quality`：通过，0错误、2项复杂度警告，位于来源重审与模板研究编排函数。较长的图元处理和样式检查已按职责拆分；剩余警告包括集合推导和输入门判断，记录为后续扩展时的维护关注点。
- `verify-module`：通过；警告为模块下没有独立src/tests目录。本项目代码已处于仓库src、测试统一位于根tests，不复制目录满足扫描器建议。
- `verify-security`：最终0项发现。首次扫描把同一行的 `Path(__file__)` 与后面的 `platform` 后缀 `form` 组合误识别为用户路径输入；人工核对来源为解释器文件路径，拆成每字段一行后扫描通过。没有为此修改路径行为。
- `verify-change`：沙箱内最初误报0变更，沙箱外只读重跑识别13项路径；脚本行数统计仍为0，不能用它代替真实diff。另以 `git status --short`、`git diff --numstat`、`git diff --check` 和新模块文件逐项核对范围及文档同步。
- `check-docs.ps1`：退出码0，沿用既有STATUS/TASKLIST体系。模块README/DESIGN已替换生成骨架，主方案进度、305范围纠偏、10候选属性状态均已同步。
- 最终全量测试后只调整环境报告一处换行及文档；`environment-final/report.json` 已再次通过10次确定性冒烟，模块导入仍来自当前src。

## 7. 阶段门及下一步

| 门 | 已有证据 | 未完成，不能关闭 |
|---|---|---|
| T01 | 原生重审、身份/摘要封装、11实例回放 | 尺寸组、同列/组合/孔带与完整覆盖映射；三种正式契约 |
| T02 | 三种源图、三套候选属性及双格式解析 | 实际加载和依赖闭包、内容绑定、源图单位/多比例/缩短 |
| T03 | SDK角色/显示字体对照，最小几何反例 | 全部原语、字体文件、占用与误差、可读性净距、保存重开 |
| T04 | 精确运行库锁、CP-SAT10次确定性冒烟、全量回归 | 真实布局性能/内存、规模保护与生产预算 |
| T05 | 原文件完整性核对，本次无原生写操作 | 现场源噪声重复采集、恢复/中断/人工变更试验 |
| T07 | 初始案例库存，重复实例不累计 | 发布准入集及认可偏好冻结 |

阶段0没有完成。下一步先完成独立尺寸/覆盖适配和正式契约，再准备584新测试页的明确写入计划及读回断言；现场能力通过后才将相应原语交给候选和CP-SAT布局求解。整个过程保持原正式入口和既有图纸。
