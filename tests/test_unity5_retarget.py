# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""5.6 类型树缺省字段的兼容性；使用 Unity 路线的 venv 运行。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/unity-retarget"))
try:
    import retarget
except ModuleNotFoundError as error:
    if error.name not in ("UnityPy", "lz4", "lz4.block"):
        raise
    retarget = None


@unittest.skipIf(retarget is None, "需要 Unity 路线 venv 的 UnityPy 和 lz4")
class Unity56TypetreeTests(unittest.TestCase):
    def setUp(self):
        self.params = dict(m_VectorParams=[], m_MatrixParams=[], m_BufferParams=[], m_UAVParams=[],
            m_TextureParams=[dict(m_NameIndex=2, m_Index=3, m_SamplerIndex=1, m_Dim=2)],
            m_ConstantBuffers=[dict(m_NameIndex=0, m_Size=16, m_MatrixParams=[],
                m_VectorParams=[dict(m_NameIndex=1, m_Type=0, m_Dim=4, m_ArraySize=0, m_Index=0)])],
            m_ConstantBufferBindings=[dict(m_NameIndex=0, m_Index=0)])
        self.names = {0: "Unit", 1: "value", 2: "tex_a"}

    def test_absent_modern_fields_preserve_legacy_reflection(self):
        before = copy.deepcopy(self.params)
        refl = retarget.Refl()
        refl.add_typetree(self.params, self.names)
        self.assertEqual(self.params, before)
        self.assertEqual(refl.cbs["Unit"]["vals"]["value"]["offset"], 0)
        self.assertEqual(refl.texs["tex_a"]["sampler"], 1)
        self.assertEqual(refl.texs["tex_a"]["ms"], 0)
        self.assertEqual(refl.for_rdef()["samplers"], [dict(name="tex_a", index=1)])

    def test_present_modern_fields_are_not_dropped(self):
        self.params["m_TextureParams"][0]["m_MultiSampled"] = True
        self.params["m_Samplers"] = [dict(bindPoint=5)]
        refl = retarget.Refl()
        refl.add_typetree(self.params, self.names)
        self.assertEqual(refl.texs["tex_a"]["ms"], 1)
        self.assertEqual(refl.samplers[5], "sampler_inline_5")

    def test_unsupported_structs_are_still_rejected_when_present(self):
        self.params["m_ConstantBuffers"][0]["m_StructParams"] = [dict()]
        with self.assertRaises(NotImplementedError):
            retarget.Refl().add_typetree(self.params, self.names)


if __name__ == "__main__":
    unittest.main()
