#!/usr/bin/env python3
"""Find likely anomalous audio-cmn syllable clips and short edge buffers."""
import argparse, math, re, subprocess, sys
from array import array
from pathlib import Path

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
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default='audio/audio_cmn/syllabs'); ap.add_argument('--threshold',type=float,default=.18); ap.add_argument('--edge-tolerance',type=float,default=.03); ap.add_argument('--minimum-lead',type=float,default=.10); ap.add_argument('--minimum-tail',type=float,default=.15); ap.add_argument('--show-edge-files',action='store_true'); args=ap.parse_args()
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
    print(f'internal_silence_flagged={len(bad)} edge_buffer_flagged={len(edge_bad)}')

if __name__=='__main__': main()
