import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from extract_ranking_pages import detect, open_video, sign


class VideoMemoryTests(unittest.TestCase):
    def test_streaming_pause_detection_matches_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.mp4'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 30, (320, 240))
            self.assertTrue(writer.isOpened())
            for value, count in [(20, 30), (90, 42), (150, 3), (210, 36)]:
                for _ in range(count):
                    writer.write(np.full((240, 320, 3), value, dtype=np.uint8))
            writer.release()
            cap = open_video(path)
            if hasattr(cv2, 'CAP_PROP_N_THREADS'):
                self.assertEqual(cap.get(cv2.CAP_PROP_N_THREADS), 1)
            signatures = []
            i = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if i % 3 == 0:
                    signatures.append((i, sign(frame, 40, 189)))
                i += 1
            cap.release()
            expected = []
            start = 0
            min_samples = max(math.ceil(.38 * 30 / 3), math.ceil(15 / 3))
            for j in range(1, len(signatures)):
                if float(cv2.absdiff(signatures[j][1], signatures[j-1][1]).mean()) > 2.5:
                    if j - start >= min_samples:
                        expected.append((signatures[start][0], signatures[j-1][0]))
                    start = j
            if len(signatures) - start >= min_samples:
                expected.append((signatures[start][0], signatures[-1][0]))
            with patch('extract_ranking_pages.detect_table_header', return_value=(40, [])):
                result = detect(path)
            self.assertEqual(result[-1], expected)
            self.assertGreaterEqual(len(expected), 3)


if __name__ == '__main__':
    unittest.main()
