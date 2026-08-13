#!/usr/bin/env python3
"""Add small silence buffers to tightly trimmed audio-cmn syllable clips."""
import argparse, subprocess, tempfile
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',default='audio/audio_cmn/syllabs')
    ap.add_argument('--lead-ms',type=int,default=120)
    ap.add_argument('--tail-ms',type=int,default=180)
    ap.add_argument('--bitrate',default='64k')
    args=ap.parse_args()
    root=Path(args.input); files=sorted(root.glob('*.mp3'))
    with tempfile.TemporaryDirectory(prefix='tone-trainer-syllables-') as temp:
        temp_root=Path(temp)
        for i,src in enumerate(files,1):
            dst=temp_root/src.name
            delay=f'{args.lead_ms}|{args.lead_ms}'
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(src),'-af',f'adelay={delay},apad=pad_dur={args.tail_ms/1000:.3f}','-c:a','libmp3lame','-b:a',args.bitrate,str(dst)]
            subprocess.run(cmd,check=True)
            dst.replace(src)
            if i%100==0 or i==len(files):print(f'{i}/{len(files)} processed',flush=True)
    print(f'Padded {len(files)} syllable clips')

if __name__=='__main__':main()
