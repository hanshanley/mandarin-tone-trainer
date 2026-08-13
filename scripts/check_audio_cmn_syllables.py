#!/usr/bin/env python3
"""Find likely anomalous audio-cmn syllable clips with internal silence."""
import argparse, re, subprocess
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default='audio/audio_cmn/syllabs'); ap.add_argument('--threshold',type=float,default=.18); ap.add_argument('--edge-tolerance',type=float,default=.03); args=ap.parse_args()
    bad=[]
    for path in sorted(Path(args.input).glob('*.mp3')):
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],capture_output=True,text=True,check=True)
        duration=float(probe.stdout.strip())
        result=subprocess.run(['ffmpeg','-hide_banner','-i',str(path),'-af','silencedetect=noise=-45dB:d=0.03','-f','null','-'],capture_output=True,text=True,check=True)
        starts=[float(x) for x in re.findall(r'silence_start: ([0-9.]+)',result.stderr)]
        ends=[float(x) for x in re.findall(r'silence_end: ([0-9.]+)',result.stderr)]
        internal=[end-start for start,end in zip(starts,ends) if start>args.edge_tolerance and duration-end>args.edge_tolerance]
        if any(gap>=args.threshold for gap in internal): bad.append((path.name,max(internal)))
    for name,duration in bad: print(f'{name}\tinternal_silence={duration:.3f}s')
    print(f'flagged={len(bad)}')

if __name__=='__main__': main()
