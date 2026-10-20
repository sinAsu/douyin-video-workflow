import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/douyin-video-workflow/scripts'
sys.path.insert(0, str(SCRIPTS))
import workflow


class WorkflowTests(unittest.TestCase):
    def test_exact_center_crop_without_stretch(self):
        self.assertEqual(workflow.geometry(1728, 1080), (1440, 1080, 144))
        self.assertEqual(workflow.geometry(1140, 720), (960, 720, 90))
        with self.assertRaises(ValueError):
            workflow.geometry(720, 1080)
        with self.assertRaises(ValueError):
            workflow.geometry(1142, 720)

    def test_codec_filter_precedes_resolution_and_bitrate(self):
        def entry(h, rate, hevc, fmt='mp4'):
            return {'format': fmt, 'is_h265': hevc, 'bit_rate': rate,
                    'play_addr': {'height': h, 'width': h*8//5, 'data_size': 100,
                                  'url_key': str(rate), 'url_list': ['https://v1.douyinvod.com/a']}}
        detail = {'aweme_id': '1234567890123456789', 'desc': 'test', 'video': {'bit_rate': [
            entry(1080, 1500000, 1), entry(720, 4000000, 0),
            entry(1080, 1200000, 0), entry(2160, 5000000, 0, 'dash')]}}
        self.assertEqual(workflow.select_variant(detail, 'h264')['bitrate'], 1200000)
        self.assertEqual(workflow.select_variant(detail, 'any')['codec'], 'hevc')
        detail['video']['bit_rate'] = [entry(1080, 1500000, 1)]
        with self.assertRaises(ValueError):
            workflow.select_variant(detail, 'h264')

    def test_renamed_completed_file_is_found_by_signature(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); original = root/'id.mp4'; original.write_bytes(b'original data')
            record = {'path': str(original), **workflow.signature(original)}
            renamed = root/'renamed.mp4'; original.rename(renamed)
            self.assertEqual(workflow.find_completed(record, root), renamed)
            renamed.write_bytes(b'corrupted data')
            self.assertIsNone(workflow.find_completed(record, root))

    def test_http_resume_does_not_append_a_full_response(self):
        payload = bytes(range(256))*4000
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                requested = self.headers.get('Range'); requests.append(requested)
                start = int(requested.split('=')[1].split('-')[0]) if requested else 0
                if self.path == '/ignored': start = 0
                self.send_response(206 if start else 200)
                self.send_header('Content-Length', str(len(payload)-start))
                if start: self.send_header('Content-Range', f'bytes {start}-{len(payload)-1}/{len(payload)}')
                self.end_headers(); self.wfile.write(payload[start:])
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                for route in ['resume','ignored']:
                    part = Path(d)/route;part.write_bytes(payload[:300000])
                    workflow.fetch(f'http://127.0.0.1:{server.server_port}/{route}', part, len(payload), timeout=3)
                    self.assertEqual(hashlib.sha256(part.read_bytes()).digest(), hashlib.sha256(payload).digest())
                self.assertEqual(requests, ['bytes=300000-', 'bytes=300000-'])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_non_http_and_unsafe_hosts_are_rejected(self):
        for url in ['file:///secret', 'https://evil.example/a', 'https://douyinvod.com.evil.example/a']:
            self.assertFalse(workflow.is_media_url(url))
        self.assertTrue(workflow.is_media_url('https://v26-web.douyinvod.com/a?x=1'))


if __name__ == '__main__': unittest.main()
