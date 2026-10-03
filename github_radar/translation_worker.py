"""Private CPU-only worker. No store, scheduler, AI client or network imports."""
import json
import os
import socket
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from .translation_types import validate_request
from .translation_content import translate_item


def _tools_root() -> Path:
    return Path(__file__).resolve().parent.parent / '.tools'


def model_paths() -> tuple[Path, Path]:
    base = (Path(__file__).parent / 'translation_models' if getattr(sys, 'frozen', False)
            else Path(os.environ.get('STARTRAIL_MODEL_DIR') or _tools_root() / 'translation-models'))
    return base / 'en-zh', base / 'zh-en'


class CPUTranslator:
    def __init__(self):
        import ctranslate2
        import sentencepiece
        self.ct, self.sp, self.engines = ctranslate2, sentencepiece, {}

    def _engine(self, target):
        if target not in ('zh', 'en'):
            raise ValueError('Invalid translation target')
        if target not in self.engines:
            root = model_paths()[0 if target == 'zh' else 1]
            tokenizer = self.sp.SentencePieceProcessor(
                model_proto=(root / 'sentencepiece.model').read_bytes())
            engine = self.ct.Translator(str(root / 'model'), device='cpu',
                                       compute_type='int8', inter_threads=1, intra_threads=2)
            self.engines[target] = tokenizer, engine
        return self.engines[target]

    def encode(self, text: str, target: str) -> list[str]:
        return self._engine(target)[0].encode(text, out_type=str)

    def translate(self, text: str, target: str) -> str:
        tokenizer, engine = self._engine(target)
        tokens = self.encode(text, target)
        if len(tokens) > 256:
            raise ValueError('Unsplit translation input')
        result = engine.translate_batch([tokens], beam_size=4, max_decoding_length=512)
        return tokenizer.decode(result[0].hypotheses[0]).replace('▁', ' ')


@contextmanager
def _offline():
    def refuse(*args, **kwargs):
        raise RuntimeError('NETWORK_FORBIDDEN')
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved


def run_worker(input_path: Path, output_path: Path) -> int:
    code = 0
    try:
        with _offline():
            if input_path.stat().st_size > 524288:
                raise ValueError('Request too large')
            target, items = validate_request(json.loads(input_path.read_text(encoding='utf-8')))
            translator = CPUTranslator()
            results = [asdict(translate_item(item, target, translator.translate, translator.encode))
                       for item in items]
            result = {'status': 'ready', 'items': results, 'reason': None}
    except Exception:
        code = 1
        result = {'status': 'error', 'items': [], 'reason': '离线翻译引擎不可用，显示原文'}
    try:
        output_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    except OSError:
        return 1
    return code
