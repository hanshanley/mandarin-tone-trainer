#!/usr/bin/env python3
"""Download audio-cmn's tone-specific syllable clips."""
import argparse, json, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TREE='https://api.github.com/repos/hugolpz/audio-cmn/git/trees/{revision}?recursive=1'
RAW='https://raw.githubusercontent.com/hugolpz/audio-cmn/{revision}/{quality}/syllabs/'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--audio-root',default='audio/audio_cmn/syllabs'); ap.add_argument('--quality',default='64k',choices=['64k','24k-abr','18k-abr']); ap.add_argument('--revision',default='master'); ap.add_argument('--workers',type=int,default=12); args=ap.parse_args()
    req=urllib.request.Request(TREE.format(revision=urllib.parse.quote(args.revision,safe='')),headers={'User-Agent':'MandarinToneTrainer/0.1'})
    with urllib.request.urlopen(req,timeout=30) as r: tree=json.load(r)
    prefix=f'{args.quality}/syllabs/'
    names=[x['path'][len(prefix):] for x in tree['tree'] if x.get('type')=='blob' and x['path'].startswith(prefix) and x['path'].endswith('.mp3')]
    root=Path(args.audio_root); root.mkdir(parents=True,exist_ok=True)
    def get(name):
        out=root/name; out.parent.mkdir(parents=True,exist_ok=True)
        if out.exists() and out.stat().st_size:return 'existing',name
        url=RAW.format(revision=args.revision,quality=args.quality)+urllib.parse.quote(name,safe='')
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'MandarinToneTrainer/0.1'}),timeout=30) as r:data=r.read()
        out.write_bytes(data); return 'downloaded',name
    counts={'downloaded':0,'existing':0}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for f in as_completed([pool.submit(get,n) for n in names]):
            status,name=f.result(); counts[status]+=1
    print(f"{counts['downloaded']} downloaded, {counts['existing']} existing, {len(names)} total")

if __name__=='__main__': main()
