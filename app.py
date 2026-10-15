from copy import deepcopy
from datetime import date,time
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import hmac
import streamlit as st
from model import KINDS,FIELDS,new_document,copy_document,photo_record,photo_bytes,validate_document,make_backup,read_backup
from exports import make_docx,make_pdf,title,filename

st.set_page_config(page_title='기획서 작성 프로그램',page_icon='📝',layout='centered')
st.markdown('''<style>
.stApp{background:#F4F7FA;color:#172B43}.block-container{max-width:920px;padding-top:2.7rem;padding-bottom:3rem}
h1,h2,h3{color:#172B43;letter-spacing:-.04em}h1{font-size:2.2rem!important}
[data-testid="stVerticalBlockBorderWrapper"]>div{background:white;border-radius:18px}
.stButton>button,.stDownloadButton>button{border-radius:10px;min-height:44px;font-weight:600}
.eyebrow{font-size:12px;font-weight:700;letter-spacing:.18em;color:#158477;margin-bottom:9px}
.hero-sub{color:#65788B;margin:0 0 24px;font-size:15px}.steps{display:flex;gap:8px;margin:20px 0 24px}
.step{flex:1;padding:12px 4px;background:#E8EEF3;border-radius:10px;text-align:center;color:#708090;font-size:13px}
.step.active{background:#137F75;color:white;font-weight:700}.step.done{background:#DCEFEA;color:#116B62}
@media(max-width:550px){.block-container{padding-top:1.4rem}.step{font-size:11px}.steps{gap:4px}h1{font-size:1.8rem!important}}
</style>''',unsafe_allow_html=True)
s=st.session_state
try:password=st.secrets.get('app_password')
except FileNotFoundError:password=None
if password and not s.get('authenticated'):
    st.title('기획서 작성 프로그램')
    with st.form('login'):
        entered=st.text_input('접속 비밀번호',type='password')
        if st.form_submit_button('입장하기'):
            if hmac.compare_digest(entered.encode(),str(password).encode()):s.authenticated=True;st.rerun()
            else:st.error('비밀번호를 확인해 주세요.')
    st.stop()
for key in list(s):
    if key.startswith(('input_','phrase_name_','phrase_mode_')):s[key]=s[key]
if 'draft' not in s:s.draft=new_document()
for key in ['documents','phrases']:
    if key not in s:s[key]=[]
if 'step' not in s:s.step=0
if 'upload_epoch' not in s:s.upload_epoch=0
settings=json.loads((Path(__file__).parent/'settings.json').read_text(encoding='utf-8'))


def reset(source=None):
    for k in list(s):
        if k.startswith(('input_','phrase_name_','phrase_mode_','caption_')):del s[k]
    s.draft=copy_document(source) if source else new_document()
    s.step=0;s.upload_epoch+=1;s.pop('export',None)


def preview(d):
    st.subheader(title(d))
    st.write('일시: '+d['date']+(f" · {d['start']}–{d['end']}" if d['include_time'] else ''))
    st.write('장소: '+', '.join(d['places']))
    st.write('초청 연자: '+(d['speakers'] or '—'))
    st.write('참석 대상: '+', '.join(d['people']))
    for k in (['purpose','details','expected'] if d['kind']=='plan' else ['summary']):
        st.markdown('**'+FIELDS[k]+'**');st.text(d[k] or '—')
    if d['kind']=='plan':
        st.write('주제: '+(d['subject'] or '—'))
        if d['budget']:st.table([{'항목':r['item'],'금액':f"{r['amount']:,}원",'재원 및 비고':r['note']} for r in d['budget']])
        st.write(f"예산 합계: {sum(r['amount'] for r in d['budget']):,}원")
    for p in d['photos']:st.image(photo_bytes(p),caption=p['caption'] or p['name'],width=400)


