#!/usr/bin/env python3
"""Add small silence buffers to tightly trimmed audio-cmn syllable clips."""
import argparse, subprocess, tempfile
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',default='audio/audio_cmn/syllabs')
    ap.add_argument('--output',default='audio/audio_cmn/syllabs-padded')
    ap.add_argument('--lead-ms',type=int,default=120)
    ap.add_argument('--tail-ms',type=int,default=180)
    ap.add_argument('--bitrate',default='64k')
    ap.add_argument('--overwrite',action='store_true')
    args=ap.parse_args()
    root=Path(args.input); output=Path(args.output)
    if root.resolve()==output.resolve():
        raise SystemExit('Refusing in-place padding; choose a separate --output directory')
    output.mkdir(parents=True,exist_ok=True)
    files=sorted(root.glob('*.mp3')); processed=0; existing=0
    with tempfile.TemporaryDirectory(prefix='tone-trainer-syllables-') as temp:
        temp_root=Path(temp)
        for i,src in enumerate(files,1):
            final=output/src.name
            if final.exists() and final.stat().st_size and not args.overwrite:
                existing+=1
                continue
            dst=temp_root/src.name
            delay=f'{args.lead_ms}|{args.lead_ms}'
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(src),'-af',f'adelay={delay},apad=pad_dur={args.tail_ms/1000:.3f}','-c:a','libmp3lame','-b:a',args.bitrate,str(dst)]
            subprocess.run(cmd,check=True)
            dst.replace(final); processed+=1
            if i%100==0 or i==len(files):print(f'{i}/{len(files)} processed',flush=True)
    print(f'Padded {processed} syllable clips; {existing} existing under {output}')

if __name__=='__main__':main()
