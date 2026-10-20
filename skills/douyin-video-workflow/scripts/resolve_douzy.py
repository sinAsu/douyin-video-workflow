"""Use an already running, logged-in Douzy local page bridge. Never export tokens."""
import argparse
import json
from pathlib import Path
import re
import urllib.request

from workflow import atomic_json, select_variant, variants


def bridge_environment():
    import psutil
    for process in psutil.process_iter(['name']):
        if 'douyin-dl-sidecar' not in (process.info['name'] or ''):
            continue
        try: env = process.environ()
        except (psutil.AccessDenied, psutil.NoSuchProcess): continue
        if env.get('DOUYIN_PAGE_BRIDGE_URL') and env.get('DOUYIN_PAGE_BRIDGE_TOKEN'):
            from urllib.parse import urlsplit
            u = urlsplit(env['DOUYIN_PAGE_BRIDGE_URL'])
            if u.scheme != 'http' or u.hostname != '127.0.0.1':
                raise RuntimeError('Expected a loopback Douzy bridge')
            return env
    raise RuntimeError('No running Douzy page bridge. Start Douzy and log in manually, or supply exported detail JSON.')


def resolve(video_id, env):
    query = {'device_platform':'webapp','aid':'6383','channel':'channel_pc_web',
        'aweme_id':video_id,'pc_client_type':'1','version_code':'190500','version_name':'19.5.0',
        'cookie_enabled':'true','platform':'PC','browser_language':'zh-CN','browser_platform':'Win32',
        'browser_name':'Chrome','browser_version':'130.0.0.0','browser_online':'true',
        'engine_name':'Blink','engine_version':'130.0.0.0','os_name':'Windows','os_version':'10',
        'cpu_core_num':'16','device_memory':'8','screen_width':'1920','screen_height':'1080',
        'update_version_code':'170400','support_h265':'1','support_dash':'1'}
    req = urllib.request.Request(env['DOUYIN_PAGE_BRIDGE_URL']+'/fetch',
        data=json.dumps({'path':'/aweme/v1/web/aweme/detail/','query':query}).encode(),
        headers={'Content-Type':'application/json','x-douzy-page-bridge':env['DOUYIN_PAGE_BRIDGE_TOKEN']})
    with urllib.request.urlopen(req,timeout=60) as response: data=json.load(response)
    body=data.get('body') or {}
    if not data.get('ok') or body.get('status_code') != 0 or not body.get('aweme_detail'):
        raise RuntimeError(f"Detail unavailable for {video_id}; bridge={data.get('error_code')} status={body.get('status_code')}. Check login or site verification manually.")
    return body['aweme_detail']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--links',type=Path,required=True,help='UTF-8 text with full douyin.com/video/ID links')
    p.add_argument('--codec',choices=['any','h264','hevc'],default='any')
    p.add_argument('--manifest',type=Path,required=True)
    a=p.parse_args()
    ids=list(dict.fromkeys(re.findall(r'https://(?:www\.)?douyin\.com/video/(\d{10,25})',a.links.read_text(encoding='utf-8-sig'))))
    if not ids: raise ValueError('No full video URLs found. Expand short share URLs in the browser first.')
    env=bridge_environment();items=[]
    for video_id in ids:
        detail=resolve(video_id,env)
        print('OPTIONS',video_id,json.dumps([{k:v for k,v in option.items() if k not in ('urls','title','md5')} for option in variants(detail)],ensure_ascii=True),flush=True)
        selected=select_variant(detail,a.codec)
        if selected['id'] != video_id: raise ValueError('Resolved ID mismatch')
        items.append(selected)
        # Progress survives the next video's failure; update one entry per ID.
        old=json.loads(a.manifest.read_text(encoding='utf-8')) if a.manifest.exists() else []
        old=[i for i in old if i['id'] != video_id]+[selected]
        atomic_json(a.manifest,old)
    print('Resolved',len(items),'video(s); signed URLs saved locally, not printed.')


if __name__=='__main__': main()
