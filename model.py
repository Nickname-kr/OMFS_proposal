"""Session documents, image normalization, and portable backups (no database)."""
import base64
import hashlib
import json
import re
import warnings
from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image, ImageOps

KINDS = {'plan': '기획서', 'report': '결과보고서'}
FIELDS = {'purpose': '추진 목적', 'details': '주요 진행 내용', 'expected': '기대 효과', 'summary': '주요내용'}
MAX_PHOTOS = 10
MAX_BACKUP = 50 * 1024 * 1024


def new_document():
    return dict(id=str(uuid4()), kind='plan', program='', event_name='학술세미나', subject='', date=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat(), include_time=False, start='09:00', end='10:00', places=[], speakers='', people=[], purpose='', details='', expected='', summary='', budget=[], photos=[])


def copy_document(source):
    d = deepcopy(source)
    d['id'] = str(uuid4())
    d['date'] = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    return d


def normalize_image(raw):
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('사진 한 장은 10MB 이하로 올려 주세요.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(BytesIO(raw)) as im:
            if im.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('JPG, PNG, WebP 사진을 사용해 주세요.')
            if im.width * im.height > 40_000_000:
                raise ValueError('사진 해상도는 4천만 화소 이하로 줄여 주세요.')
            im = ImageOps.exif_transpose(im)
            im.thumbnail((2000, 2000))
            if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
                rgba = im.convert('RGBA')
                image = Image.new('RGB', rgba.size, 'white')
                image.paste(rgba, mask=rgba.getchannel('A'))
            else:
                image = im.convert('RGB')
            out = BytesIO()
            image.save(out, format='JPEG', quality=90)
            return out.getvalue()


def photo_record(raw, name):
    data = normalize_image(raw)
    return dict(id=str(uuid4()), name=name[:200], caption='', data=base64.b64encode(data).decode('ascii'), digest=hashlib.sha256(raw).hexdigest())


def photo_bytes(photo):
    return base64.b64decode(photo['data'], validate=True)


def validate_document(d, required=True):
    if not isinstance(d, dict): raise ValueError('잘못된 문서 데이터입니다.')
    if d.get('kind') not in KINDS: raise ValueError('문서 종류가 올바르지 않습니다.')
    for key in ('id', 'program', 'event_name', 'subject', 'date', 'start', 'end', 'speakers', *FIELDS):
        if not isinstance(d.get(key), str) or len(d[key]) > 45000:
            raise ValueError('문서의 텍스트 형식 또는 길이를 확인해 주세요.')
    date.fromisoformat(d['date'])
    if not isinstance(d.get('include_time'), bool): raise ValueError('시간 설정이 올바르지 않습니다.')
    for t in (d['start'], d['end']):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t): raise ValueError('시간 형식이 올바르지 않습니다.')
    if d['include_time'] and d['end'] <= d['start']: raise ValueError('종료 시간은 시작 시간 이후로 입력해 주세요.')
    for key in ('places', 'people'):
        if not isinstance(d.get(key), list) or len(d[key]) > 200 or any(not isinstance(x,str) or len(x)>500 for x in d[key]): raise ValueError('참석자 또는 장소 목록을 확인해 주세요.')
    if required and (not d['event_name'].strip() or not d['places'] or not d['people']):
        raise ValueError('행사명, 장소, 참석 대상을 입력해 주세요.')
    required_fields = ['purpose', 'details', 'expected'] if d['kind']=='plan' else ['summary']
    if required and any(not d[k].strip() for k in required_fields): raise ValueError('문서 내용의 필수 항목을 입력해 주세요.')
    if not isinstance(d.get('budget'), list) or len(d['budget']) > 50: raise ValueError('예산은 50개 항목까지 입력할 수 있습니다.')
    for row in d['budget']:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or len(row[k])>500 for k in ('item','note')): raise ValueError('예산 항목을 확인해 주세요.')
        amount = row.get('amount')
        if type(amount) is not int or not 0 <= amount <= 1_000_000_000_000: raise ValueError('예산 금액은 0 이상 정수로 입력해 주세요.')
        if not row['item'].strip(): raise ValueError('예산 항목 이름을 입력해 주세요.')
    if not isinstance(d.get('photos'), list) or len(d['photos']) > MAX_PHOTOS: raise ValueError('사진은 문서당 10장까지 가능합니다.')
    ids = []
    for p in d['photos']:
        if not isinstance(p,dict) or any(not isinstance(p.get(k),str) for k in ('id','name','caption','data','digest')): raise ValueError('사진 데이터가 올바르지 않습니다.')
        if len(p['caption'])>500 or len(p['name'])>200: raise ValueError('사진 설명이 너무 깁니다.')
        normalize_image(photo_bytes(p))
        ids.append(p['id'])
    if len(ids)!=len(set(ids)): raise ValueError('사진 ID가 중복되었습니다.')


def make_backup(draft, documents, phrases):
    payload = dict(format='proposal-writer', version=1, draft=draft, documents=documents, phrases=phrases)
    content=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    if len(content)>MAX_BACKUP: raise ValueError('백업이 50MB를 넘습니다. 보관 문서를 줄여 주세요.')
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z: z.writestr('workspace.json',content)
    return out.getvalue()


def read_backup(raw):
    if len(raw)>MAX_BACKUP: raise ValueError('백업은 50MB 이하만 불러올 수 있습니다.')
    with ZipFile(BytesIO(raw)) as z:
        if z.namelist()!=['workspace.json']: raise ValueError('이 앱에서 내려받은 백업 ZIP을 선택해 주세요.')
        if z.getinfo('workspace.json').file_size>MAX_BACKUP: raise ValueError('압축 해제 데이터가 너무 큽니다.')
        p=json.loads(z.read('workspace.json'))
    if p.get('format')!='proposal-writer' or p.get('version')!=1: raise ValueError('지원하지 않는 백업 형식입니다.')
    validate_document(p['draft'],False)
    if not isinstance(p['documents'],list) or len(p['documents'])>100: raise ValueError('보관 문서는 100건까지 가능합니다.')
    for d in p['documents']: validate_document(d,False)
    ids=[d['id'] for d in p['documents']]
    if len(ids)!=len(set(ids)): raise ValueError('문서 ID가 중복되었습니다.')
    if not isinstance(p['phrases'],list) or len(p['phrases'])>500: raise ValueError('상용구는 500개까지 가능합니다.')
    for f in p['phrases']:
        if not isinstance(f,dict) or any(not isinstance(f.get(k),str) for k in ('id','name','category','content')) or f['category'] not in FIELDS or not f['name'].strip() or not f['content'].strip() or len(f['content'])>45000 or len(f['name'])>200:
            raise ValueError('상용구 데이터를 확인해 주세요.')
    if len({f['id'] for f in p['phrases']})!=len(p['phrases']): raise ValueError('상용구 ID가 중복되었습니다.')
    return p
