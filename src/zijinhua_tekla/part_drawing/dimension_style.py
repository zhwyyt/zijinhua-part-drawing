from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class CadDimensionStyle:
    style_id: str
    font_family: str
    text_width_factor: float
    text_height_mm: float
    linear_arrow_name: str
    leader_arrow_name: str
    arrow_size_mm: float
    extension_offset_mm: float
    extension_beyond_mm: float
    text_gap_mm: float
    decimal_places: int
    suppress_trailing_zeros: bool
    text_above_line: bool

    def measurement_factor(self, layout_scale: float) -> float:
        if layout_scale <= 0:
            raise ValueError("layout scale must be positive")
        return 1.0 / layout_scale

    def format_measurement(self, value: float) -> str:
        quantum = Decimal(1).scaleb(-self.decimal_places)
        rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        text = f"{rounded:.{self.decimal_places}f}"
        if self.suppress_trailing_zeros and "." in text:
            text = text.rstrip("0").rstrip(".")
        return text


PART_CAD_DIMENSION_STYLE_V1 = CadDimensionStyle(
    style_id="partCadDimensionStyle.v1",
    font_family="Arial",
    text_width_factor=0.65,
    text_height_mm=2.5,
    linear_arrow_name="OBLIQUE",
    leader_arrow_name="",
    arrow_size_mm=2.0,
    extension_offset_mm=2.5,
    extension_beyond_mm=2.0,
    text_gap_mm=1.0,
    decimal_places=0,
    suppress_trailing_zeros=True,
    text_above_line=True,
)
