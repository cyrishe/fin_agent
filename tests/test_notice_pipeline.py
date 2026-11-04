import ast
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import notice_pipeline as n


def prepared(text='公司A向银行B借款100万元，期限一年。\n发布日期2026年9月30日。'):
    return dict(source={'id': 1, 'url': 'https://example.org/1.pdf', 'title': '借款公告',
                        'pub_time': '2026-09-30'}, content_text=text, content_hash=n.digest(text),
                observed_at='2026-10-02T00:00:00+00:00', parser_version='fixture')


def payload():
    return dict(issuer_name='公司A', business_category='融资', summary='公司A借款100万元。', events=[{
        'title': '借款', 'summary': '借款100万元，期限一年。', 'evidence': [1],
        'metrics': [{'name': '借款金额', 'raw': '100万元', 'value': '100', 'unit': '万元', 'evidence': [1]}],
        'participants': [{'name': '银行B', 'role': '出借方', 'evidence': [1]}]}])


def test_text_segmentation_is_lossless_and_evidence_has_real_pages():
    text=('一行正文\n' * 800) + '尾部'
    p=prepared(text); parts=n.paragraphs(text)
    assert ''.join(a['text'] for a in parts)==text
    p['pages']=[{'page':1,'start':0,'end':1300},{'page':2,'start':1300,'end':len(text)}]
    result=n.evidence([2,1,2],parts,p)
    assert len(result['source_locator']['spans'])==2
    assert result['source_locator']['spans'][0]['physical_pages']==[1,2]
    assert result['source_quote']=='\n[…]\n'.join(text[x['start']:x['end']] for x in result['source_locator']['spans'])


def test_normal_optional_unknown_fields_and_zero_are_compatible():
    p=payload();p['future_optional_field']='ignored'
    f=p['events'][0]['metrics'][0];f['value']='0';f['as_of_date']='2026-09-30'
    result=n.normalize(p,prepared())
    assert result['facts'][0]['value_num']=='0'
    assert result['facts'][0]['metric_code'] is None
    assert result['facts'][0]['period_start'] is None
    assert result['events'][0]['event_date'] is None
    assert result['participants'][0]['event_no']==1


def test_range_null_and_non_numeric_business_event():
    p=payload();f=p['events'][0]['metrics'][0]
    f.update(value=None,lower='20',upper='45',unit='年')
    result=n.normalize(p,prepared());assert result['facts'][0]['value_num'] is None
    p['events'][0]['metrics']=[]
    assert n.normalize(p,prepared())['facts']==[]
    assert n.normalize({'summary':'仅会议网络投票操作说明','ignore_reason':'无业务信息','events':[]},prepared())['events']==[]


@pytest.mark.parametrize('mutate',[
    lambda p:p['events'][0].update(evidence=[999]),
    lambda p:p['events'][0]['metrics'][0].update(value='NaN'),
    lambda p:p['events'][0]['metrics'][0].update(value='1e30'),
    lambda p:p['events'][0]['metrics'][0].update(lower=20,upper=10),
    lambda p:p['events'][0]['metrics'][0].update(period_start='2026-10-02',period_end='2026-09-01'),
    lambda p:p.update(ignore_reason='低价值'),
])
def test_only_unstorable_or_inconsistent_contract_rejected(mutate):
    p=payload();mutate(p)
    with pytest.raises(ValueError):n.normalize(p,prepared())


def test_rows_keep_document_ownership_and_do_not_require_model_ids():
    p=prepared();r={'prepared':p,'extraction':n.normalize(payload(),p),'version_id':'a'*64,
                  'prompt_hash':'b'*64,'model':'test','completed_at':'2026-10-02T00:00:00+00:00'}
    rows=n.rows_for(r,'test')
    assert rows['notice_metric_facts'][0]['event_id']==rows['notice_events'][0]['event_id']
    assert rows['notice_participants'][0]['event_id']==rows['notice_events'][0]['event_id']
    assert all(x['document_version_id']=='a'*64 for t in n.TABLES[:3] for x in rows[t])
    assert rows['notice_documents'][0]['batch_id']=='test'


def test_one_repair_then_stop_and_log_every_call():
    class Fake:
        max_tokens=1000
        def __init__(self):self.count=0
        def chat(self,*args,**kwargs):
            self.count+=1
            return ('{' if self.count==1 else __import__('json').dumps(payload())), {'finish_reason':'stop'}
    f=Fake();logs=[]
    result,calls=n.extract(prepared(),f,'prompt',on_call=lambda c:logs.append(len(c)))
    assert len(result['events'])==1 and logs==[1,2] and f.count==2
    class Bad(Fake):
        def chat(self,*a,**k):self.count+=1;return '{}',{'finish_reason':'length'}
    b=Bad()
    with pytest.raises(ValueError,match='one repair'):n.extract(prepared(),b,'prompt')
    assert b.count==2
    with pytest.raises(ValueError,match='no silent truncation'):n.extract(prepared(),f,'prompt',max_chars=2)


