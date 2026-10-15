"""Word templates and matching print-oriented A4 PDF output."""
from io import BytesIO
from pathlib import Path
from copy import deepcopy
from html import escape
from datetime import date
import re
import unicodedata
import threading
from model import KINDS, photo_bytes
ROOT=Path(__file__).parent
LOCK=threading.Lock()


def clean(v):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',unicodedata.normalize('NFC',str(v or '')))


def title(d):
    return '\n'.join(v for v in [d['program'].strip(), d['event_name'].strip()+' '+KINDS[d['kind']]] if v)


def date_label(d):
    dt=date.fromisoformat(d['date'])
    value=dt.strftime('%Y.%m.%d') if d['kind']=='plan' else f'{dt.year}. {dt.month}. {dt.day}.'
    return value + (f"  {d['start']} ~ {d['end']}" if d['include_time'] else '')


def filename(d):
    return d['date']+'_'+re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',d['event_name'])[:50]+'_'+KINDS[d['kind']]


def values(d):
    return dict(title=title(d),purpose=d['purpose'],subject=d['subject'],date=date_label(d),places=', '.join(d['places']),speakers=d['speakers'],people=', '.join(d['people']),details=d['details'],expected=d['expected'],summary=d['summary'])


def set_text(p,text):
    props=deepcopy(p.runs[0]._r.rPr) if p.runs and p.runs[0]._r.rPr is not None else None
    p.clear()
    r=p.add_run(clean(text))
    if props is not None: r._r.insert(0,props)


def make_docx(d):
    from docx import Document
    from docx.shared import Mm,Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc=Document(ROOT/'templates'/f"{d['kind']}.docx")
    fields=values(d)
    for p in doc.paragraphs:
        text=p.text
        for k,v in fields.items(): text=text.replace('{{'+k+'}}',clean(v) or '—')
        if text!=p.text:
            set_text(p,text)
            if d['kind']=='report' and text.startswith('주요내용:'):p.alignment=0
        if p.text.startswith('행사사진'):p.paragraph_format.keep_with_next=bool(d['photos'])
    if d['kind']=='plan':
        table=doc.tables[0]
        prototype=deepcopy(table.rows[1]._tr)
        for row in list(table.rows)[1:]: table._tbl.remove(row._tr)
        rows=[(r['item'],f"{r['amount']:,}원",r['note']) for r in d['budget']]
        rows.append(('합계',f"{sum(r['amount'] for r in d['budget']):,}원",''))
        for i,row_values in enumerate(rows):
            table._tbl.append(deepcopy(prototype))
            row=table.rows[-1]
            for c,v in zip(row.cells,row_values):
                set_text(c.paragraphs[0],v)
                for run in c.paragraphs[0].runs: run.bold=i==len(rows)-1
        trpr=table.rows[0]._tr.get_or_add_trPr()
        repeat=OxmlElement('w:tblHeader');trpr.append(repeat)
    if d['photos']:
        if d['kind']=='plan':
            p=doc.add_paragraph('첨부사진');p.runs[0].bold=True;p.paragraph_format.space_before=Pt(12);p.paragraph_format.keep_with_next=True
        for photo in d['photos']:
            from PIL import Image
            raw=photo_bytes(photo)
            with Image.open(BytesIO(raw)) as im: w,h=im.size
            scale=min(160/w,95/h)
            p=doc.add_paragraph();p.alignment=1
            p.paragraph_format.keep_with_next=bool(photo['caption'])
            p.add_run().add_picture(BytesIO(raw),width=Mm(w*scale),height=Mm(h*scale))
            if photo['caption']:
                p=doc.add_paragraph(clean(photo['caption']));p.alignment=1
                p.paragraph_format.space_after=Pt(12)
                for r in p.runs:r.font.size=Pt(9)
    doc.core_properties.title=clean(title(d))
    doc.core_properties.author='';doc.core_properties.last_modified_by=''
    out=BytesIO();doc.save(out);return out.getvalue()


def make_pdf(d):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, KeepTogether
    from PIL import Image
    with LOCK:
        for face,file in [('Myeongjo','NanumMyeongjo-Regular.ttf'),('MyeongjoBold','NanumMyeongjo-Bold.ttf')]:
            if face not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(face,str(ROOT/'assets/fonts'/file)))
    body=ParagraphStyle('body',fontName='Myeongjo',fontSize=10,leading=14,wordWrap='CJK',spaceAfter=5 if d['kind']=='plan' else 12)
    heading=ParagraphStyle('heading',parent=body,fontName='MyeongjoBold',keepWithNext=True,spaceBefore=12,spaceAfter=5)
    titl=ParagraphStyle('title',parent=body,fontName='MyeongjoBold',fontSize=14,leading=20,alignment=1,spaceAfter=20,keepWithNext=True)
    caption=ParagraphStyle('caption',parent=body,fontSize=9,leading=12,alignment=1,spaceAfter=12)
    def p(v,style=body): return Paragraph(escape(clean(v) or '—').replace('\n','<br/>'),style)
    # Split long text into lines so content can flow over pages.
    def prose(v): return [p(line) for line in (clean(v) or '—').split('\n')]
    f=values(d);story=[p(f['title'],titl)]
    if d['kind']=='plan':
        story += [p('1. 추진 목적',heading),*prose(f['purpose']),p('2. 행사 개요',heading),p('주제: '+(f['subject'] or '—'))]
    for label,k in [('일시','date'),('장소','places'),('초청 연자','speakers'),('참석 대상','people')]: story.append(p(label+': '+(f[k] or '—')))
    if d['kind']=='plan':
        story += [p('3. 주요 진행 내용',heading),*prose(f['details']),p('4. 예산',heading)]
        cell=ParagraphStyle('cell',parent=body,spaceAfter=0,leading=13,alignment=1)
        rows=[[p(v,cell) for v in ['항목','금액','재원 및 비고']]]
        rows += [[p(r['item'],cell),p(f"{r['amount']:,}원",cell),p(r['note'],cell)] for r in d['budget']]
        bold=ParagraphStyle('total',parent=cell,fontName='MyeongjoBold')
        rows += [[p('합계',bold),p(f"{sum(r['amount'] for r in d['budget']):,}원",bold),p('',cell)]]
        table=Table(rows,colWidths=[50.5*mm,50.5*mm,65*mm],repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#D9D9D9')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EEEEEE')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story += [table,p('5. 기대 효과',heading),*prose(f['expected'])]
        
    else:
        story += prose('주요내용: '+(f['summary'] or '—'))
        if not d['photos']:story.append(p('행사사진: ',heading))
    for i,photo in enumerate(d['photos']):
        raw=photo_bytes(photo)
        with Image.open(BytesIO(raw)) as im:w,h=im.size
        scale=min(160*mm/w,95*mm/h)
        picture=RLImage(BytesIO(raw),width=w*scale,height=h*scale)
        group=([p('첨부사진' if d['kind']=='plan' else '행사사진:',heading)] if i==0 else [])+[picture,Spacer(1,4*mm)]
        if photo['caption']:group.append(p(photo['caption'],caption))
        story.append(KeepTogether(group))
    out=BytesIO();margin=20 if d['kind']=='plan' else 22
    SimpleDocTemplate(out,pagesize=A4,leftMargin=22*mm,rightMargin=22*mm,topMargin=margin*mm,bottomMargin=margin*mm,title=clean(title(d)),author='').build(story)
    return out.getvalue()
