"""Preview, encode and verify centered 4:3 crops without modifying sources."""
import argparse
import datetime
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

import imageio_ffmpeg
import psutil
from mp4 import media_info, verify
from workflow import Lock, atomic_json, geometry, signature

HIDDEN = {'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)}


def ffmpeg():
    return os.environ.get('FFMPEG') or shutil.which('ffmpeg') or imageio_ffmpeg.get_ffmpeg_exe()


def video(path): return next(t for t in media_info(path) if t['type']=='vide')


def preflight(source):
    result=subprocess.run([ffmpeg(),'-nostdin','-hide_banner','-i',str(source)],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',**HIDDEN)
    lines=[line for line in result.stderr.splitlines() if 'Video:' in line]
    if len(lines)!=1: raise ValueError('Require exactly one video stream: '+source.name)
    line=lines[0]
    sar=re.search(r'SAR (\d+):(\d+)',line)
    rotations=re.findall(r'rotation of ([-+.\d]+)',result.stderr)
    if (not re.search(r'\byuv420p(?=[,(\s])',line) or not sar or sar[1]!=sar[2]
            or any(abs(float(value))>0.01 for value in rotations)
            or 'bt2020' in line or 'smpte2084' in line or 'arib-std-b67' in line):
        raise ValueError('Only unrotated 8-bit YUV420 square-pixel SDR is supported; inspect '+source.name)
    coded=re.search(r'\b(\d{2,5})x(\d{2,5})\b',line)
    info=video(source)
    if not coded or (int(coded[1]),int(coded[2]))!=(info['width'],info['height']):
        raise ValueError('Coded and displayed dimensions differ: '+source.name)
    geometry(info['width'],info['height'])
    return info


def guard_orphan(state):
    active=state/'active-encoder.json'
    if not active.exists():return
    saved=json.loads(active.read_text(encoding='utf-8'))
    if not saved:return
    try:
        process=psutil.Process(saved['pid'])
        if abs(process.create_time()-saved['create_time'])<1:
            raise RuntimeError('An encoder from an earlier worker is still running. Inspect its progress and wait; do not move its output or start another encoder.')
    except psutil.NoSuchProcess:pass


def encoding(encoder, mode, bitrate):
    common=['-c:v',encoder,'-profile:v','high']
    if encoder=='h264_nvenc': common+=['-preset','p5']
    elif encoder in ('h264_qsv','libx264'): common+=['-preset','slow' if encoder=='h264_qsv' else 'medium']
    elif encoder=='h264_amf': common+=['-quality','quality']
    if mode=='source-bitrate':
        # Target average video bitrate, not the container's audio+video rate.
        common+=['-b:v',str(bitrate)]
        if encoder=='h264_nvenc': common+=['-rc','vbr']
        elif encoder=='h264_amf': common+=['-rc','vbr_peak','-maxrate',str(bitrate*2)]
    elif encoder=='h264_qsv': common+=['-global_quality','18']
    elif encoder=='h264_nvenc': common+=['-rc','vbr','-cq','19','-b:v','0']
    elif encoder=='h264_amf': common+=['-rc','cqp','-qp_i','18','-qp_p','20']
    else: common+=['-crf','18']
    return common


def choose_encoder(source, requested, mode, rate, crop):
    candidates=['h264_nvenc','h264_qsv','h264_amf','libx264'] if requested=='auto' else [requested]
    failures=[]
    for encoder in candidates:
        command=[ffmpeg(),'-nostdin','-hide_banner','-loglevel','error','-ss','0','-i',str(source),
                 '-t','2','-map','0:v:0','-vf',crop,*encoding(encoder,mode,rate),'-an','-f','null','-']
        try:
            result=subprocess.run(command,capture_output=True,timeout=60,**HIDDEN)
            if result.returncode==0:
                print('ENCODER',encoder,flush=True);return encoder
            failures.append(encoder)
        except (OSError,subprocess.TimeoutExpired): failures.append(encoder)
    raise RuntimeError('Actual encoding probe failed: '+', '.join(failures))


def paths(directory):
    files=sorted(Path(directory).resolve().glob('*.mp4'))
    if not files: raise ValueError('No top-level MP4 files in input directory')
    if any(p.stem.endswith('.partial') for p in files): raise ValueError('Input includes unfinished output')
    return files


def checked(source,target):
    tracks=verify(source,target)
    v=next(t for t in tracks if t['type']=='vide')
    # Compare encoded frame counts with source, then decode samples at 3 positions.
    for position in [0,v['duration']/2,max(0,v['duration']-1)]:
        decoded=subprocess.run([ffmpeg(),'-nostdin','-hide_banner','-loglevel','error','-xerror','-ss',str(position),
                        '-i',str(target),'-map','0:v:0','-frames:v','1','-an','-f','framemd5','-'],
                       check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**HIDDEN)
        frames=[line for line in decoded.stdout.splitlines() if line and not line.startswith(b'#')]
        if len(frames)!=1 or decoded.stderr:raise ValueError('Expected one clean decoded frame at '+str(position))
    return tracks


def preview(args):
    from PIL import Image,ImageDraw
    import io
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    for source in paths(args.input):
        info=preflight(source);width,height,x=geometry(info['width'],info['height'])
        position=min(args.at,max(0,info['duration']/2))
        r=subprocess.run([ffmpeg(),'-nostdin','-loglevel','error','-ss',str(position),'-i',str(source),
                          '-frames:v','1','-f','image2pipe','-vcodec','png','-threads','2','-'],capture_output=True,check=True,**HIDDEN)
        original=Image.open(io.BytesIO(r.stdout)).convert('RGB')
        if original.size!=(info['width'],info['height']): raise ValueError('Rotation/display geometry needs explicit handling')
        cropped=original.crop((x,0,x+width,height))
        cropped.save(output/(source.stem+'_4x3.png'))
        original.save(output/(source.stem+'_original.png'))
        marked=original.copy();ImageDraw.Draw(marked).rectangle((x,0,x+width-1,height-1),outline='red',width=max(2,height//200))
        h=360;left=marked.resize((round(marked.width/height*h),h));right=cropped.resize((480,h))
        canvas=Image.new('RGB',(left.width+right.width+30,h+40),'#eeeeee');canvas.paste(left,(0,30));canvas.paste(right,(left.width+30,30))
        ImageDraw.Draw(canvas).text((4,6),f'Original: {info["width"]}x{height}; crop {x}px each side    ->    {width}x{height}',fill='black')
        canvas.save(output/(source.stem+'_comparison.jpg'),quality=95)
        print('PREVIEW',source.name,f'{width}x{height}',f'crop={x}',flush=True)


def run(args):
    sources=paths(args.input);output=Path(args.output).resolve()
    if output==Path(args.input).resolve(): raise ValueError('Output must differ from input')
    output.mkdir(parents=True,exist_ok=True);state=output/'.crop-state';state.mkdir(exist_ok=True)
    with Lock(state/'worker.lock'):
        guard_orphan(state)
        jobs_path=state/'jobs.json';jobs=json.loads(jobs_path.read_text(encoding='utf-8')) if jobs_path.exists() else {}
        status={'state':'running','total':len(sources),'completed':[],'mode':args.mode,'worker_pid':os.getpid()}
        def save():
            status['updated_at']=datetime.datetime.now().astimezone().isoformat();atomic_json(state/'status.json',status)
        save()
        try:
            for index,source in enumerate(sources,1):
                info=preflight(source);w,h,x=geometry(info['width'],info['height']);crop=f'crop={w}:{h}:{x}:0'
                rate=round(info['data_bytes']*8/info['duration'])
                identity={'source':str(source),'signature':signature(source),'mode':args.mode,
                          'requested_encoder':args.encoder,'crop':crop,'video_bitrate_target':rate if args.mode=='source-bitrate' else None}
                old=jobs.get(source.name)
                target=output/source.name;partial=output/(source.stem+'.partial.mp4')
                if (target.exists() or partial.exists()) and (not old or old.get('identity')!=identity):
                    raise ValueError('Existing output belongs to a different or unrecorded source/configuration: '+source.name)
                status.update(current=source.name,index=index,duration=info['duration']);save()
                if target.exists():
                    result=checked(source,target)
                else:
                    jobs[source.name]={'identity':identity};atomic_json(jobs_path,jobs)
                    progress=state/(source.stem+'.progress')
                    finished=partial.exists() and progress.exists() and progress.read_text().rstrip().endswith('progress=end')
                    if not finished:
                        if partial.exists():
                            retained=state/'interrupted';retained.mkdir(exist_ok=True)
                            partial.rename(retained/(str(time.time_ns())+'_'+partial.name))
                        encoder=choose_encoder(source,args.encoder,args.mode,rate,crop)
                        status['encoder']=encoder;save()
                        command=[ffmpeg(),'-nostdin','-hide_banner','-loglevel','warning','-stats_period','10',
                            '-i',str(source),'-map','0:v:0','-map','0:a?','-map_metadata','0','-map_chapters','0',
                            '-vf',crop,*encoding(encoder,args.mode,rate),'-fps_mode','passthrough','-c:a','copy',
                            '-movflags','+faststart','-progress',str(progress),'-n',str(partial)]
                        jobs[source.name]['command']=command;atomic_json(jobs_path,jobs)
                        with (state/(source.stem+'.log')).open('ab') as log:
                            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,**HIDDEN)
                            try:created=psutil.Process(process.pid).create_time()
                            except psutil.NoSuchProcess:created=0
                            atomic_json(state/'active-encoder.json',{'pid':process.pid,'create_time':created,'output':str(partial)})
                            code=process.wait()
                            atomic_json(state/'active-encoder.json',None)
                            if code:raise subprocess.CalledProcessError(code,command)
                    result=checked(source,partial);partial.rename(target)
                jobs[source.name]['verified']={'tracks':result,'output_size':target.stat().st_size}
                atomic_json(jobs_path,jobs);status['completed'].append(source.name);save()
                print('VERIFIED',index,'/',len(sources),source.name,flush=True)
            status.update(state='complete',current=None);save()
        except BaseException as error:
            status.update(state='failed',error=f'{type(error).__name__}: {error}');save();raise


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('preview');a.add_argument('--input',required=True);a.add_argument('--output',required=True);a.add_argument('--at',type=float,default=600)
    a=sub.add_parser('run');a.add_argument('--input',required=True);a.add_argument('--output',required=True)
    a.add_argument('--mode',choices=['source-bitrate','quality'],default='source-bitrate')
    a.add_argument('--encoder',choices=['auto','h264_nvenc','h264_qsv','h264_amf','libx264'],default='auto')
    a.add_argument('--background',action='store_true')
    a=sub.add_parser('status');a.add_argument('--output',required=True)
    a=sub.add_parser('verify');a.add_argument('--input',required=True);a.add_argument('--output',required=True)
    a=sub.add_parser('doctor')
    args=p.parse_args()
    if args.command=='preview':preview(args)
    elif args.command=='run':
        if args.background:
            output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True);state=output/'.crop-state';state.mkdir(exist_ok=True)
            command=[sys.executable,'-u',str(Path(__file__).resolve()),*[x for x in sys.argv[1:] if x!='--background']]
            with (state/'worker.log').open('ab') as log:
                worker=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,**HIDDEN)
            atomic_json(state/'launcher.json',{'pid':worker.pid});print('STARTED',worker.pid,'check status after launch')
        else:run(args)
    elif args.command=='status':
        state=Path(args.output)/'.crop-state';print((state/'status.json').read_text(encoding='utf-8'))
        s=json.loads((state/'status.json').read_text(encoding='utf-8'))
        if s.get('current'):
            f=state/(Path(s['current']).stem+'.progress')
            if f.exists():print(f.read_text()[-900:]);print('progress_age_seconds',round(time.time()-f.stat().st_mtime))
    elif args.command=='verify':
        for source in paths(args.input):checked(source,Path(args.output)/source.name);print('VERIFIED',source.name,flush=True)
    else:
        print('FFMPEG',ffmpeg())
        r=subprocess.run([ffmpeg(),'-hide_banner','-encoders'],capture_output=True,text=True,**HIDDEN)
        print('\n'.join(l for l in r.stdout.splitlines() if any(k in l for k in ('h264_nvenc','h264_qsv','h264_amf','libx264'))))
        print('Listing is not proof of usable hardware. run probes actual input before encoding.')


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
