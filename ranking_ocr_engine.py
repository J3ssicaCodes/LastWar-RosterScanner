"""Reuse the installed Tesseract model for ranking cells within each worker."""
import ctypes as C
from ctypes.util import find_library
from functools import lru_cache
from threading import local

import numpy as np

WORD = 3  # TessPageIteratorLevel.RIL_WORD
_workers = local()


@lru_cache(maxsize=1)
def _library():
    name = find_library('tesseract')
    if not name:
        return None
    ptr, integer, string = C.c_void_p, C.c_int, C.c_char_p
    signatures = {
        'TessBaseAPICreate': (ptr, []),
        'TessBaseAPIInit3': (integer, [ptr, string, string]),
        'TessBaseAPIDelete': (None, [ptr]),
        'TessBaseAPIClear': (None, [ptr]),
        'TessBaseAPIClearAdaptiveClassifier': (None, [ptr]),
        'TessBaseAPISetPageSegMode': (None, [ptr, integer]),
        'TessBaseAPISetVariable': (integer, [ptr, string, string]),
        'TessBaseAPISetImage': (None, [ptr, ptr, integer, integer, integer, integer]),
        'TessBaseAPIRecognize': (integer, [ptr, ptr]),
        'TessBaseAPIGetIterator': (ptr, [ptr]),
        'TessResultIteratorGetUTF8Text': (ptr, [ptr, integer]),
        'TessResultIteratorConfidence': (C.c_float, [ptr, integer]),
        'TessResultIteratorNext': (integer, [ptr, integer]),
        'TessResultIteratorDelete': (None, [ptr]),
        'TessDeleteText': (None, [ptr]),
        'TessMonitorCreate': (ptr, []),
        'TessMonitorSetDeadlineMSecs': (None, [ptr, integer]),
        'TessMonitorDelete': (None, [ptr]),
    }
    try:
        lib = C.CDLL(name)
        for symbol, (result, args) in signatures.items():
            fn = getattr(lib, symbol)
            fn.restype, fn.argtypes = result, args
    except (OSError, AttributeError):
        return None
    return lib


class _Engine:
    def __init__(self, lib):
        self.lib = lib
        self.handle = lib.TessBaseAPICreate()
        if not self.handle or lib.TessBaseAPIInit3(self.handle, None, b'eng') != 0:
            self.close()
            raise RuntimeError('Could not initialize Tesseract.')

    def close(self):
        if self.handle:
            self.lib.TessBaseAPIDelete(self.handle)
            self.handle = None

    def __del__(self):
        self.close()

    def read(self, image, psm, whitelist):
        lib, handle = self.lib, self.handle
        pixels = np.ascontiguousarray(image)
        height, width = pixels.shape[:2]
        channels = pixels.shape[2] if pixels.ndim == 3 else 1
        lib.TessBaseAPISetPageSegMode(handle, psm)
        # Empty whitelist restores full text recognition after a numeric cell.
        if not lib.TessBaseAPISetVariable(handle, b'tessedit_char_whitelist', (whitelist or '').encode()):
            return None
        lib.TessBaseAPIClearAdaptiveClassifier(handle)
        monitor = lib.TessMonitorCreate()
        if not monitor:
            return None
        iterator = None
        words, scores = [], []
        try:
            lib.TessMonitorSetDeadlineMSecs(monitor, 18_000)
            lib.TessBaseAPISetImage(handle, pixels.ctypes.data, width, height, channels, pixels.strides[0])
            if lib.TessBaseAPIRecognize(handle, monitor) != 0:
                return '', 0.0
            iterator = lib.TessBaseAPIGetIterator(handle)
            if iterator:
                while True:
                    pointer = lib.TessResultIteratorGetUTF8Text(iterator, WORD)
                    if pointer:
                        try:
                            word = C.string_at(pointer).decode('utf-8', errors='replace').strip()
                        finally:
                            lib.TessDeleteText(pointer)
                        if word:
                            words.append(word)
                            scores.append(lib.TessResultIteratorConfidence(iterator, WORD))
                    if not lib.TessResultIteratorNext(iterator, WORD):
                        break
            return ' '.join(words), round(sum(scores) / len(scores), 1) if scores else 0.0
        finally:
            if iterator:
                lib.TessResultIteratorDelete(iterator)
            lib.TessMonitorDelete(monitor)
            lib.TessBaseAPIClear(handle)


def read_ranking_text(image, psm, whitelist=None):
    """Return None when the native engine is unavailable so callers can fall back."""
    if not image.size:
        return '', 0.0
    if image.dtype != np.uint8 or not (image.ndim == 2 or (image.ndim == 3 and image.shape[2] == 3)):
        return None
    if not hasattr(_workers, 'engine'):
        lib = _library()
        try:
            _workers.engine = _Engine(lib) if lib else None
        except RuntimeError:
            _workers.engine = None
    engine = _workers.engine
    return engine.read(image, psm, whitelist) if engine else None
