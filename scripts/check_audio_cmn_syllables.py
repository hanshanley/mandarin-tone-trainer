#!/usr/bin/env python3
"""Find likely anomalous audio-cmn syllable clips with internal silence."""
import argparse, re, subprocess
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default='audio/audio_cmn/syllabs'); ap.add_argument('--threshold',type=float,default=.18); args=ap.parse_args()
    bad=[]
    for path in sorted(Path(args.input).glob('*.mp3')):
        result=subprocess.run(['ffmpeg','-hide_banner','-i',str(path),'-af','silencedetect=noise=-45dB:d=0.03','-f','null','-'],capture_output=True,text=True)
        starts=[float(x) for x in re.findall(r'silence_start: ([0-9.]+)',result.stderr)]
        ends=[float(x) for x in re.findall(r'silence_end: ([0-9.]+)',result.stderr)]
        internal=[end-start for start,end in zip(starts[1:],ends[1:])]
        if any(duration>=args.threshold for duration in internal): bad.append((path.name,max(internal)))
    for name,duration in bad: print(f'{name}\tinternal_silence={duration:.3f}s')
    print(f'flagged={len(bad)}')

if __name__=='__main__': main()
