import unittest

from app.io.xlsx import read_workbook, write_workbook


class TestXlsx(unittest.TestCase):
    def test_roundtrip(self):
        rows = [
            ["部门", "职位", "人数"],
            ["外交部", "科员", "2"],
            ["税务局", "执法员", "3"],
        ]
        write_workbook("t.xlsx", "职位表", rows)
        wb = read_workbook("t.xlsx")
        self.assertIn("职位表", wb)
        self.assertEqual(wb["职位表"], rows)

    def test_special_chars_roundtrip(self):
        rows = [["a<b>&c", "引号\"x\"", "换行\n单元格", "080904K"]]
        write_workbook("t2.xlsx", "s", rows)
        wb = read_workbook("t2.xlsx")
        self.assertEqual(wb["s"][0], ["a<b>&c", '引号"x"', "换行\n单元格", "080904K"])

    def test_sparse_cells_padded(self):
        # 单元格带 r 引用时跳列——读取需按列号回填
        import zipfile

        sheet = (
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1">'
            '<c r="A1" t="inlineStr"><is><t>甲</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>丙</t></is></c>'
            '</row></sheetData></worksheet>'
        )
        from app.io.xlsx import _CTMPL, _RELS, _WB, _WB_RELS

        with zipfile.ZipFile("t3.xlsx", "w") as z:
            z.writestr("[Content_Types].xml", _CTMPL)
            z.writestr("_rels/.rels", _RELS)
            z.writestr("xl/workbook.xml", _WB.format(name="s"))
            z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
            z.writestr("xl/worksheets/sheet1.xml", sheet)
        wb = read_workbook("t3.xlsx")
        self.assertEqual(wb["s"][0], ["甲", "", "丙"])

    def test_shared_strings_path(self):
        import zipfile

        from app.io.xlsx import _CTMPL, _RELS, _WB, _WB_RELS

        shared = (
            '<?xml version="1.0"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>索引0</t></si>"
            "<si><r><t>富文本</t></r><r><t>拼接</t></r></si>"
            "</sst>"
        )
        sheet = (
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c>'
            '<c r="C1"><v>42</v></c>'
            '</row></sheetData></worksheet>'
        )
        with zipfile.ZipFile("t4.xlsx", "w") as z:
            z.writestr("[Content_Types].xml", _CTMPL)
            z.writestr("_rels/.rels", _RELS)
            z.writestr("xl/workbook.xml", _WB.format(name="s"))
            z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
            z.writestr("xl/sharedStrings.xml", shared)
            z.writestr("xl/worksheets/sheet1.xml", sheet)
        wb = read_workbook("t4.xlsx")
        self.assertEqual(wb["s"][0], ["索引0", "富文本拼接", "42"])


if __name__ == "__main__":
    unittest.main()
