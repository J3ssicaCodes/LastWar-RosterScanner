"""Read the game’s rank column with the bundled RapidOCR recognition model."""
from functools import lru_cache
import logging
import re

from scan_rankings import enlarge, text_with_conf


@lru_cache(maxsize=1)
def _rank_model():
    try:
        from rapidocr.ch_ppocr_rec.main import TextRecognizer
        from rapidocr.main import DEFAULT_CFG_PATH
        from rapidocr.utils.parse_parameters import ParseParams

        cfg = ParseParams.load(DEFAULT_CFG_PATH)
        cfg.Rec.engine_cfg = cfg.EngineConfig.onnxruntime
        cfg.Rec.engine_cfg.intra_op_num_threads = 1
        cfg.Rec.engine_cfg.inter_op_num_threads = 1
        cfg.Rec.font_path = None
        return TextRecognizer(cfg.Rec)
    except (ImportError, OSError, RuntimeError, ValueError):
        logging.getLogger(__name__).warning('RapidOCR rank model unavailable; using the local fallback.')
        return None


def read_rank(crop):
    model = _rank_model()
    if model is None:
        return text_with_conf(enlarge(crop), 7, '0123456789')
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    result = model(TextRecInput(img=[crop], return_word_box=False))
    if result.txts:
        value = result.txts[0].strip()
        if re.fullmatch(r'[0-9]{1,3}', value):
            return value, 100 * result.scores[0]
    return '', 0.