def downloads(d,prefix):
    signature=hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if st.button('PDF·Word 파일 준비',key=prefix+'_prepare',type='primary',use_container_width=True):
        with st.spinner('사진을 포함한 문서를 만드는 중입니다…'):
            result={};errors=[]
            for ext,func in [('pdf',make_pdf),('docx',make_docx)]:
                try:result[ext]=func(d)
                except Exception as exc:errors.append(f'{ext.upper()} 생성 오류 ({type(exc).__name__}) — templates와 assets 폴더를 확인해 주세요.')
            s.export=dict(signature=signature,files=result,errors=errors)
    bundle=s.get('export',{})
    if bundle.get('signature')==signature:
        for e in bundle['errors']:st.error(e)
        a,b=st.columns(2)
        for col,ext,label,mime in [(a,'pdf','PDF 다운로드 · 인쇄용','application/pdf'),(b,'docx','Word 다운로드 · 편집용','application/vnd.openxmlformats-officedocument.wordprocessingml.document')]:
            if ext in bundle['files']:
                with col:st.download_button(label,bundle['files'][ext],file_name=filename(d)+'.'+ext,mime=mime,key=prefix+'_'+ext,use_container_width=True)


def insert_phrase(field,content,mode):
    old=s.get('input_'+field,s.draft[field])
    value='\n\n'.join(v for v in [old.rstrip(),content.strip()] if v) if mode=='뒤에 추가' else content
    if len(value)>45000:s.notice='삽입 후 45,000자를 초과합니다.';return
    s['input_'+field]=value;s.draft[field]=value


def phrase_input(field):
    label=FIELDS[field]
    options={p['id']:p for p in s.phrases if p['category']==field}
    with st.expander(label+' · 상용구 불러오기'):
        if options:
            chosen=st.selectbox('상용구 선택',list(options),index=None,format_func=lambda k,opts=options:opts[k]['name'],key='pick_'+field)
            if chosen:
                st.text(options[chosen]['content'])
                mode=st.radio('삽입 방식',['뒤에 추가','내용 교체'],horizontal=True,key='phrase_mode_'+field)
                confirm=st.checkbox('현재 내용을 교체합니다',key='confirm_'+field+'_'+chosen) if mode=='내용 교체' else True
                st.button('삽입',key='insert_'+field,disabled=not confirm,on_click=insert_phrase,args=(field,options[chosen]['content'],mode))
        else:st.caption('아래 내용을 작성한 뒤 상용구로 저장해 보세요.')
    s.draft[field]=st.text_area(label+' *',value=s.draft[field],height=150,max_chars=45000,key='input_'+field)
    with st.expander(label+' · 상용구로 저장'):
        name=st.text_input('상용구 이름',key='phrase_name_'+field,max_chars=200)
        if st.button('상용구로 저장',key='save_phrase_'+field):
            if not name.strip() or not s.draft[field].strip():st.error('이름과 내용을 입력해 주세요.')
            elif len(s.phrases)>=500:st.error('상용구는 500개까지 보관할 수 있습니다.')
            elif any(p['name']==name.strip() and p['category']==field for p in s.phrases):st.error('같은 분류에 동일한 이름이 있습니다.')
            else:s.phrases.append(dict(id=str(uuid4()),name=name.strip(),category=field,content=s.draft[field]));s.notice='상용구를 현재 세션에 저장했습니다. 보관하려면 백업을 다운로드하세요.';st.rerun()

st.markdown('<div class="eyebrow">PROPOSAL / WORKSPACE</div>',unsafe_allow_html=True)
st.title('기획서 작성 프로그램')
st.markdown('<p class="hero-sub">행사의 시작부터 마무리까지, 필요한 내용을 한곳에.</p>',unsafe_allow_html=True)
st.caption('외부 저장소 연결 없이 사용합니다. 종료 전 「백업·복원」에서 사진·문서·상용구를 내려받으세요.')
page=st.radio('메뉴',['새 문서','보관 문서','상용구 관리','백업·복원'],horizontal=True,label_visibility='collapsed',key='navigation')
if s.get('notice'):st.info(s.pop('notice'))

