# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Unity 构建时剥空程序的 Shader（D3D11 blob 只剩 0 计数）必须安静跳过并计入 empty_blob，不能抛 IndexError。
原型是 A Space for the Unbound (2020.3) 里的 TextMeshPro/Mobile/Distance Field External Outline：只有 D3D11 平台、
压缩 blob 5 字节、解压 4 字节、程序表计数 0、两个 pass 均无子程序。这里用同结构自造数据，不含游戏资源。"""
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/unity-retarget"))
try:
    import lz4.block
    import retarget
    import unityblob
except ModuleNotFoundError as error:
    if error.name not in ("UnityPy", "lz4", "lz4.block"):
        raise
    retarget = None


def stripped_shader_typetree():
    raw = struct.pack("<i", 0)   # 程序表计数 0：Unity 在构建时剥掉了全部子程序
    comp = lz4.block.compress(raw, store_size=False)
    empty_prog = dict(m_SubPrograms=[], m_CommonParameters=copy.deepcopy(retarget.EMPTY_PARAMS))
    passes = [dict(progVertex=copy.deepcopy(empty_prog), progFragment=copy.deepcopy(empty_prog),
                   progGeometry=dict(m_SubPrograms=[]), progHull=dict(m_SubPrograms=[]), m_NameIndices=[])
              for _ in range(2)]
    return dict(m_ParsedForm=dict(m_Name="TextMeshPro/Mobile/Distance Field External Outline",
                                  m_SubShaders=[dict(m_Passes=passes)], m_KeywordNames=[]),
                platforms=[retarget.PLAT_D3D11], offsets=[[0]], compressedLengths=[[len(comp)]],
                decompressedLengths=[[len(raw)]], compressedBlob=comp)


@unittest.skipIf(retarget is None, "需要 Unity 路线 venv 的 UnityPy 和 lz4")
class StrippedShaderTests(unittest.TestCase):
    def test_empty_program_table_parses_to_no_entries(self):
        self.assertEqual(list(unityblob.parse_blob(struct.pack("<i", 0))), [])

    def test_stripped_shader_is_skipped_counted_and_left_untouched(self):
        tt = stripped_shader_typetree()
        before = copy.deepcopy(tt)
        base = retarget.log["empty_blob"]
        nerr = len(retarget.errors)
        self.assertFalse(retarget.process_shader(tt, True, True, None, None))
        self.assertEqual(retarget.log["empty_blob"], base + 1)
        self.assertEqual(len(retarget.errors), nerr)
        self.assertEqual(tt, before)   # 类型树原样：成品里它保持 D3D11-only，与 Windows 构建一致


if __name__ == "__main__":
    unittest.main()
