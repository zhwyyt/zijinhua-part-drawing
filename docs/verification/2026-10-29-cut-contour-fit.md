# 切割出来的轮廓拟合

日期：2026-10-29

## 失败案例

梁命令画出的腹板，两个角由 `BooleanPart` / `BOOLEAN_CUT` 切出。切割体轮廓上的 `CHAMFER_ROUNDING` 吃掉了相邻边，解析重建失败。终态盖面把每个圆角存成 12 条约 4.58 mm 的弦。几何事实只留下 `LINE`，没有半径。

同一次读取里，另一处切割是直边，库存边仍是直线。这些不能收成圆弧。

## 期望

- 只拟合落在布尔切割体平面盖边界上的终态外轮廓链。
- 这条链的顶点都在同一圆上，每条弦的中点离该圆不超过模型容差 0.1 mm，而且整段不是一条直线，才收成一条 `ARC`。
- 库存边、直切口、对不上圆的多边形缺口、孔符号、没有落在切割体上的折面，保持 `LINE`。拟合失败也保持 `LINE`。
- 半径和圆心来自这些弦，不直接采用 `Chamfer.X/Y`。
- 不按零件号、构件号或项目名分支。

## 证据字段

拟合段的 `basis` 是 `CUT_CONTOUR_FITTED_WITHIN_TOLERANCE`。`fit` 记录 `method=CUT_CONTOUR_CIRCLE`、`center`、`radius`、`maxRadialResidualMm`、`maxChordDeviationMm`、`chordCount`、`operationIds`。`sourceRefs` 含终态盖和对应切割体。轮廓上另有 `cutContourFits`。轮廓依据仍是 `OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE`。

## 回归

`tests.test_cut_contour_fit`。

用 2026-10-29 选择读取回放该腹板：两条弧都是 R35.000，圆心分别在角点，各 12 弦，径向残差 0，弦偏差 0.0749 mm，操作分别对应两个圆角切割。直边和库存边仍是 `LINE`。同批其余 8 件没有新的拟合弧。证据仍是 `SUFFICIENT`。没有写 Tekla。