def test_env_file_pg_user_not_shadowed_by_os_user(tmp_path,monkeypatch):
    p=tmp_path/'env';p.write_text('HOST=db\nUSER=db_reader\nPASSWORD=private\nDB_NAME=notice\n')
    monkeypatch.setenv('USER','local_login');monkeypatch.setenv('LLM_DEFAULT_MODEL','runtime-model')
    cfg=n.settings(p)
    assert cfg['NOTICE_PG_USER']=='db_reader'
    assert cfg['LLM_DEFAULT_MODEL']=='runtime-model'


def test_python39_syntax_and_source_loading_has_no_app_imports():
    source=Path(n.__file__).read_text()
    ast.parse(source,feature_version=(3,9))
    assert 'from src.' not in source


def test_transaction_is_atomic_and_replay_does_not_write(monkeypatch):
    class Connection:
        def __init__(self, exists=False, fail=False):
            self.exists,self.fail=exists,fail
            self.inserts=[];self.committed=self.rolled_back=self.closed=False
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,params):
            if sql.startswith('INSERT'):
                self.inserts.append(sql)
                if self.fail and 'notice_metric_facts' in sql:raise RuntimeError('write failed')
        def fetchone(self):return ('existing',) if self.exists else None
        def commit(self):self.committed=True
        def rollback(self):self.rolled_back=True
        def close(self):self.closed=True
    p=prepared();r={'prepared':p,'extraction':n.normalize(payload(),p),'version_id':'a'*64,
                  'prompt_hash':'b'*64,'model':'test','completed_at':'2026-10-02T00:00:00+00:00'}
    for exists,fail in [(False,False),(True,False),(False,True)]:
        conn=Connection(exists,fail);monkeypatch.setattr(n,'mysql_connection',lambda _:conn)
        if fail:
            with pytest.raises(RuntimeError):n.write_result(r,'test',{})
            assert conn.rolled_back and not conn.committed
        else:
            assert n.write_result(r,'test',{}) is (not exists)
            assert bool(conn.inserts) is (not exists)
            assert conn.committed is (not exists)
        assert conn.closed


def test_pdf_merged_cell_keeps_its_two_row_scope():
    pytest.importorskip('pdfplumber')
    # Real vector PDF: the right-hand cell spans the A/B rows. No business-specific fill rule.
    stream=(b'0.5 w 40 600 400 90 re S 240 600 m 240 690 l S '
            b'40 660 m 440 660 l S 40 630 m 240 630 l S '
            b'BT /F1 12 Tf 50 670 Td (Class) Tj 200 0 Td (Date) Tj ET '
            b'BT /F1 12 Tf 50 640 Td (A) Tj 0 -30 Td (B) Tj ET '
            b'BT /F1 12 Tf 250 645 Td (2026/10/14) Tj ET')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
             b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
             b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'
    data+=b''.join(('%010d 00000 n \n'%i).encode() for i in offsets[1:])
    data+=b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF'
    text,pages=n.pdf_text(data)
    assert '行2-3 列2-2：2026/10/14' in text
    assert '行3-3 列1-1：B' in text
    assert pages==[{'page':1,'start':0,'end':len(text)}]


def test_provider_request_disables_reasoning_and_preserves_decimal_precision(monkeypatch):
    import json
    import io
    def respond(req,**kwargs):
        sent=json.loads(req.data)
        assert sent['enable_thinking'] is False
        assert sent['response_format']=={'type':'json_object'}
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
                                      'model':'test','usage':{'total_tokens':12}}).encode())
    monkeypatch.setattr(n,'urlopen',respond)
    content,trace=n.LLM({'LLM_BASE_URL':'https://example.org/v1','LLM_API_KEY':'test','LLM_DEFAULT_MODEL':'test'}).chat('x','y')
    assert content=='{}' and trace['usage']['total_tokens']==12
    assert n.decimal_value(Decimal('44993054.01'))=='44993054.01'
    assert n.decimal_value('9999999999999999999999.12345678')=='9999999999999999999999.12345678'


def test_image_only_pdf_is_transcribed_or_fails_explicitly():
    pytest.importorskip('pdfplumber')
    from PIL import Image
    import io
    data=io.BytesIO();Image.new('RGB',(150,200),'white').save(data,format='PDF')
    with pytest.raises(ValueError,match='image transcription'):
        n.pdf_text(data.getvalue())
    seen=[]
    def transcribe(page,number):
        seen.append(number);return '资产总计 447262974.90 元'
    text,pages=n.pdf_text(data.getvalue(),ocr=transcribe)
    assert seen==[1] and '447262974.90' in text
    assert pages[0]['image_transcribed'] and pages[0]['end']==len(text)
