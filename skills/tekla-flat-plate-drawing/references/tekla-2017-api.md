# Tekla 2017 实测注意事项

以下是 2026-10-17 本机试验经验，不是跨版本 API 保证。使用前检查当前 SDK 签名、现场连接和已有执行器。此包不提供预编译程序，不依赖项目忽略的 outputs 目录。

## 连接和读取

- 本机安装目录为 `D:\Program Files\Tekla Structures\2017\nt\bin`，程序集在其 `plugins` 子目录：Tekla.Structures、Tekla.Structures.Model、Tekla.Structures.Drawing、Tekla.Structures.Geometry3d.Compatibility。其他机器先发现路径，不照抄。
- 临时 .NET Framework 程序可使用本机 `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` 编译，并用 AssemblyResolve 从实际 SDK 目录解析依赖。仅当 SESSIONNAME 为空时，本机试验曾设为 Console。不要更改用户现有环境值。
- 模型与 DrawingHandler 都必须连接到已打开的 Tekla。沙箱内 IPC 失败可能需要获准的提升权限调用，不等于需要接管桌面。避免启动图纸列表宏；本机某些宏会进入设置界面并断连。
- 保存原 TransformationPlane，在 finally 恢复；读实体、孔组与视图时显式变换坐标。枚举所有相关 BoltGroup，读目标关系、标准、槽孔参数、孔心/轴、组实体及原生图纸孔对象，不能只过滤 Hole1。
- 反射可用于核查属性/枚举。本机深挖 QueryOperation IL 曾导致崩溃，不作为常规探测方式。

## 图纸和对象所有权

- 使用 `SinglePartDrawing(part.Identifier, sheetNumber)`，创建前读已有图纸，明确目标图。三张试验在 sheet 0 插入失败、sheet 1 成功，根因未确定；不可据此通用改用 1 或随机重试页号。
- 车间属性文件名尚未验证。读取现场已认可配置，别捏造属性文件名；默认配置生成的图只能按实际验收结果报告。
- 获取目标零件视图，再在该 view 中创建标注，不在总图页上调用尺寸创建。
- 默认视图可能带邻件。`Drawing.Part.Hideable.HideFromDrawingView()` → Modify → Select → IsHidden 已保存重开验证。普通模型轮廓模式应有一个可见目标；解析曲线模式允许目标隐藏但必须保留唯一正确ID关联，且实际导出完整终态。邻件始终不可见，不推广为所有对象都可隐藏。
- 为辅助 Text/Line 记录可读所有权标签及源特征标识，同时保存对象 ID 清单。重跑只替换自身标注；不要删除所有文字/直线，删除尺寸父集后不要再次删除同批子对象。
- Insert/Modify 返回 true 不足以证明持久化。保存、关闭、重开并 Select 后读回。连接中断、实体版本改变或写入失败时停止该件；保留现场与日志，不继续重复插入。

## 尺寸与等距

- 从目标 Drawing.Part 建 `StraightDimensionSetAttributes(drawingPart)`，由 StraightDimensionSetHandler 在目标 view 创建真实坐标尺寸集。以当前 SDK 重载为准，不猜参数签名。
- 本机实测默认 FilledArrow 枚举值 -1004、箭头 2×2、字体 2.5、精度 0.1；优先使用枚举名称。读属性前先 Select，避免读到未刷新的默认值。
- 等距组合设置 `CombinedDimension.MinimumNumberToCombine = 2` 和 `NumberOfItemsTimesLength`。3 个孔心两段 160 显示 `2@160`。边距与孔距分界要保护，避免软件自动合并。
- 子尺寸对象可能重复报告组合文字，如两端都出现 `7@200`；以图面及实际锚点链核验，不能把读回文字次数当成孔数或可见标签数。
- 自由覆盖 `StraightDimension.Value` 后 Modify 虽成功，重读仍回到实测值。禁止用覆盖数值实现数量说明。
- Distance 文档与本机表现存在单位差异：scale 5 下数值 60 约为 12 mm 纸面。每种版本/适配器先验证距离单位，再统一转换，避免把比例乘两次。真实坐标保留精度，仅最终显示舍入。

## 半径、编号和引线

