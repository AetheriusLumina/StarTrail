import importlib
import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class TranslationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('github_radar.translation_worker'),
                             'CPU translation worker not implemented')
        self.worker = importlib.import_module('github_radar.translation_worker')

    def test_tokenizer_reads_chinese_path_bytes_and_engine_uses_cpu(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '中文语言包'
            root.mkdir()
            (root / 'sentencepiece.model').write_bytes(b'model bytes')
            engine, tokenizer = Mock(), Mock()
            tokenizer.encode.return_value = ['a']
            tokenizer.decode.return_value = '译▁文'
            engine.translate_batch.return_value = [SimpleNamespace(hypotheses=[['result']])]
            ct = SimpleNamespace(Translator=Mock(return_value=engine))
            sp = SimpleNamespace(SentencePieceProcessor=Mock(return_value=tokenizer))
            with patch.dict('sys.modules', ctranslate2=ct, sentencepiece=sp), patch.object(
                    self.worker, 'model_paths', return_value=(root, root)):
                translator = self.worker.CPUTranslator()
                self.assertEqual(translator.encode('text', 'zh'), ['a'])
                self.assertEqual(translator.translate('text', 'zh'), '译 文')
            sp.SentencePieceProcessor.assert_called_with(model_proto=b'model bytes')
            ct.Translator.assert_called_with(str(root / 'model'), device='cpu',
                                            compute_type='int8', inter_threads=1, intra_threads=2)
            engine.translate_batch.assert_called_once_with([['a']], beam_size=4,
                                                          max_decoding_length=512)

    def test_worker_blocks_network_and_writes_typed_results_without_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp)/'输入.json', Path(temp)/'输出.json'
            source.write_text(json.dumps({'target':'zh','items':[{'id':'a','parts':[
                {'kind':'text','text':'Useful project'}]}]}), encoding='utf-8')
            def inference(text, target):
                with self.assertRaisesRegex(RuntimeError, 'NETWORK_FORBIDDEN'):
                    socket.create_connection(('127.0.0.1', 1))
                with socket.socket() as sock:
                    with self.assertRaisesRegex(RuntimeError, 'NETWORK_FORBIDDEN'):
                        sock.connect_ex(('127.0.0.1', 1))
                return '有用的项目'
            fake = SimpleNamespace(translate=inference, encode=lambda t,l:t.split())
            with patch.object(self.worker, 'CPUTranslator', return_value=fake), patch('sys.stdout', None):
                self.assertEqual(self.worker.run_worker(source, output), 0)
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['items'][0]['parts'], [{'kind':'text','text':'有用的项目'}])
            self.assertEqual(result['items'][0]['status'], 'translated')

    def test_missing_model_is_explicit_error_file(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp)/'in.json', Path(temp)/'out.json'
            source.write_text('{"target":"zh","items":[]}', encoding='utf-8')
            with patch.object(self.worker, 'CPUTranslator', side_effect=FileNotFoundError('secret/path')):
                self.assertEqual(self.worker.run_worker(source, output), 1)
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'error')
            self.assertIn('原文', result['reason'])
            self.assertNotIn('secret', output.read_text(encoding='utf-8'))

    def test_cli_worker_dispatch_precedes_store_and_scheduler(self):
        from github_radar.__main__ import main
        with patch.object(self.worker, 'run_worker', return_value=0) as run, patch(
                'github_radar.storage.RadarStore', side_effect=AssertionError('store initialized')):
            self.assertEqual(main(['--translation-worker','输入','输出']), 0)
        run.assert_called_once_with(Path('输入'), Path('输出'))

    def test_frozen_resources_use_module_directory(self):
        with patch('sys.frozen', True, create=True):
            first, second = self.worker.model_paths()
        self.assertEqual(first, Path(self.worker.__file__).parent/'translation_models'/'en-zh')
        self.assertEqual(second.name, 'zh-en')
