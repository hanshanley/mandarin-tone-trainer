#!/usr/bin/env python3
"""Find likely anomalous or duplicated correction-audio clips."""
import argparse, hashlib, json, math, re, subprocess, sys
from array import array
from collections import defaultdict
from pathlib import Path

def payload_hash(path):
    data=path.read_bytes()
    if data[:3]==b'ID3' and len(data)>=10:
        size=0
        for byte in data[6:10]:
            size=(size<<7)|(byte&0x7f)
        data=data[10+size:]
    return hashlib.sha256(data).hexdigest()

def duplicate_payload_groups(paths):
    groups=defaultdict(list)
    for path in paths:
        groups[payload_hash(path)].append(path)
    return [sorted(group) for group in groups.values() if len(group)>1]

def edge_buffers(path,sample_rate=16000,window_ms=10):
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-f','s16le','-ac','1','-ar',str(sample_rate),'-'])
    samples=array('h'); samples.frombytes(pcm)
    if sys.byteorder!='little':
        samples.byteswap()
    window=max(1,round(sample_rate*window_ms/1000))
    rms=[]
    for start in range(0,len(samples)-window+1,window):
        chunk=samples[start:start+window]
        rms.append(math.sqrt(sum(value*value for value in chunk)/len(chunk))/32768)
    if not rms:
        return 0,0,0
    ordered=sorted(rms)
    noise_floor=ordered[max(0,len(ordered)//5-1)]
    threshold=max(10**(-45/20),max(rms)*10**(-28/20),noise_floor*4)
    active=[index for index,value in enumerate(rms) if value>=threshold]
    if not active:
        return 0,0,len(samples)/sample_rate
    step=window/sample_rate
    return active[0]*step,(len(rms)-1-active[-1])*step,len(samples)/sample_rate

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default='audio/audio_cmn/syllabs'); ap.add_argument('--duplicate-input',action='append'); ap.add_argument('--quality',default='data/correction_audio_quality.json'); ap.add_argument('--threshold',type=float,default=.18); ap.add_argument('--edge-tolerance',type=float,default=.03); ap.add_argument('--minimum-lead',type=float,default=.10); ap.add_argument('--minimum-tail',type=float,default=.15); ap.add_argument('--show-edge-files',action='store_true'); args=ap.parse_args()
    bad=[]; edge_bad=[]
    for path in sorted(Path(args.input).glob('*.mp3')):
        lead,tail,duration=edge_buffers(path)
        if lead<args.minimum_lead or tail<args.minimum_tail:
            edge_bad.append((path.name,lead,tail))
        result=subprocess.run(['ffmpeg','-hide_banner','-i',str(path),'-af','silencedetect=noise=-45dB:d=0.03','-f','null','-'],capture_output=True,text=True,check=True)
        starts=[float(x) for x in re.findall(r'silence_start: ([0-9.]+)',result.stderr)]
        ends=[float(x) for x in re.findall(r'silence_end: ([0-9.]+)',result.stderr)]
        internal=[end-start for start,end in zip(starts,ends) if start>args.edge_tolerance and duration-end>args.edge_tolerance]
        if any(gap>=args.threshold for gap in internal): bad.append((path.name,max(internal)))
    for name,duration in bad: print(f'{name}\tinternal_silence={duration:.3f}s')
    if args.show_edge_files:
        for name,lead,tail in edge_bad: print(f'{name}\tlead={lead:.3f}s\ttail={tail:.3f}s')
    quality=json.loads(Path(args.quality).read_text())
    duplicate_inputs=args.duplicate_input or [args.input,'audio/pinyin_public']
    duplicate_groups=0; unquarantined=0
    for directory in dict.fromkeys(map(Path,duplicate_inputs)):
        if not directory.is_dir():
            continue
        source='audio_cmn' if directory.name=='syllabs' else 'pinyin_public'
        prefix='cmn-' if source=='audio_cmn' else ''
        for group in duplicate_payload_groups(directory.glob('*.mp3')):
            duplicate_groups+=1
            keys=[path.stem.removeprefix(prefix) for path in group]
            healthy=[
                key for key in keys
                if quality[source].get(key,{}).get('status')!='bad'
            ]
            status='quarantined' if len(healthy)<=1 else 'unquarantined'
            if status=='unquarantined':
                unquarantined+=1
            print(f'{source}\tduplicate_payload={"|".join(keys)}\tstatus={status}')
    print(f'internal_silence_flagged={len(bad)} edge_buffer_flagged={len(edge_bad)} duplicate_payload_groups={duplicate_groups} unquarantined_duplicates={unquarantined}')
    if unquarantined:
        raise SystemExit(1)

if __name__=='__main__': main()
