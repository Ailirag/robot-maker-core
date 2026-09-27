"""The tangent-hole test of brep_repair must ignore the axis a planar face is
flat on: every hole of a face at x = const "touches" the outer wire in x."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from engineering.instructions import brep_repair


def _box(x0, x1, y0, y1, z0, z1):
    return SimpleNamespace(BoundBox=SimpleNamespace(XMin=x0, XMax=x1, YMin=y0, YMax=y1,
                                                    ZMin=z0, ZMax=z1))


class TangentHoles(unittest.TestCase):
    outer = _box(-101.4, -101.4, -62.0, 62.0, 4.0, 78.0)

    def test_a_hole_inside_the_face_is_not_tangent(self):
        self.assertFalse(brep_repair._touches(_box(-101.4, -101.4, -50.6, -37.4, 48.9, 62.1),
                                              self.outer))

    def test_a_hole_reaching_the_side_edge_is_tangent(self):
        self.assertTrue(brep_repair._touches(_box(-101.4, -101.4, -62.0, -52.0, 56.0, 66.0),
                                             self.outer))

    def test_a_hole_reaching_the_bottom_edge_is_tangent(self):
        self.assertTrue(brep_repair._touches(_box(-101.4, -101.4, -10.0, 10.0, 4.0, 14.0),
                                             self.outer))


if __name__ == "__main__":
    unittest.main()
