import importlib.util
import hashlib
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import struct
import unittest
import threading
from unittest.mock import patch

import imageio_ffmpeg

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/douyin-video-workflow/scripts'
sys.path.insert(0,str(SCRIPTS))


class RealCropTests(unittest.TestCase):
    def test_download_validates_mp4_then_skips_a_renamed_complete_file(self):
        import workflow
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);media=root/'fixture.mp4';ff=imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run([ff,'-nostdin','-loglevel','error','-f','lavfi','-i','color=size=1140x720:rate=25',
                            '-t','1','-c:v','libx264','-preset','ultrafast',str(media)],check=True)
            payload=media.read_bytes();requests=[]
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_GET(self):
                    requests.append(1);self.send_response(200)
                    self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                manifest=root/'manifest.json';out=root/'download'
                manifest.write_text(json.dumps([{'id':'1234567890123456789','variant':'h264-720-fixture','codec':'h264',
                    'width':1140,'height':720,'size':len(payload),'md5':hashlib.md5(payload).hexdigest(),
                    'urls':[f'http://127.0.0.1:{server.server_port}/fixture']}]))
                with patch.object(workflow,'is_media_url',return_value=True):
                    workflow.download(manifest,out)
                    downloaded=next(out.glob('*.mp4'));downloaded.rename(out/'user_renamed.mp4')
                    workflow.download(manifest,out)
                self.assertEqual(len(requests),1)
                self.assertEqual((out/'user_renamed.mp4').read_bytes(),payload)
            finally:server.shutdown();server.server_close();thread.join()

    def test_source_bitrate_crop_and_restart_preserve_frames_and_audio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'source';src.mkdir();out=root/'output';ff=imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run([ff,'-nostdin','-loglevel','error','-f','lavfi','-i','testsrc2=size=1140x720:rate=25',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
                            '-t','3','-c:v','libx264','-preset','ultrafast','-b:v','1200k',
                            '-c:a','aac',str(src/'sample.mp4')],check=True)
            cmd=[sys.executable,str(SCRIPTS/'video_crop.py'),'run','--input',str(src),'--output',str(out),
                 '--mode','source-bitrate','--encoder','libx264']
            first=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            from mp4 import media_info
            tracks=media_info(out/'sample.mp4');v=next(t for t in tracks if t['type']=='vide')
            self.assertEqual((v['width'],v['height'],v['samples']),(960,720,75))
            def audio_hash(p):
                return subprocess.check_output([ff,'-nostdin','-loglevel','error','-i',str(p),'-map','0:a:0','-c:a','copy','-f','hash','-'])
            self.assertEqual(audio_hash(src/'sample.mp4'),audio_hash(out/'sample.mp4'))
            modified=(out/'sample.mp4').stat().st_mtime_ns
            second=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual(modified,(out/'sample.mp4').stat().st_mtime_ns)
            status=json.loads((out/'.crop-state/status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'],'complete')
            # Same frame count/duration must not conceal a shifted edit offset.
            import mp4
            altered=out/'altered.mp4';altered.write_bytes((out/'sample.mp4').read_bytes())
            with altered.open('r+b') as f:
                changed=False
                def shift(a,b):
                    nonlocal changed
                    for kind,start,end in mp4.boxes(f,a,b):
                        if kind==b'elst' and not changed:
                            f.seek(start+12);media=struct.unpack('>i',f.read(4))[0]
                            f.seek(start+12);f.write(struct.pack('>i',media+512));changed=True
                        elif kind in (b'moov',b'trak',b'edts'):shift(start,end)
                shift(0,altered.stat().st_size)
            self.assertTrue(changed)
            with self.assertRaisesRegex(AssertionError,'playback offset'):
                mp4.verify(src/'sample.mp4',altered)

    def test_orphan_guard_does_not_restart_a_live_encoder(self):
        import os
        import psutil
        from video_crop import guard_orphan
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            (state/'active-encoder.json').write_text(json.dumps({'pid':os.getpid(),'create_time':psutil.Process().create_time()}))
            with self.assertRaisesRegex(RuntimeError,'still running'):guard_orphan(state)


if __name__=='__main__':unittest.main()
