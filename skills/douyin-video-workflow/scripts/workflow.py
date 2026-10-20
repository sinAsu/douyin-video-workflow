"""Local Douyin download records, strict variant selection, and resumable HTTP."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit


def geometry(width, height):
    if height % 6:
        raise ValueError('Cannot retain height and exact even-pixel 4:3 geometry')
    cropped = height * 4 // 3
    if width < cropped or (width-cropped) % 4:
        raise ValueError('Cannot crop both sides symmetrically on the 4:2:0 pixel grid')
    return cropped, height, (width-cropped)//2


def is_media_url(url):
    u = urlsplit(url)
    return u.scheme == 'https' and (u.hostname or '').endswith('.douyinvod.com') and not u.username


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def signature(path):
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode('ascii'))
    with path.open('rb') as f:
        for offset in sorted({0, max(0, size//2-32768), max(0, size-65536)}):
            f.seek(offset); digest.update(str(offset).encode()+b':'+f.read(65536))
    return {'size': size, 'sample_sha256': digest.hexdigest()}


def find_completed(record, output):
    previous = Path(record['path'])
    for p in [previous, *[p for p in output.glob('*.mp4') if p != previous]]:
        if p.is_file() and p.stat().st_size == record['size'] and signature(p)['sample_sha256'] == record['sample_sha256']:
            return p
    return None


def variants(detail):
    v = detail.get('video') or {}
    entries = list(v.get('bit_rate') or [])
    # Some replies expose H.264 only through the explicit fallback field.
    for field, codec in [('play_addr_h264','h264'), ('play_addr_265','hevc')]:
        if v.get(field):
            entries.append({'format':'mp4', 'play_addr':v[field],
                            'is_h265':int(codec=='hevc'), 'bit_rate':0})
    result = []
    for entry in entries:
        if entry.get('format') != 'mp4':
            continue  # DASH video-only needs separate audio + remux, never pretend it is muxed.
        addr = entry.get('play_addr') or {}
        urls = [u for u in addr.get('url_list', []) if is_media_url(u)]
        if not urls or not addr.get('width') or not addr.get('height') or not addr.get('data_size'):
            continue
        codec = 'hevc' if entry.get('is_h265') or entry.get('is_bytevc1') else 'h264'
        result.append({'id':str(detail.get('aweme_id','')), 'title':detail.get('desc',''),
            'variant':addr.get('url_key') or f"{codec}_{addr['width']}x{addr['height']}_{addr['data_size']}",
            'codec':codec, 'width':addr['width'], 'height':addr['height'],
            'bitrate':entry.get('bit_rate',0), 'size':addr['data_size'],
            'md5':addr.get('file_hash'), 'urls':urls})
    return result


def select_variant(detail, codec):
    options = [v for v in variants(detail) if codec == 'any' or v['codec'] == codec]
    if not options:
        raise ValueError(f'No muxed MP4 variant for codec={codec}; inspect variants, do not silently transcode')
    return max(options, key=lambda v:(v['height'], v['width'], v['bitrate'], v['size']))


def fetch(url, partial, expected, timeout=60):
    """Per-read timeout only. Ignore-Range responses restart, never append."""
    offset = partial.stat().st_size if partial.exists() else 0
    if offset == expected:
        return
    if offset > expected:
        raise ValueError('Partial exceeds source size; keep it and inspect identity')
    headers = {'Referer':'https://www.douyin.com/', 'User-Agent':'Mozilla/5.0'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        mode = 'wb'
        if r.status == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', r.headers.get('Content-Range',''))
            if not match or int(match[1]) != offset or int(match[3]) != expected:
                raise ValueError('Mismatching HTTP range or remote file identity')
            mode = 'ab' if offset else 'wb'
        elif r.status != 200:
            raise ValueError(f'Unexpected response {r.status}')
        with partial.open(mode) as f:
            while chunk := r.read(1024*1024):
                f.write(chunk)
        if partial.stat().st_size != expected:
            raise ValueError('Incomplete response; partial retained for resume')


class Lock:
    def __init__(self, path): self.path = path
    def __enter__(self):
        self.f = self.path.open('a+b')
        if self.f.tell() == 0: self.f.write(b'0'); self.f.flush()
        self.f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close(); raise RuntimeError('Another worker owns this output directory')
        return self
    def __exit__(self, *_): self.f.close()


def download(manifest, output):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    state = output/'.workflow-state'; state.mkdir(exist_ok=True)
    items = json.loads(Path(manifest).read_text(encoding='utf-8'))
    with Lock(state/'download.lock'):
        registry = state/'downloads.json'
        records = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
        for item in items:
            if not re.fullmatch(r'\d{10,25}', item['id']): raise ValueError('Invalid video ID')
            if not item.get('variant') or item['codec'] not in ('h264','hevc'): raise ValueError('Missing variant identity')
            key = item['id']+':'+item['variant']
            record = records.get(key)
            found = find_completed(record, output) if record else None
            if found:
                record['path'] = str(found); atomic_json(registry, records)
                print('SKIP', item['id'], found.name, flush=True); continue
            tag = hashlib.sha256(item['variant'].encode()).hexdigest()[:10]
            target = output/f"{item['id']}_{item['codec']}_{item['height']}p_{tag}.mp4"
            partial = target.with_suffix('.mp4.part')
            # A crash after rename is recoverable only after size/hash/MP4 checks.
            if not target.exists():
                error = None
                for attempt in range(8):
                    try:
                        urls = [u for u in item['urls'] if is_media_url(u)]
                        if not urls: raise ValueError('No approved Douyin CDN URL')
                        fetch(urls[attempt % len(urls)], partial, int(item['size']))
                        error = None; break
                    except urllib.error.HTTPError as exc:
                        if exc.code in (401,403,404,410):
                            raise RuntimeError('Media URL expired/rejected: re-resolve the same variant; partial kept') from None
                        error = exc
                    except (OSError, ValueError) as exc: error = exc
                    if attempt < 7: time.sleep(3)
                if error: raise RuntimeError('Download failed; partial retained') from None
                candidate = partial
            else: candidate = target
            if candidate.stat().st_size != int(item['size']): raise ValueError('Source size mismatch')
            if item.get('md5'):
                digest = hashlib.md5()
                with candidate.open('rb') as f:
                    while chunk := f.read(8*1024*1024): digest.update(chunk)
                if digest.hexdigest() != item['md5']: raise ValueError('Source MD5 mismatch; file retained')
            from mp4 import media_info
            tracks = media_info(candidate)
            video = next(t for t in tracks if t['type']=='vide')
            actual = 'h264' if video['codecs']==['avc1'] else 'hevc' if video['codecs'] in (['hvc1'],['hev1']) else 'unknown'
            if actual != item['codec'] or video['width'] != item['width'] or video['height'] != item['height']:
                raise ValueError('Downloaded codec/dimensions differ from selected variant')
            if candidate != target: candidate.rename(target)
            records[key] = {'path':str(target), 'id':item['id'], 'variant':item['variant'], **signature(target)}
            atomic_json(registry, records);print('DONE', item['id'], target.name, flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    d = sub.add_parser('download');d.add_argument('--manifest',required=True);d.add_argument('--output',required=True)
    d.add_argument('--background',action='store_true')
    s = sub.add_parser('select');s.add_argument('--detail',required=True);s.add_argument('--codec',choices=['any','h264','hevc'],default='any');s.add_argument('--manifest',required=True)
    a = p.parse_args()
    if a.command == 'download':
        if a.background:
            state=Path(a.output).resolve()/'.workflow-state';state.mkdir(parents=True,exist_ok=True)
            command=[sys.executable,'-u',str(Path(__file__).resolve()),'download','--manifest',str(Path(a.manifest).resolve()),'--output',str(Path(a.output).resolve())]
            with (state/'worker.log').open('ab') as log:
                worker=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            atomic_json(state/'worker.json',{'pid':worker.pid});print('STARTED',worker.pid,'check log and file growth')
        else: download(a.manifest,a.output)
    else:
        data = json.loads(Path(a.detail).read_text(encoding='utf-8'))
        detail = data.get('aweme_detail') or (data.get('body') or {}).get('aweme_detail') or data
        item = select_variant(detail,a.codec);atomic_json(a.manifest,[item])
        print(json.dumps({k:v for k,v in item.items() if k not in ('urls','title')},ensure_ascii=True))


if __name__ == '__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
