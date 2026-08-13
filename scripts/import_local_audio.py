#!/usr/bin/env python3
"""Index locally obtained human Mandarin audio.

Folder convention:
  imports/<source>/<WORD>/<any-audio-files>
Examples:
  imports/forvo/公司/rhapsodia.mp3
  imports/forvo/公司/zizi.mp3

Sidecar JSON with the same stem is optional and can contain speaker, sex,
country, region, rate, surface_pattern, notes, source_url.
"""
import argparse, json, shutil
from pathlib import Path
AUDIO={'.mp3','.wav','.ogg','.m4a','.webm','.flac'}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--imports', default='imports')
    ap.add_argument('--audio-root', default='audio')
    ap.add_argument('--manifest', default='data/recordings.json')
    args=ap.parse_args()
    base=Path(args.imports); outroot=Path(args.audio_root); recs=[]
    if Path(args.manifest).exists():
        try: recs=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        except: recs=[]
    known={(r.get('source'),r.get('word'),r.get('filename')) for r in recs}
    for source_dir in base.iterdir() if base.exists() else []:
        if not source_dir.is_dir(): continue
        source=source_dir.name
        for word_dir in source_dir.iterdir():
            if not word_dir.is_dir(): continue
            word=word_dir.name
            for f in word_dir.iterdir():
                if f.suffix.lower() not in AUDIO: continue
                destdir=outroot/source/word; destdir.mkdir(parents=True,exist_ok=True)
                dest=destdir/f.name
                if not dest.exists(): shutil.copy2(f,dest)
                side=f.with_suffix('.json'); meta={}
                if side.exists():
                    try: meta=json.loads(side.read_text(encoding='utf-8'))
                    except Exception: pass
                key=(source,word,f.name)
                if key in known: continue
                rec={
                    'word':word,'source':source,'filename':f.name,
                    'audio_path':str(dest.as_posix()),
                    'speaker':meta.get('speaker',f.stem),
                    'sex':meta.get('sex'),'country':meta.get('country'),'region':meta.get('region'),
                    'rate':meta.get('rate'),'surface_pattern':meta.get('surface_pattern'),
                    'source_url':meta.get('source_url'),'notes':meta.get('notes',''),
                    'source_recording_id':meta.get('source_recording_id'),
                    'language_code':meta.get('language_code','zh'),
                    'language_status':meta.get('language_status','verified_mandarin'),
                    'license':meta.get('license'),'recording_type':meta.get('recording_type','isolated_word')
                }
                recs.append(rec); known.add(key)
    Path(args.manifest).parent.mkdir(parents=True,exist_ok=True)
    Path(args.manifest).write_text(json.dumps(recs,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Indexed {len(recs)} recordings')

if __name__=='__main__': main()
