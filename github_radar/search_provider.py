"""AI contributes search evidence, never authoritative IDs or Star statistics."""
from datetime import datetime,timezone
import json,tempfile
from pathlib import Path
from .ai_types import RelevanceVerdict
from urllib.parse import urlsplit
from .ai_provider import AIOutputError,_closed_object
from .codex_runner import CodexRunner
from .discovery_types import DiscoveryCandidate,SourceEvidence,valid_repository_name
from .search_types import QueryExpansion,AISearchResult,GrowthAssessment

def strings(value,limit,size):
    if not isinstance(value,list) or len(value)>limit or any(not isinstance(t,str) or not 1<=len(t.strip())<=size for t in value):
        raise AIOutputError('AI 返回的检索文字无效')
    return tuple(dict.fromkeys(t.strip() for t in value))

class SearchProvider:
    def __init__(self,provider):
        self.provider=provider;self.runner=CodexRunner(provider.connection)

    def cancel(self):self.runner.cancel();self.provider.cancel()

    def expand_keyword(self,term,model_id,topics,*,timeout=120):
        schema=_closed_object({'terms':{'type':'array','maxItems':6,'items':{'type':'string','maxLength':120}},
            'topics':{'type':'array','maxItems':6,'items':{'type':'string','maxLength':120}}},['terms','topics'])
        result=self.runner.run('Preserve the original keyword meaning. Suggest at most six focused Chinese/English synonyms. Topics must come from the supplied public directory. Do not broaden to generic unrelated AI projects.',
            {'original':term,'public_topics':list(topics)},schema,model_id,timeout=timeout)
        if set(result)!={'terms','topics'}:raise AIOutputError('关键词扩展格式有误')
        terms=strings(result['terms'],6,120);selected=strings(result['topics'],6,120)
        if any(t not in topics for t in selected):raise AIOutputError('AI 返回了不存在的公开主题')
        return QueryExpansion(term,tuple(t for t in terms if t!=term),selected,'1')

    def search_repositories(self,scope,expansion,known_names,gap,*,timeout,cancel_event):
        row=_closed_object({'full_name':{'type':'string','maxLength':240},'source_url':{'type':'string','maxLength':500},'evidence':{'type':'string','maxLength':300}},['full_name','source_url','evidence'])
        schema=_closed_object({'candidates':{'type':'array','maxItems':200,'items':row},'coverage':{'type':'array','maxItems':10,'items':{'type':'string'}},'notes':{'type':'array','maxItems':10,'items':{'type':'string'}}},['candidates','coverage','notes'])
        result=self.runner.run('Actively search GitHub public repositories and Trending plus Trendshift public topics and lists. '+
            ('Find recent rising projects for the target official UTC statistics day; list candidate evidence, never invent daily Stars. ' if scope.section=='growth' else 'Find repositories substantively related to the original keyword, including synonyms, beyond the current popular list. ')+
            'Supply actual owner/name and public source URLs. Report finite coverage, gaps and failures. Exclude known names. Do not return IDs or Star counts.',
            {'section':scope.section,'keyword':scope.term,'terms':list(expansion.terms) if expansion else [],'topics':list(expansion.topics) if expansion else [],'stat_date':scope.stat_date,'known_names':list(known_names[:200]),'gap':gap},
            schema,scope.model_id,web_search=True,timeout=timeout,cancel_event=cancel_event)
        if set(result)!={'candidates','coverage','notes'} or not isinstance(result['candidates'],list) or len(result['candidates'])>200:
            raise AIOutputError('AI 搜索结果格式有误')
        observed=datetime.now(timezone.utc).isoformat();candidates=[];names=set()
        for row in result['candidates']:
            if not isinstance(row,dict) or set(row)!={'full_name','source_url','evidence'}:raise AIOutputError('AI 仓库出处格式有误')
            name,url,note=row['full_name'],row['source_url'],row['evidence']
            if not valid_repository_name(name) or len(name)>240 or not isinstance(url,str) or len(url)>500 or not isinstance(note,str) or not 1<=len(note.strip())<=300:
                raise AIOutputError('AI 仓库身份或依据无效')
            source=urlsplit(url)
            if source.scheme!='https' or source.netloc not in ('github.com','trendshift.io') or source.username or source.password:
                raise AIOutputError('AI 搜索来源不是受支持的公开网站')
            if name.casefold() in names:continue
            names.add(name.casefold())
            evidence=SourceEvidence('ai_web_search',name,url,observed,'search',scope.stat_date,'none',None,None,None,(),evidence_text=note.strip())
            candidates.append(DiscoveryCandidate(name,None,('ai_web_search',),observed,evidence=(evidence,)))
        return AISearchResult(tuple(candidates),strings(result['coverage'],10,300),strings(result['notes'],10,300),self.runner.web_search_calls)

    def assess_growth_batch(self,inputs,scope,*,timeout=120):
        row=_closed_object({'repo_id':{'type':'integer'},'status':{'type':'string','enum':['supported','conflict','uncertain']},'reason':{'type':'string','maxLength':300}},['repo_id','status','reason'])
        schema=_closed_object({'assessments':{'type':'array','maxItems':20,'items':row}},['assessments'])
        data={'stat_date':scope.stat_date,'repositories':[{'repo_id':p.observation.repo.id,'full_name':p.observation.repo.full_name,'official_added':p.official_day.added if p.official_day else None,'evidence':[{'source':e.source_name,'date':e.stat_date,'url':e.source_url,'added':e.daily_added_text,'note':e.evidence_text} for e in p.evidence[:8]]} for p in inputs]}
        result=self.runner.run('Cross-check evidence identity, target UTC day and metric meaning. Official GitHub daily Star counts are authoritative. Trending/Trendshift popularity does not replace them. Return supported, conflict or uncertain with reason, never a replacement number.',data,schema,scope.model_id,timeout=timeout)
        if set(result)!={'assessments'} or not isinstance(result['assessments'],list):raise AIOutputError('增长判断格式有误')
        ids={p.observation.repo.id for p in inputs};found={}
        for row in result['assessments']:
            if not isinstance(row,dict) or set(row)!={'repo_id','status','reason'} or type(row['repo_id']) is not int or row['repo_id'] not in ids or row['repo_id'] in found or row['status'] not in ('supported','conflict','uncertain') or not isinstance(row['reason'],str) or not 1<=len(row['reason'].strip())<=300:
                raise AIOutputError('增长证据判断无效')
            found[row['repo_id']]=GrowthAssessment(**row)
        if set(found)!=ids:raise AIOutputError('增长证据判断缺少仓库')
        return tuple(found[p.observation.repo.id] for p in inputs)


    def filter_catalog(self,inputs,keyword,terms,model_id,*,timeout=120):
        # One read-only invocation sees the entire merged candidate corpus.
        # Compact columns avoid repeating field names and large README bodies.
        ids=[i.repo.id for i in inputs]
        if len(ids)!=len(set(ids)) or len(ids)>10000:raise AIOutputError('候选库过大或身份重复，已保留资料，请调整范围后继续')
        description_limit=max(32,min(400,600000//max(len(inputs),1)-100))
        readme_count=sum(bool(i.readme_excerpt) for i in inputs)
        excerpt_limit=min(1200,180000//max(readme_count,1))
        data={'original_keyword':keyword,'terms':list(terms),
            'columns':['repo_id','full_name','stars','description','topics','language','readme_excerpt','source_limited'],
            'repositories':[[i.repo.id,i.repo.full_name,i.repo.stars,i.repo.description[:description_limit],
                [t[:40] for t in i.repo.topics[:4]],i.repo.language,
                i.readme_excerpt[:excerpt_limit] if i.readme_excerpt else None,
                i.source_limited or not i.readme_excerpt or len(i.readme_excerpt)>excerpt_limit] for i in inputs]}
        schema=_closed_object({key:{'type':'array','maxItems':10000,'items':{'type':'integer'}}
            for key in ('relevant_ids','irrelevant_ids','uncertain_ids')},['relevant_ids','irrelevant_ids','uncertain_ids'])
        with tempfile.TemporaryDirectory(prefix='startrail-candidates-') as temporary:
            file=Path(temporary)/'candidates.json'
            with file.open('w',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,separators=(',',':'))
            if file.stat().st_size>1048576:raise AIOutputError('整组候选资料超过安全输入限制，未拆成反复AI调用；候选已保存，请调整范围或模型后继续')
            result=self.runner.run('Compare ALL supplied repositories together against the original keyword and its focused synonyms. '
                'Partition every supplied ID exactly once into relevant, irrelevant, or uncertain. Popularity alone does not establish relevance. '
                'When descriptions or excerpts are insufficient, use uncertain, never guess. Do not rank or invent Stars. '
                'The application will sort relevant repositories by authoritative total Stars.',None,schema,model_id,timeout=timeout,data_file=file)
        if set(result)!=set(schema['required']):raise AIOutputError('整组相关性判断格式有误')
        verdicts={};supplied=set(ids)
        for status in ('relevant','irrelevant','uncertain'):
            values=result[status+'_ids']
            if not isinstance(values,list):raise AIOutputError('整组相关性判断格式有误')
            for identity in values:
                if type(identity) is not int or identity not in supplied or identity in verdicts:raise AIOutputError('整组判断包含错误或重复的仓库身份')
                verdicts[identity]=RelevanceVerdict(identity,status,'整组候选依据公开名称、简介、主题及有限README统一判断；资料不足时标记不确定')
        if set(verdicts)!=supplied:raise AIOutputError('整组判断没有覆盖全部提供的候选，原结果保留')
        return tuple(verdicts[identity] for identity in ids)

    def filter_batch(self,inputs,keyword,model_id,*,timeout=120):
        previous=self.provider.TIMEOUT_SECONDS
        self.provider.TIMEOUT_SECONDS=timeout
        try:return self.provider.judge_batch(inputs,keyword,model_id)
        finally:self.provider.TIMEOUT_SECONDS=previous
