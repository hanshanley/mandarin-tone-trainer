#!/usr/bin/env python3
"""Index Mandarin Common Voice clips containing HSK words.

This does NOT pretend the whole sentence is an isolated-word recording. It
creates contextual clip records. These can later be word-aligned/cropped, while
already being useful for the app's harder 'in context' listening mode.
"""
import argparse,csv,json,re
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--tsv',required=True)
    ap.add_argument('--clips-dir',required=True)
    ap.add_argument('--hsk-json',default='data/hsk_words.json')
    ap.add_argument('--out',default='data/common_voice_context.jsonl')
    args=ap.parse_args()
    words=json.loads(Path(args.hsk_json).read_text(encoding='utf-8'))
    targets={x['word']:x for x in words if x['word']}
    # Longest first reduces redundant substring matches somewhat.
    ordered=sorted(targets,key=len,reverse=True)
    with open(args.tsv,encoding='utf-8') as f, open(args.out,'w',encoding='utf-8') as o:
        rd=csv.DictReader(f,delimiter='\t')
        count=0
        for r in rd:
            text=r.get('sentence','')
            hits=[w for w in ordered if w in text]
            if not hits: continue
            clip=str(Path(args.clips_dir)/(r.get('path') or ''))
            for w in hits:
                rec={'word':w,'source':'common_voice','kind':'context','audio_path':clip,'sentence':text,
                     'speaker':r.get('client_id'),'age':r.get('age'),'gender':r.get('gender'),
                     'accent':r.get('accents') or r.get('accent'),'locale':r.get('locale')}
                o.write(json.dumps(rec,ensure_ascii=False)+'\n'); count+=1
        print('Indexed contextual occurrences:',count)
if __name__=='__main__': main()
