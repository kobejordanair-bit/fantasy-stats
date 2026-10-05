import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fantasy_core import blank_season
from season_exports import export_all, safe_csv, SHEETS
from season_exports import source_note, source_note_height
from fetch_stats import new_run


def fixture():
    s=blank_season(2025,'=SYNTHETIC </ScRiPt><img src=x onerror=alert(1)>')
    s['categories']=[{'key':'PTS','name':'PTS','kind':'count','lowerIsBetter':False},{'key':'FG%','name':'FG%','kind':'percentage','lowerIsBetter':False}]
    s['players']=[{'id':'999.p.1','name':'=SUM(1,2)','position':'PG','status':'observed','weeks':2,'stats':{'PTS':10,'FG%':.5,'FGM':5,'FGA':10}}]
    s['quality']['warnings']=['Synthetic test only; no real league data.']
    return s


class ExportTests(unittest.TestCase):
    def test_typed_excel_csv_escape_html_offline_and_non_overwrite(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as tmp:
            export_all(fixture(),tmp)
            with open(Path(tmp)/'season_summary.csv',encoding='utf-8-sig',newline='') as f:
                rows=list(csv.reader(f))
            self.assertEqual(rows[1][1],"'=SUM(1,2)")
            self.assertEqual(float(rows[1][rows[0].index('FG%')]),.5)
            book=load_workbook(Path(tmp)/'fantasy_season.xlsx',data_only=False)
            self.assertEqual(book.sheetnames,SHEETS)
            ws=book[SHEETS[0]];headers=[cell.value for cell in ws[3]]
            cell=ws.cell(4,headers.index('FG%')+1)
            self.assertEqual(cell.value,.5);self.assertEqual(cell.data_type,'n');self.assertEqual(cell.number_format,'0.0%')
            self.assertEqual(ws['B4'].data_type,'s');self.assertEqual(ws['B4'].value,'=SUM(1,2)')
            self.assertEqual(ws.freeze_panes,'F4');self.assertIsNotNone(ws.auto_filter.ref);book.close()
            markup=(Path(tmp)/'fantasy_dashboard.html').read_text(encoding='utf-8')
            self.assertNotIn('</ScRiPt>',markup);self.assertNotIn('<img src=x',markup)
            self.assertNotIn('<script src=',markup);self.assertIn('\\u003c',markup)
            self.assertNotIn('innerHTML',markup);self.assertIn('類別領先者',markup);self.assertIn('MVP',markup)
            self.assertIn('球員走勢',markup);self.assertIn('走勢球員',markup)
            self.assertEqual(json.loads((Path(tmp)/'season.json').read_text(encoding='utf-8'))['players'][0]['stats']['FG%'],.5)
            with self.assertRaises(FileExistsError):export_all(fixture(),tmp)

    def test_csv_formula_prefixes_and_zero(self):
        for value in ['=1',' +SUM(1)','-formula','@SUM(1)','\tcmd','\rtext','\ntext']:
            self.assertTrue(safe_csv(value).startswith("'"))
        self.assertEqual(safe_csv(0),0);self.assertEqual(safe_csv(-2),-2)

    def test_annual_runs_never_reuse_previous_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=new_run(tmp,'2025-26');second=new_run(tmp,'2025-26')
            self.assertNotEqual(first,second);self.assertTrue(first.exists());self.assertTrue(second.exists())

    def test_offline_manual_source_is_not_promoted_to_official(self):
        s=fixture();s['source']['kind']='manual';s['quality']['scope']='roster-week'
        note=source_note(s)
        self.assertIn('manual',note);self.assertIn('roster-week',note)
        self.assertNotIn('官方',note);self.assertNotIn('已驗證',note)

    def test_merged_source_note_fits_chinese_warnings_without_changing_workbook_data(self):
        from openpyxl import load_workbook
        short=fixture();short['quality']['warnings']=[]
        long=fixture();warning='尚未取得每日球員資料，請保留未知數值。'*18+'\n這是提醒文字末尾。'
        long['quality']['warnings']=[warning]
        with tempfile.TemporaryDirectory() as tmp:
            short_dir=Path(tmp)/'short';long_dir=Path(tmp)/'long'
            export_all(short,short_dir);export_all(long,long_dir)
            short_book=load_workbook(short_dir/'fantasy_season.xlsx')
            long_book=load_workbook(long_dir/'fantasy_season.xlsx')
            try:
                self.assertEqual(short_book.sheetnames,SHEETS)
                self.assertEqual(long_book.sheetnames,SHEETS)
                for a,b in zip(short_book,long_book):
                    self.assertEqual(a['A2'].value,source_note(short))
                    self.assertEqual(b['A2'].value,source_note(long))
                    self.assertIn(warning,b['A2'].value)
                    self.assertLessEqual(a.row_dimensions[2].height,40)
                    self.assertLessEqual(b.row_dimensions[2].height,409)
                    self.assertEqual(a.freeze_panes,b.freeze_panes)
                    self.assertEqual(a.auto_filter.ref,b.auto_filter.ref)
                    self.assertEqual(a.print_title_rows,b.print_title_rows)
                    self.assertEqual(str(a.merged_cells),str(b.merged_cells))
                    self.assertEqual(a.max_column,b.max_column)
                    for col in a.column_dimensions:
                        self.assertEqual(a.column_dimensions[col].width,b.column_dimensions[col].width)
                    for row in a:
                        for cell in row:
                            other=b[cell.coordinate]
                            if cell.coordinate!='A2': self.assertEqual(cell.value,other.value)
                            self.assertEqual(cell.data_type,other.data_type)
                            self.assertEqual(cell.number_format,other.number_format)
                narrow=long_book['類別戰績'].row_dimensions[2].height
                self.assertGreater(narrow,140)  # The old fixed 64 pt clipped this note.
                self.assertGreater(narrow,long_book['球員季總'].row_dimensions[2].height)
                ws=long_book[SHEETS[0]];headers=[cell.value for cell in ws[3]]
                pct=ws.cell(4,headers.index('FG%')+1)
                self.assertEqual((pct.value,pct.data_type,pct.number_format),(.5,'n','0.0%'))
            finally:
                short_book.close();long_book.close()
        self.assertEqual(source_note_height('中'*10000,[15]),409)

if __name__=='__main__':unittest.main()
