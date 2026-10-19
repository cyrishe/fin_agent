"""Independent OOXML readback of every combined R35 workbook cell."""
from datetime import datetime
import json
import math
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

from scripts.experiment_b2_two_month_2024_r35 import OUT
from scripts.replay_limit_rebound_cash_r23 import dump, sha

FILE = Path('docs/stock_automl_runs/20261019_b2_both_2024_r35/B2_一月两月验证_2024起全期_R35_20261019.xlsx')
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def column(i):
    result = ''
    i += 1
    while i:
        i, j = divmod(i-1, 26)
        result = chr(65+j) + result
    return result


def main():
    expected = json.loads((OUT / 'workbook_data.json').read_text())
    cells_checked, formulas, rows = 0, 0, {}
    with zipfile.ZipFile(FILE) as z:
        assert z.testzip() is None
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        relations = {x.attrib['Id']: x.attrib['Target'] for x in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets = ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets', NS)
        assert [s.attrib['name'] for s in sheets] == list(expected)
        for sheet in sheets:
            name = sheet.attrib['name']
            path = relations[sheet.attrib['{' + REL + '}id']]
            path = path.lstrip('/') if path.startswith('/') else 'xl/' + path
            root = ET.fromstring(z.read(path))
            data = {}
            for c in root.findall('.//s:sheetData/s:row/s:c', NS):
                kind = c.attrib.get('t')
                node = c.find('s:v', NS)
                value = node.text if node is not None else None
                assert kind != 'e', (name, c.attrib['r'], value)
                if kind == 's':
                    value = strings[int(value)]
                elif kind == 'inlineStr':
                    value = ''.join(c.find('s:is', NS).itertext()) if c.find('s:is', NS) is not None else None
                elif kind in ('str', 'd'):
                    pass
                elif kind == 'b':
                    value = int(value) if value else None
                else:
                    value = float(value) if value else None
                data[c.attrib['r']] = value
                if c.find('s:f', NS) is not None:
                    formulas += 1
            content = expected[name]
            for i, heading in enumerate(content['headers']):
                assert data.get(column(i) + '5') == heading
            for rownum, row in enumerate(content['rows'], 6):
                for i, wanted in enumerate(row):
                    got = data.get(column(i) + str(rownum))
                    location = (name, column(i) + str(rownum), wanted, got)
                    if wanted is None:
                        assert got is None or got == '', location
                    elif i in content['dates'] and isinstance(wanted, str) and len(wanted) == 10 and wanted[4] == '-':
                        assert (got[:10] == wanted if isinstance(got, str) else
                                got == (datetime.fromisoformat(wanted) - datetime(1899, 12, 30)).days), location
                    elif isinstance(wanted, (int, float)):
                        assert isinstance(got, (int, float)) and math.isclose(got, wanted, rel_tol=1e-12, abs_tol=1e-8), location
                    else:
                        assert wanted == got, location
                    cells_checked += 1
            rows[name] = len(content['rows'])
        tables = [n for n in z.namelist() if n.startswith('xl/tables/') and n.endswith('.xml')]
        assert len(tables) == len(expected)
    receipt = dict(file=str(FILE), sha256=sha(FILE), bytes=FILE.stat().st_size,
                   verified_data_cells=cells_checked, cached_formulas=formulas,
                   sheet_rows=rows, zip_integrity=True, formula_errors=0,
                   date_types_checked=True)
    dump(OUT / 'workbook_verification.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
