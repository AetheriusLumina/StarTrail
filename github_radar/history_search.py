"""One validated definition for history counts and saved-day results."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qsl
from calendar import monthrange


def month_bounds(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}',value):
        raise ValueError('月份格式应为YYYY-MM')
    year,month=map(int,value.split('-'))
    try:
        first=date(year,month,1)
        last=date(year,month,monthrange(year,month)[1])
    except (ValueError,OverflowError):
        raise ValueError('请选择有效年月') from None
    return first.isoformat(),last.isoformat()


@dataclass(frozen=True)
class HistoryFilters:
    q: str = ''
    from_date: str | None = None
    to_date: str | None = None
    source: str = 'all'

    def validate(self):
        if not isinstance(self.q,str) or len(self.q)>200:
            raise ValueError('搜索内容最多200个字符')
        for value in (self.from_date,self.to_date):
            if value is not None:
                if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):
                    raise ValueError('日期格式应为YYYY-MM-DD')
                try: date.fromisoformat(value)
                except ValueError: raise ValueError('请输入真实日期') from None
        if self.from_date and self.to_date and self.from_date>self.to_date:
            raise ValueError('开始日期不能晚于结束日期')
        if not isinstance(self.source,str) or (self.source not in ('all','growth') and
            not re.fullmatch(r'keyword:0*[1-9][0-9]*',self.source)):
            raise ValueError('历史来源无效')
        return self


def parse_history_query(query: str, allow_cursor: bool) -> tuple[HistoryFilters,int]:
    values={}
    allowed={'q','from','to','source'} | ({'cursor'} if allow_cursor else set())
    for key,value in parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=6):
        if key not in allowed or key in values:
            raise ValueError('历史查询参数重复或无效')
        values[key]=value
    cursor=values.get('cursor','0')
    if not re.fullmatch(r'[0-9]+',cursor):raise ValueError('分页位置无效')
    filters=HistoryFilters(values.get('q',''),values.get('from'),values.get('to'),values.get('source','all')).validate()
    return filters,int(cursor)


def _text_values(value):
    if isinstance(value,str):return [value]
    if isinstance(value,list):return [s for child in value for s in _text_values(child)]
    if isinstance(value,dict):return [s for child in value.values() for s in _text_values(child)]
    return []


def search_rows(connection, filters: HistoryFilters):
    filters.validate()
    clauses,params=[],[]
    for value,operator in ((filters.from_date,'>='),(filters.to_date,'<=')):
        if value is not None:clauses.append('rec.local_date '+operator+' ?');params.append(value)
    if filters.source=='growth':clauses.append("rec.section='growth'")
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    rows=connection.execute('''SELECT rec.*,repo.full_name,repo.description,repo.topics,
        readme.text AS readme_text,readme.content_hash AS readme_hash
        FROM recommendations rec JOIN repositories repo ON repo.id=rec.repo_id
        LEFT JOIN readme_cache readme ON readme.repo_id=rec.repo_id'''+where+
        ' ORDER BY rec.local_date DESC,rec.position',params).fetchall()
    keyword=int(filters.source.split(':')[1]) if filters.source.startswith('keyword:') else None
    needle=filters.q.casefold()
    content={}
    if needle:
        # Decode saved JSON, so Chinese text is searchable even if escaped on disk.
        for row in connection.execute('SELECT repo_id,content FROM ai_explanations'):
            try:strings=_text_values(json.loads(row['content']))
            except (ValueError,TypeError):continue
            content.setdefault(row['repo_id'],[]).extend(strings)
    result=[]
    for row in rows:
        if keyword is not None and row['keyword_id']!=keyword and keyword not in json.loads(row['matched_keyword_ids']):continue
        if needle:
            try:topics=_text_values(json.loads(row['topics']))
            except (ValueError,TypeError):topics=[]
            materials=[row['full_name'],row['description'],*topics,*content.get(row['repo_id'],[])]
            text=row['readme_text']
            if text is not None and hashlib.sha256(text.encode()).hexdigest()==row['readme_hash']:materials.append(text)
            if not any(needle in material.casefold() for material in materials):continue
        result.append(row)
    return result