if page=='백업·복원':
    st.subheader('다음에도 이어서 작성하기')
    st.write('현재 초안, 보관 문서, 상용구, 첨부사진을 하나의 ZIP에 담습니다. 브라우저 새로고침·종료 또는 서버 재시작 시 세션 자료가 사라질 수 있습니다.')
    if st.button('백업 파일 준비',type='primary'):
        try:s.backup=make_backup(s.draft,s.documents,s.phrases)
        except ValueError as e:st.error(str(e))
    if s.get('backup'):st.download_button('준비한 백업 ZIP 다운로드',s.backup,'기획서_작업백업.zip','application/zip');st.caption('내용을 수정했다면 「백업 파일 준비」를 다시 눌러 최신 파일을 받으세요.')
    uploaded=st.file_uploader('이 앱의 백업 ZIP 불러오기',type=['zip'])
    approved=st.checkbox('현재 자료 전체를 백업 내용으로 교체합니다')
    if st.button('백업 복원',disabled=not uploaded or not approved):
        try:
            data=read_backup(uploaded.getvalue())
            reset();s.draft=data['draft'];s.documents=data['documents'];s.phrases=data['phrases'];s.pop('backup',None)
            s.notice='백업을 복원했습니다. 새 문서 메뉴에서 이어서 작성하세요.';st.rerun()
        except Exception as e:st.error('백업을 불러오지 못했습니다. 올바른 앱 백업 파일인지 확인해 주세요. '+(str(e) if isinstance(e,ValueError) else ''))
    st.stop()

if page=='상용구 관리':
    st.subheader('자주 쓰는 문구를 한곳에')
    with st.expander('＋ 새 상용구 등록'):
        with st.form('new_phrase',clear_on_submit=True):
            name=st.text_input('이름',max_chars=200);category=st.selectbox('분류',list(FIELDS),format_func=FIELDS.get);content=st.text_area('내용',max_chars=45000)
            if st.form_submit_button('등록'):
                if not name.strip() or not content.strip():st.error('이름과 내용을 입력하세요.')
                elif len(s.phrases)>=500:st.error('상용구는 500개까지 가능합니다.')
                elif any(p['name']==name.strip() and p['category']==category for p in s.phrases):st.error('같은 분류에 동일한 이름이 있습니다.')
                else:s.phrases.append(dict(id=str(uuid4()),name=name.strip(),category=category,content=content));st.rerun()
    query=st.text_input('상용구 검색')
    filt=st.selectbox('분류 필터',['all',*FIELDS],format_func=lambda x:'전체' if x=='all' else FIELDS[x])
    for p in list(s.phrases):
        if (filt!='all' and filt!=p['category']) or query.casefold() not in (p['name']+' '+p['content']).casefold():continue
        with st.expander(FIELDS[p['category']]+' · '+p['name']):
            with st.form('edit_'+p['id']):
                name=st.text_input('이름 수정',value=p['name'],max_chars=200)
                category=st.selectbox('분류 수정',list(FIELDS),index=list(FIELDS).index(p['category']),format_func=FIELDS.get)
                content=st.text_area('내용 수정',value=p['content'],max_chars=45000)
                if st.form_submit_button('수정 저장'):
                    if not name.strip() or not content.strip():st.error('이름과 내용을 입력하세요.')
                    elif any(x['id']!=p['id'] and x['name']==name.strip() and x['category']==category for x in s.phrases):st.error('같은 분류에 동일한 이름이 있습니다.')
                    else:p.update(name=name.strip(),content=content,category=category);st.rerun()
            confirm=st.checkbox('이 상용구를 삭제합니다',key='delete_check_'+p['id'])
            if st.button('삭제',key='delete_'+p['id'],disabled=not confirm):s.phrases=[x for x in s.phrases if x['id']!=p['id']];st.rerun()
    st.stop()

