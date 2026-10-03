"""Consumer contracts: translation may change prose, never executable literals."""
import importlib
import importlib.util
import re
import unittest


class TranslationContentTests(unittest.TestCase):
    def test_chinese_prose_with_compensation_symbols_does_not_call_english_model(self):
        source='离职补偿 N、代通知金、2N，保存原文。'
        result=self.run_item(self.item(('text',source)),infer=lambda *_:self.fail('Chinese N/2N must stay unchanged'))
        self.assertEqual(self.shown(result),source)
        self.assertIsNone(result.reason)

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('github_radar.translation_content'),
                             'Offline content translator is not implemented')
        self.types = importlib.import_module('github_radar.translation_types')
        self.content = importlib.import_module('github_radar.translation_content')

    def item(self, *parts):
        return self.types.TranslationItem('p1',tuple(self.types.TranslationPart(*p) for p in parts))

    @staticmethod
    def encode(text, target):
        return re.findall(r'\S+', text)

    def run_item(self, item, infer=lambda text,target:text, target='zh', encode=None):
        return self.content.translate_item(item,target,infer,encode or self.encode)

    @staticmethod
    def shown(result):
        return ''.join(p.text for p in result.parts)

    def test_ai_tags_runtime_terms_and_emoji_do_not_become_financial_text(self):
        for term in ('ai','llm','RAG','MCP','OpenShell','PageIndex','📑'):
            with self.subTest(term=term):
                value=self.run_item(self.item(('text',term)),
                                    infer=lambda text,target:'(单位:千美元)')
                self.assertEqual(self.shown(value),term)
        value=self.run_item(self.item(('text','Non-autoregressive agents use a private runtime.')))
        self.assertIn('非自回归',self.shown(value))
        self.assertIn('智能体',self.shown(value))
        self.assertIn('运行环境',self.shown(value))
        for source,expected in [('content-curation','内容精选'),('agentic-ai','智能体-ai')]:
            self.assertEqual(self.shown(self.run_item(self.item(('text',source)))),expected)

    def test_prose_is_translated_while_code_and_url_remain_literal(self):
        item=self.item(('text','Install with '),('literal','pip install radar'),
                       ('text',' and see '),('literal','https://github.com/owner/repo#start'),('text','.'))
        def infer(text,target):
            return '请用 X0，查看 X1。'
        result=self.run_item(item,infer)
        self.assertEqual(result.status,'translated')
        self.assertEqual(self.shown(result),'请用 pip install radar，查看 https://github.com/owner/repo#start。')
        self.assertEqual([p.text for p in result.parts if p.kind=='literal'],
                         ['pip install radar','https://github.com/owner/repo#start'])

    def test_platform_names_in_generated_chinese_facts_stay_exact(self):
        source='通过 Kubernetes 管理沙箱；需要 Apple Silicon、Windows WSL 2。'
        inputs=[]
        def infer(text,target):
            inputs.append(text)
            return text.replace('Kubernetes','库贝尔涅兹').replace('Apple Silicon','苹果硅').replace('WSL','WSL 读取')
        result=self.run_item(self.item(('text',source)),infer)
        self.assertEqual(self.shown(result),source)
        self.assertEqual(inputs,[],'Already-Chinese facts containing platform names need no model inference')

    def test_twelve_literals_restore_once_without_x1_corrupting_x10(self):
        parts=[('text','Please keep these commands: ')]
        for index in range(12): parts += [('literal',f'--command-{index}'),('text',' ')]
        result=self.run_item(self.item(*parts))
        self.assertEqual([p.text for p in result.parts if p.kind=='literal'],
                         [f'--command-{i}' for i in range(12)])
        self.assertNotIn('X10',self.shown(result))

    def test_duplicate_literal_values_keep_both_nodes(self):
        item=self.item(('text','See '),('literal','same'),('text',' and '),('literal','same'))
        result=self.run_item(item)
        self.assertEqual([p.text for p in result.parts if p.kind=='literal'],['same','same'])

    def test_source_marker_collision_uses_another_prefix(self):
        item=self.item(('text','Keep X0 with '),('literal','--safe'))
        result=self.run_item(item)
        self.assertEqual(result.status,'translated')
        self.assertIn('X0',self.shown(result))
        self.assertIn('--safe',self.shown(result))

    def test_lost_duplicate_reordered_or_unknown_markers_fall_back(self):
        item=self.item(('text','Use '),('literal','--first'),('text',' then '),('literal','--second'))
        for output in ['正文 X0','X0 X0 X1','X1 X0','X0 X1 X999']:
            with self.subTest(output=output):
                result=self.run_item(item,lambda text,target:output)
                self.assertEqual(result.status,'original')
                self.assertEqual(result.parts,item.parts)
                self.assertTrue(result.reason)

    def test_missing_model_markers_retry_prose_without_exposing_literals(self):
        item=self.item(('text','Use '),('literal','--first'),('text',' safely.'))
        inputs=[]
        def infer(text,target):
            inputs.append(text)
            return '使用安全。' if 'X0' in text else '本地译文'
        result=self.run_item(item,infer)
        self.assertEqual(result.status,'translated')
        self.assertEqual([p.text for p in result.parts if p.kind=='literal'],['--first'])
        self.assertTrue(all('--first' not in text for text in inputs))
        self.assertNotIn('Use',self.shown(result))

    def test_implicit_technical_ids_inline_code_urls_and_numbers_are_protected(self):
        source='Use `npm install` for owner/repo with API and README at https://github.com/owner/repo#x with 1000 stars.'
        item=self.item(('text',source))
        result=self.run_item(item)
        for value in ['`npm install`','owner/repo','API','README','https://github.com/owner/repo#x','1000','stars']:
            self.assertIn(value,self.shown(result))
        self.assertNotRegex(self.shown(result),r'[XYZ]\d+')

    def test_already_target_chinese_is_preserved_in_mixed_sentence(self):
        item=self.item(('text','Local search. 项目支持本地运行。 Use Python.'))
        result=self.run_item(item,lambda text,target:text.replace('Local search.','本地搜索。'))
        self.assertIn('项目支持本地运行。',self.shown(result))
        self.assertIn('Python',self.shown(result))

    def test_already_english_fragment_is_preserved_when_translating_chinese(self):
        item=self.item(('text','本地搜索。 Keep this sentence unchanged.'))
        result=self.run_item(item,target='en')
        self.assertIn('Keep this sentence unchanged.',self.shown(result))

    def test_artificial_terminology_retains_meaning_and_negation(self):
        item=self.item(('text','Do not delete the projects you follow; track daily Star growth.'))
        result=self.run_item(item)
        self.assertIn('Do not delete',self.shown(result))
        self.assertIn('关注的项目',self.shown(result))
        self.assertIn('每日新增 Star',self.shown(result))
        self.assertNotIn('恒星',self.shown(result))

    def test_long_paragraph_passes_all_segments_without_silent_truncation(self):
        source='word ' * 700
        def infer(text,target):
            self.assertLessEqual(len(self.encode(text,target)),256)
            return text
        result=self.run_item(self.item(('text',source)),infer)
        self.assertEqual(self.shown(result),source)

    def test_unbreakable_oversize_text_returns_the_complete_original(self):
        source='a' * 1024
        result=self.run_item(self.item(('text',source)),encode=lambda text,target:list(text))
        self.assertEqual(result.status,'original')
        self.assertEqual(self.shown(result),source)
        self.assertTrue(result.reason)

    def test_all_literal_content_never_runs_the_model(self):
        def forbidden(text,target): self.fail('Literal content reached the model')
        item=self.item(('literal','```\nprint("<safe>")\n\n```'))
        result=self.run_item(item,forbidden)
        self.assertEqual(result.parts,item.parts)

    def test_fenced_code_inside_text_is_preserved_as_one_literal_value(self):
        block='```sh\npip install radar\n\n# <tag> stays literal\n```'
        item=self.item(('text','Install with:\n'+block+'\nThen open the app.'))
        result=self.run_item(item,lambda text,target:'请安装：X0\n然后打开应用。')
        self.assertEqual(result.status,'translated')
        self.assertIn(block,self.shown(result))

    def test_inference_error_and_empty_result_keep_original(self):
        item=self.item(('text','Translate this meaningful sentence.'))
        def fail(text,target): raise RuntimeError('CPU model missing')
        for infer in (fail,lambda text,target:''):
            result=self.run_item(item,infer)
            self.assertEqual(result.status,'original')
            self.assertEqual(result.parts,item.parts)
            self.assertTrue(result.reason)

    def test_validate_parses_parts_without_changing_source(self):
        target,items=self.types.validate_request({'target':'en','items':[
            {'id':'p1','parts':[{'kind':'text','text':'中文说明'},{'kind':'literal','text':'--key'}]}]})
        self.assertEqual(target,'en')
        self.assertEqual(items[0].parts[1].text,'--key')
        self.assertEqual(items[0].parts[0].text,'中文说明')

    def test_validate_rejects_malformed_values_and_duplicate_ids(self):
        valid={'id':'a','parts':[{'kind':'text','text':'source'}]}
        cases=[{'target':True,'items':[valid]}, {'target':'fr','items':[valid]},
               {'target':'zh','items':[valid,valid]}, {'target':'zh','items':[{'id':'','parts':[]}]},
               {'target':'zh','items':[{'id':True,'parts':[]}]},
               {'target':'zh','items':[{'id':'a','parts':[{'kind':'html','text':'<script>'}]}]},
               {'target':'zh','items':[{'id':'a','parts':[{'kind':'text','text':False}]}]}]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError): self.types.validate_request(payload)

    def test_validate_enforces_limits_without_truncation(self):
        def one(text,id='a'): return {'id':id,'parts':[{'kind':'text','text':text}]}
        cases=[[one('a'*16385)], [one('a',str(i)) for i in range(129)],
               [one('a'*16384,str(i)) for i in range(5)], [one('a','x'*65)]]
        for items in cases:
            with self.subTest(count=len(items)):
                with self.assertRaises(ValueError): self.types.validate_request({'target':'zh','items':items})
        target,items=self.types.validate_request({'target':'zh','items':[one('a'*16384)]})
        self.assertEqual(len(items[0].parts[0].text),16384)
