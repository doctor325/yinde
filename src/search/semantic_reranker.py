"""Offline pairwise relevance scores. No entity-specific rules or generation."""
import json
import time
from pathlib import Path


class Reranker:
    def __init__(self, state):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        from scripts.setup_semantic_reranker import digest, REVISION
        self.np = np
        folder = Path(state) / 'reranker'
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        if receipt['revision'] != REVISION:
            raise ValueError('Unexpected reranker revision')
        for item in receipt['files']:
            if digest(folder / item['file']) != item['sha256']:
                raise ValueError('Reranker digest mismatch')
        self.tokenizer = Tokenizer.from_file(str(folder / 'tokenizer.json'))
        self.tokenizer.enable_padding(pad_id=1, pad_token='<pad>')
        # Preserve the question, limit only candidate text, record truncations below.
        self.tokenizer.no_truncation()
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 8
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(str(folder / 'onnx/model.onnx'), sess_options=options,
                                          providers=['CPUExecutionProvider'])
        self.inputs = {x.name for x in self.model.get_inputs()}

    def score(self, query, texts):
        from search.zh import to_simplified
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
            raise ValueError('Invalid query')
        if not isinstance(texts, list) or len(texts) > 120 or any(not isinstance(t, str) or len(t) > 1000 for t in texts):
            raise ValueError('Invalid candidates')
        started = time.perf_counter()
        pairs = [(to_simplified(query), to_simplified(t)) for t in texts]
        self.tokenizer.no_truncation()
        # encode_batch pads, so inspect unpadded length via attention mask.
        lengths = [sum(x.attention_mask) for x in self.tokenizer.encode_batch(pairs)] if pairs else []
        self.tokenizer.enable_truncation(max_length=512, strategy='only_second')
        scores = self.np.empty(len(texts), dtype='float32')
        order = sorted(range(len(texts)), key=lambda i: lengths[i])
        for lo in range(0, len(order), 4):
            ids = order[lo:lo+4]
            batch = self.tokenizer.encode_batch([pairs[i] for i in ids])
            inputs = {name: self.np.asarray([getattr(x, field) for x in batch], dtype='int64')
                      for name, field in (('input_ids', 'ids'), ('attention_mask', 'attention_mask'), ('token_type_ids', 'type_ids'))
                      if name in self.inputs}
            values = self.model.run(None, inputs)[0].reshape(-1)
            if len(values) != len(ids) or not self.np.isfinite(values).all():
                raise ValueError('Invalid reranker output')
            scores[ids] = values
        return {'scores': scores.tolist(), 'truncated_indices': [i for i, n in enumerate(lengths) if n > 512],
                'ms': round((time.perf_counter()-started)*1000, 2)}