if page=='보관 문서':
    query=st.text_input('문서 검색',placeholder='행사명·프로그램·참석자 검색')
    if not s.documents:st.info('마지막 단계에서 보관한 문서가 여기에 표시됩니다.')
    for d in reversed(s.documents):
        if query.casefold() not in (title(d)+' '+', '.join(d['people'])).casefold():continue
        with st.expander(d['date']+' · '+d['event_name']+' '+KINDS[d['kind']]):
            preview(d);downloads(d,'history_'+d['id'])
            if st.button('새 초안으로 복사',key='copy_'+d['id']):
                s.pending_copy=deepcopy(d)
            if s.get('pending_copy',{}).get('id')==d['id']:
                if st.checkbox('현재 초안을 바꿉니다',key='copy_ok_'+d['id']):
                    if st.button('복사 적용',key='copy_apply_'+d['id']):reset(d);s.pop('pending_copy',None);s.notice='새 문서 메뉴에서 복사한 초안을 확인하세요.';st.rerun()
            confirm=st.checkbox('보관 문서를 삭제합니다',key='doc_delete_check_'+d['id'])
            if st.button('문서 삭제',key='doc_delete_'+d['id'],disabled=not confirm):s.documents=[x for x in s.documents if x['id']!=d['id']];st.rerun()
    st.stop()

labels=['기본 정보','참석자·장소','내용·예산','사진','확인·출력']
st.markdown('<div class="steps">'+''.join(f'<div class="step {"active" if i==s.step else "done" if i<s.step else ""}">{i+1} · {v}</div>' for i,v in enumerate(labels))+'</div>',unsafe_allow_html=True)
d=s.draft
if s.step==0:
    with st.expander('이전 문서에서 시작'):
        if s.documents:
            options={x['id']:x for x in s.documents}
            chosen=st.selectbox('불러올 문서',list(options),index=None,format_func=lambda k:options[k]['date']+' · '+title(options[k]).replace('\n',' '))
            fields=st.multiselect('불러올 항목',['program','event_name','subject','places','speakers','people','purpose','details','expected','summary','budget','photos'],default=['program','event_name','subject','places','speakers','people','purpose','details','expected','summary','budget'],format_func=lambda x:{'program':'프로그램명','event_name':'행사명','subject':'주제','places':'장소','speakers':'초청 연자','people':'참석 대상','budget':'예산','photos':'사진',**FIELDS}[x])
            approved=st.checkbox('선택한 항목으로 새 초안을 만들고 현재 초안을 교체합니다')
            if st.button('초안으로 불러오기',disabled=not chosen or not approved or not fields):
                src=options[chosen];reset();s.draft['kind']=src['kind']
                for k in fields:s.draft[k]=deepcopy(src[k])
                st.rerun()
        else:st.caption('문서를 보관한 뒤 다시 사용할 수 있습니다.')
