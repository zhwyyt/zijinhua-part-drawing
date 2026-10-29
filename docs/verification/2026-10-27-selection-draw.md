# 2026-10-27 当前选择出图

模型 `G:\徐汇T1地块带图纸\徐汇T1地块`。入口是本仓库 `draw-native-parts`，省略 `--ids`，属性 `标准零件图10(按1倍导出)`，所有权文件 `I:\zijinhuatekla\outputs\m4-implementation-20261023\t1-ownership.json`。证据目录 `outputs/native-drawing/20261027-selection/`（不入库）。退出码 1，`result.json` 为 PASS=0、FAIL/BLOCKED=3。准备阶段 `originalDrawingRestored=true`，`workPlaneRestored=true`。

| 零件 | 模型 ID | 结果 |
| --- | --- | --- |
| T2-10B-305 | 449588104 | 专用几何阻断，未建图。坡口已核验：45°、深 12、钝边 2、板厚 14，沿 270 边。一组 6 个 M24 位置被模型写成槽孔（槽长 30），并连接另外两块板；Hole2 为假不能证明本板是圆孔，所以停在逐板孔形证据。 |
| T2-10B-101 | 449291360 | 专用几何阻断，未建图。切割操作没有全部解析；实体有厚度中间点，不是完整棱柱。 |
| T2-10B-450 | 450067509 | 几何 READY。新建自有图 `[T2-10B-.450 - 1]`，对象 `453672835`，第 1 页，阶段 PREPARED。标注未写。 |

T2-10B-450 轮廓约 234 mm。模板文字按整行带宽占用，268 条文字的带宽合在一起盖住可用纸面，比例 1 到 50 都是 `TEMPLATE_ENVELOPE_OVERFLOW`。按规则不换图幅。

写图前后模型身份一致。T2-10B-305 与 T2-10B-101 的零件证据逐字段相同。T2-10B-450 的实体坐标在两次读取之间有约 1e-6 mm 级漂移，批次来源复查因此把三件都标成 `CHANGED_OR_UNAVAILABLE`。这不是模型被改写。
