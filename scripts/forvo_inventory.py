#!/usr/bin/env python3
"""Inventory every Mandarin [zh] pronunciation Forvo reports for HSK words.

This uses Forvo's official word-pronunciations API with language=zh. It stores
speaker/pronunciation metadata, not audio. Forvo's API currently says generated
audio links expire after two hours and API audio must not be cached, so this
script intentionally does not bulk-download API audio.
"""
import argparse, json, os, time, urllib.parse, urllib.request
from pathlib import Path

BASE='https://apifree.forvo.com/key/{key}/format/json/action/word-pronunciations/word/{word}/language/zh/order/rate-desc/limit/100'

def fetch(key, word):
    url=BASE.format(key=urllib.parse.quote(key,safe=''), word=urllib.parse.quote(word,safe=''))
    req=urllib.request.Request(url, headers={'User-Agent':'MandarinToneTrainer/0.1'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--hsk-json', default='data/hsk_words.json')
    ap.add_argument('--out', default='data/forvo_inventory.jsonl')
    ap.add_argument('--api-key', default=os.getenv('FORVO_API_KEY'))
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--limit-words', type=int, default=None)
    ap.add_argument('--sleep', type=float, default=.15)
    args=ap.parse_args()
    if not args.api_key:
        raise SystemExit('Set FORVO_API_KEY or pass --api-key.')
    words=json.loads(Path(args.hsk_json).read_text(encoding='utf-8'))
    words=words[args.start: args.start+args.limit_words if args.limit_words else None]
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    mode='a' if args.start else 'w'
    with open(args.out,mode,encoding='utf-8') as out:
        for n,item in enumerate(words,args.start):
            try:
                payload=fetch(args.api_key,item['word'])
                entries=[]
                for x in payload.get('items',[]):
                    entries.append({k:x.get(k) for k in [
                        'id','word','original','addtime','hits','username','sex','country','code','langname','rate','num_votes','pathmp3','pathogg'
                    ]})
                rec={'hsk_id':item['id'],'word':item['word'],'language':'zh','count':len(entries),'pronunciations':entries}
            except Exception as e:
                rec={'hsk_id':item['id'],'word':item['word'],'language':'zh','error':str(e),'count':0,'pronunciations':[]}
            out.write(json.dumps(rec,ensure_ascii=False)+'\n'); out.flush()
            print(n,item['word'],rec['count'])
            time.sleep(args.sleep)

if __name__=='__main__': main()