with st.container(border=True):
    st.caption(f'STEP {s.step+1:02d} / 05')
    if s.step==0:
        st.subheader('어떤 행사를 준비하나요?')
        d['kind']=st.selectbox('출력 양식',list(KINDS),index=list(KINDS).index(d['kind']),format_func=KINDS.get,key='input_kind')
        d['program']=st.text_input('프로그램명 · 제목 첫 줄',value=d['program'],placeholder='예: 구강악안면외과 연수프로그램',max_chars=200,key='input_program')
        d['event_name']=st.text_input('행사명 *',value=d['event_name'],max_chars=100,key='input_event_name')
        d['subject']=st.text_input('주제 · 기획서 행사 개요에 표시',value=d['subject'],max_chars=500,key='input_subject')
        d['date']=st.date_input('행사 날짜 *',value=date.fromisoformat(d['date']),format='YYYY/MM/DD',key='input_date').isoformat()
        d['include_time']=st.checkbox('시간도 표시',value=d['include_time'],key='input_include_time')
        if d['include_time']:
            a,b=st.columns(2)
            with a:d['start']=st.time_input('시작',value=time.fromisoformat(d['start']),key='input_start').strftime('%H:%M')
            with b:d['end']=st.time_input('종료',value=time.fromisoformat(d['end']),key='input_end').strftime('%H:%M')
    elif s.step==1:
        st.subheader('누가, 어디에서 함께하나요?')
        people=sorted(set(settings['people'])|set(d['people'])|{p for x in s.documents for p in x['people']})
        places=sorted(set(settings['places'])|set(d['places'])|{p for x in s.documents for p in x['places']})
        d['people']=[v.strip() for v in st.multiselect('참석 대상 *',people,default=d['people'],accept_new_options=True,key='input_people',placeholder='이름 또는 대상 그룹 입력 후 Enter') if v.strip()]
        d['places']=[v.strip() for v in st.multiselect('장소 *',places,default=d['places'],accept_new_options=True,key='input_places',placeholder='장소 입력 후 Enter · 여러 곳 선택 가능') if v.strip()]
        d['speakers']=st.text_input('초청 연자',value=d['speakers'],max_chars=1000,key='input_speakers')
        st.caption('명단에 없는 이름이나 장소는 직접 입력해 추가하세요. 참석 대상은 “교수진 및 대학원생”처럼 그룹으로도 입력할 수 있습니다.')
    elif s.step==2:
        st.subheader('문서의 내용을 정리해 주세요.')
        for field in (['purpose','details','expected'] if d['kind']=='plan' else ['summary']):phrase_input(field)
        if d['kind']=='plan':
            st.markdown('**예산**')
            with st.form('budget_add',clear_on_submit=True):
                item=st.text_input('예산 항목',max_chars=500)
                amount=st.number_input('금액 (원)',min_value=0,max_value=1_000_000_000_000,value=0,step=1000)
                note=st.text_input('재원 및 비고',max_chars=500)
                if st.form_submit_button('예산 항목 추가'):
                    if not item.strip():st.error('예산 항목 이름을 입력하세요.')
                    elif len(d['budget'])>=50:st.error('예산 항목은 50개까지 가능합니다.')
                    else:d['budget'].append(dict(item=item.strip(),amount=int(amount),note=note));st.rerun()
            for i,row in enumerate(d['budget']):
                with st.expander(f"{row['item']} · {row['amount']:,}원"):
                    with st.form('budget_edit_'+str(i)):
                        item=st.text_input('항목 수정',value=row['item'],max_chars=500);amount=st.number_input('금액 수정',min_value=0,max_value=1_000_000_000_000,value=row['amount']);note=st.text_input('비고 수정',value=row['note'],max_chars=500)
                        if st.form_submit_button('예산 수정 저장'):
                            if item.strip():row.update(item=item.strip(),amount=int(amount),note=note);st.rerun()
                            else:st.error('항목 이름을 입력해 주세요.')
                    if st.button('항목 삭제',key='budget_remove_'+str(i)):d['budget'].pop(i);st.rerun()
            st.metric('예산 합계',f"{sum(x['amount'] for x in d['budget']):,}원")
    elif s.step==3:
        st.subheader('사진을 첨부해 주세요.')
        st.caption('JPG·PNG·WebP, 장당 10MB·최대 10장. 사진은 선택 사항입니다. 기획서는 끝에 첨부사진으로, 결과보고서는 행사사진 아래에 들어갑니다.')
        files=st.file_uploader('사진 선택',type=['jpg','jpeg','png','webp'],accept_multiple_files=True,key='photos_upload_'+str(s.upload_epoch))
        if st.button('선택한 사진 추가',disabled=not files):
            try:
                additions=[]
                for f in files:
                    if hashlib.sha256(f.getvalue()).hexdigest() in {p['digest'] for p in d['photos']+additions}:continue
                    additions.append(photo_record(f.getvalue(),f.name))
                if len(d['photos'])+len(additions)>10:raise ValueError('사진은 최대 10장까지 가능합니다.')
                d['photos'].extend(additions);s.upload_epoch+=1;st.rerun()
            except Exception as e:st.error(str(e) if isinstance(e,ValueError) else '사진을 읽을 수 없습니다. JPG 또는 PNG로 변환한 후 다시 올려 주세요.')
        for i,p in enumerate(d['photos']):
            st.image(photo_bytes(p),width=350)
            p['caption']=st.text_input('사진 설명',value=p['caption'],key='caption_'+p['id'],max_chars=500)
            a,b,c=st.columns(3)
            with a:
                if st.button('↑ 위로',key='up_'+p['id'],disabled=i==0):d['photos'][i-1],d['photos'][i]=d['photos'][i],d['photos'][i-1];st.rerun()
            with b:
                if st.button('↓ 아래로',key='down_'+p['id'],disabled=i==len(d['photos'])-1):d['photos'][i+1],d['photos'][i]=d['photos'][i],d['photos'][i+1];st.rerun()
            with c:
                if st.button('사진 삭제',key='photo_delete_'+p['id']):d['photos'].pop(i);st.rerun()
    else:
        st.subheader('확인하고 출력하세요.')
        preview(d)
        try:validate_document(d)
        except ValueError as e:st.error(str(e))
        else:
            if st.button('현재 세션에 문서 보관'):
                exists=any(x['id']==d['id'] for x in s.documents)
                if len(s.documents)>=100 and not exists:st.error('보관 문서는 100건까지 가능합니다.')
                else:s.documents=[x for x in s.documents if x['id']!=d['id']]+[deepcopy(d)];st.success('보관했습니다. 종료 전에 백업도 다운로드해 주세요.')
            downloads(d,'draft')
        with st.expander('새 문서 작성'):
            confirm=st.checkbox('현재 초안을 비우고 새로 시작합니다')
            if st.button('빈 문서로 시작',disabled=not confirm):reset();st.rerun()
