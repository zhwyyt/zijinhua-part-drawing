# Tekla 2017 原生平板写图桥接器

把已通过独立几何/表达/PLAN门的`nativeDrawingPlan.v1`（单视图）或`nativeDrawingPlan.v2`（含剖面）写入专用单零件图，保存关闭重开后读取实际SDK对象并导出DXF。推荐从项目根使用完整入口：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m zijinhua_tekla.cli draw-native-parts --out outputs/native-drawing/new-batch
```

省略`--ids`使用当前模型选择；也可显式指定`--ids 70906000,70906017`。必须已有用户授权目标，不能因为文档列出了ID就自行处理。`--out`须为新目录。`--ownership`指定跨批次所有权登记文件，默认`outputs/native-drawing-ownership.json`；重复更新必须沿用同一个登记文件。M4登记同时保存图纸数据库身份；旧文件缺ID时拒绝自动迁移。新建只尝试第1页、碰撞即停止，不完整枚举图纸目录。输出`result.json`、源证据、要求/计划、SDK读回、实际DXF、PNG与逐件审计。退出码0表示全PASS，1含FAIL/BLOCKED，2输入或执行错误。

需要NC补逐板孔形时，可提供 `--nc-settings-file <已有.ncfs文件>`，在新目录导出并核对采集前后源、编号、逐孔映射；不修改NC设置，不跳过几何门。

M4：`--drawing-attributes`指定新图属性，已有自有图沿用其原模板。保存实时纸面原始证据与归一化快照；字高 25，图幅取模板。视图优先 1:1，放不下时接受 2、2.5、5、10、20、50，且必须与 `viewPlan.scale` 一致；剖面使用同一比例。表格、说明及图框共同约束可用区；模板缺失、未知图元或比例 50 仍放不下均阻断，不回退固定预留。标题栏比例文字（`1:2` 这类）不计入模板指纹，但保存后必须等于本次视图比例；图框或其他文字变化仍拒绝。新图准备成功即登记页号，后续规划失败也保留证据。见[M4设计](../../docs/design/2026-10-23-m4-live-template-snapshot.md)。

v2 剖面：`sections` 各自携带 frame、cut、viewPlan 和标注命令；使用 `View.CreateSectionView` 创建真实 SectionView / SectionMark，全部视图预检所有权后才替换。每个视图读回 modelId、坐标系、原点和对象；主图与剖面覆盖、图面、纸面区域分别核验。`sourceFrame` 专门核对原始 DSTV，绘图 frame 可以是经几何核验的板面坐标系。2026-10-26 当前T2-10B-304主图＋A-A自有第1页已两次保存重开/SDK/DXF/源重查PASS，519项回归通过。见实测。

运行需Windows、已打开目标模型的Tekla 2017、.NET Framework 4编译器、Python项目依赖（含ezdxf、Shapely、Matplotlib）。完整入口自动编译读取器和写图器；可用`--reader-exe`/`--writer-exe`指定已有版本。SDK默认`D:\Program Files\Tekla Structures\2017\nt\bin\plugins`。

低层诊断调用（不能跳过正式来源门用于交付）：

```powershell
./tools/tekla2017_native_drawing/build.ps1 -OutputDirectory outputs/native-drawing-build
./outputs/native-drawing-build/TeklaNativeDrawing.exe --write outputs/batch/job.json outputs/batch/native
./outputs/native-drawing-build/TeklaNativeDrawing.exe --audit outputs/batch/job.json outputs/batch/reaudit
```

`--prepare`只创建/准备自有目标图并保存重开采集模板；正式流水线据此产生 `viewPlan` 后再执行 `--write`。`--audit`不重建对象，只打开自有目标进行读回、导出后关闭。三个模式均要求没有活动图纸，不保存或关闭用户当前图。低层job需含modelPath及非空parts。旧job缺少M4模板、显示契约或数据库身份时应重建；只补ownedSheet不足以运行，不猜页号或身份。

图纸名称`NATIVE-PLATE-R1`。保留用户原图；只更新登记且名称/目标一致的自有图。发现未拥有标注、锁定/下发/冻结时拒绝更新。圆弧/Boolean件使用原生解析曲线表达核验终态，并保留参与材料表计算的目标Part关联（不Hide，仅关闭重复线条与填充渲染）；定制曲线不自动跟随模型。详见[设计](DESIGN.md)及验收记录。

规划R2使用特征链、附近真实孔基准和局部尺寸；支持`suppressWitnesses`隐藏原生界线，由自有Line表达间隙/缩短，保存重开核查属性、端点、可见性。增加引线跨尺寸/界线失败检查，命令参数不变。见R2实测。

R2.1补完整单排孔两端边距、板身宽外置及R旁直接直边长度，组合间距只显示两端界线。写图同时禁用源Drawing.Bolt实体表示（隐藏、Invisible/Symbol、孔/轴符号关闭），不改模型螺栓。读回增加`sourceBolts`完整显示属性和`objectInventory`计数；缺源螺栓证据的旧读回不能直接通过新版审计，需重新采集。见R2.1实测。

R2.2取消R旁短直段按纸面长度省略；短局部区间可唯一对应实际相切直边时，用真实直段长度替换“直段+R投影”。尺寸端点和值不造假，局部排版与R引线避让独立验收。

R2.3对长短轮廓区间均优先直接加工尺寸，替换需相邻圆弧端点投影证明。圆角间隔拆成独立尺寸集、columnGroup同列整体避让，总体尺寸在外；保存重开核对实际Distance/Up与计划，不能以尺寸值正确代替布局正确。

P6（2026-10-21，按口径 v1，已离线实施、编译通过，现场未验证）：
- 尺寸显示取整（`NoDecimals`）。
- 等距由 Tekla 自动合并：`CombinedDimension` 设为计划中的 `NumberOfItemsTimesLength`，`MinimumNumberToCombine=3`；不再隐藏原生文字、叠加自写 n@p 文字。
- 计划 `bolts` 列为 `SYMBOL` 的螺栓组按车间图显示为 `Symbol2`、绿色、含孔和轴线；其余螺栓组仍隐藏，由自有曲线表达终态孔。
- R 改为原生 `RadiusDimension`，经过圆弧上三个真实点。
- 读回新增 `radius_dimension` 行；可见螺栓只允许出现在计划的符号组。旧版 job 缺少 `display`、`radii`、`bolts` 时拒绝写入。

见P6 离线记录。