- 本批 Native RadiusDimension 产生了不需要的整圆，即使半径/箭头属性正确。当前使用原生 Text + LeaderLinePlacing，写 `R=35` 等文字，FrameNone、固定放置，指向实际弧段，不创建 Circle。
- 无引线编号明确使用 PointPlacing、NoArrow、FrameNone、固定放置；不要继承默认带箭头样式。InsertionPoint 在本机表现接近文字框中心，布局后测真实框；旋转角读回可能为 0–360 度。
- 圆孔说明近侧交点按规则计算；文字与孔心重合时先重新布置文字，不能除零或任取箭头方向。长圆孔要与两段直线及端部半圆的实际边界求交并验证弧域。
- 中心十字用两条原生点划 Line，方向来自实际孔轴；旧样例每侧延长 8 模型 mm 是单图参数，不是通用纸面要求。

## 缩短界线与图面

- 本机未找到逐条界线起点间隙属性。`ExtensionLineTypes.Yes` 在该 SDK 中表示隐藏原生界线（名称易误读），可在读回验证后用自有原生实线补短界线；辅助线无箭头，原生尺寸锚点和值不变。
- 已验样例 scale 2，普通间隙 6、肩部 8 模型 mm 对应 3/4 mm 纸面。应由当前轮廓及比例决定，不能把这两个数写成所有件规则。
- 视图纸面边界按 Origin + FrameOrigin 及 Width/Height 核对；这不能代替标题栏/表格占位检查。297×210 是 A4，594×420 是 A2。
- 辅助 Text/Line 不保证随模型关联更新。保存源几何指纹，模型变化须重新规划/创建/验收，不能只修改孔说明文字。

## 执行器现状

项目`draw-parts`是离线入口；正式原生入口已为`draw-native-parts`，SDK程序在`tools/tekla2017_native_drawing/`。忽略目录里的NativeTrial、BatchNativeTrial、RepairChamferTrials只作历史探针，不作为通用适配器。

配套 `plan-expressions` 已实现加工表达要求生成与覆盖审计，见 [表达引擎](expression-engine.md)。它不写图，需接真实快照和原生读回转换，不能当作 create 命令使用。

## 通用执行器现场补充（2026-10-20）

- 同编号实例共享页号空间；枚举目录使用全局未用页号。更新先查名称/目标ID，再核对显式ownedSheet和锁定/冻结/下发状态；修改前完整预检人工标注。Select后读Mark。
- Drawing.Arc构造顺序为起点、终点、圆心，选择顺时针弧侧。必须用实际DXF核验胶囊的完整边界，不能只看两个半圆数量。
- StraightDimensionSet.Distance从第一个传入锚点测量，不从最外侧锚点测量。符号/坐标正确而层距错误时会穿字，须读回实际图面。
- 模型圆弧图形可能仍以弦代弧，圆环现场约4.16 mm偏差不满足0.1 mm。曲线/Boolean件使用核验终态的原生Line/Arc/Circle，隐藏近似Part但保留目标关联。定制曲线不能自动随模型更新。
- DXF可能将`3@90`拆成多个TEXT，导致前缀和值重叠。当前保留真实原生组合尺寸，隐藏其字体并另放`3@  90`原生Text；核对SDK组合值、实际显示字符串和真实字形框。空白字符框不能当可见字形碰撞。
- 对象COD_ID为自有UUID，COD_KEY映射源特征；不是SDK公开原生ID。重复运行对象键/数量稳定，UUID可重建。原生SDK与实际DXF才是读回来源，不能从计划补缺。

- 规划R2已将缩短界线接入通用桥接器：suppressWitnesses设置ExtensionLineTypes.Yes，自有Line按真实锚点的轴向投影绘制；保存重开读取extension、端点及SolidLine并在实际DXF复核。局部肩部界线可进一步缩短以避R引注，不能移动尺寸测量点。
- 独立图面门用实际SDK尺寸端点/Distance/Up重建尺寸线，检查Text的实际tip/at引线是否跨尺寸线或自有界线。该门能发现旧“无压字PASS”漏掉的交叉；不是任意线线交叉或模板美观的完整证明。
- `Drawing.Bolt.Hideable.IsHidden=true`不会清空原来的Solid/Red属性。独立终态孔边界方案同时设BoltAttributes为Invisible/Symbol、SymbolContainsHole=false、SymbolContainsAxis=false，再隐藏；保存重开逐项读回。此修改仅为Drawing对象，不改Model.Bolt。
- 三孔的`2@80`中点正好经过中间孔界线，字体Invisible也可能继续遮线；仅移动自有Text不能消除原生隐藏文字遮挡。组合尺寸显示两端界线，内部孔保留中心线和全部原生测量点；不放宽“完整界线出现在DXF”的验收。