def sync_inputs():
    for k in ('kind','program','event_name','subject','date','include_time','start','end','people','places','speakers',*FIELDS):
        if 'input_'+k not in s:continue
        value=s['input_'+k]
        if k=='date':value=value.isoformat()
        elif k in ('start','end'):value=value.strftime('%H:%M')
        elif k in ('people','places'):value=[v.strip() for v in value if v.strip()]
        s.draft[k]=value
    for p in s.draft['photos']:
        if 'caption_'+p['id'] in s:p['caption']=s['caption_'+p['id']]

def advance():
    sync_inputs()
    d=s.draft
    error=None
    if s.step==0 and not d['event_name'].strip():error='행사명을 입력해 주세요.'
    elif s.step==0 and d['include_time'] and d['end']<=d['start']:error='종료 시간은 시작 시간 이후여야 합니다.'
    elif s.step==1 and (not d['people'] or not d['places']):error='참석 대상과 장소를 입력해 주세요.'
    elif s.step==2 and any(not d[k].strip() for k in (['purpose','details','expected'] if d['kind']=='plan' else ['summary'])):error='필수 내용을 입력해 주세요.'
    if error:s.notice=error
    else:s.step+=1

def go_back():
    sync_inputs()
    s.step-=1

a,b=st.columns([1,2])
with a:
    st.button('← 이전',disabled=s.step==0,use_container_width=True,on_click=go_back)
with b:
    if s.step<4:st.button('다음 →',type='primary',use_container_width=True,on_click=advance)
st.caption('필수 항목 * · 입력 내용은 단계 이동 시 유지됩니다. PDF·Word는 출력용, 백업 ZIP은 다음 작업을 이어가기 위한 파일입니다.')
