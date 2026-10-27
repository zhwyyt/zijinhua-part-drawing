import unittest

from zijinhua_tekla.part_drawing.dimension_style import PART_CAD_DIMENSION_STYLE_V1


class PartDrawingDimensionStyleTests(unittest.TestCase):
    def test_v1_matches_the_confirmed_cad_sample(self):
        style = PART_CAD_DIMENSION_STYLE_V1
        self.assertEqual("partCadDimensionStyle.v1", style.style_id)
        self.assertEqual("Arial", style.font_family)
        self.assertEqual(0.65, style.text_width_factor)
        self.assertEqual(2.5, style.text_height_mm)
        self.assertEqual("OBLIQUE", style.linear_arrow_name)
        self.assertEqual("", style.leader_arrow_name)
        self.assertEqual(2.0, style.arrow_size_mm)
        self.assertEqual(2.5, style.extension_offset_mm)
        self.assertEqual(2.0, style.extension_beyond_mm)
        self.assertEqual(1.0, style.text_gap_mm)
        self.assertEqual(0, style.decimal_places)

    def test_measurement_factor_is_inverse_of_layout_scale(self):
        style = PART_CAD_DIMENSION_STYLE_V1
        expected = {1.0: 1.0, 0.5: 2.0, 0.2: 5.0, 0.1: 10.0, 0.05: 20.0}
        self.assertEqual(
            expected,
            {scale: style.measurement_factor(scale) for scale in expected},
        )
        with self.assertRaisesRegex(ValueError, "scale must be positive"):
            style.measurement_factor(0.0)

    def test_v1_formats_display_values_with_sample_precision(self):
        style = PART_CAD_DIMENSION_STYLE_V1
        self.assertEqual("243", style.format_measurement(243.174))
        self.assertEqual("244", style.format_measurement(243.6))


if __name__ == "__main__":
    unittest.main()
