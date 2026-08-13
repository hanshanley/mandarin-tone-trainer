#!/usr/bin/env python3
"""Add validated pinyin syllables to the HSK JSON for audio lookup."""
import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

TONE_MARKS = str.maketrans('āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ', 'aaaaeeeeiiiioooouuuuvvvv')


def plain(s):
    value=s.lower().replace('ü','v').translate(TONE_MARKS)
    return re.sub(r'[^a-zv]', '', value)


def load_vocab(audio_root):
    vocab={'r'}
    for path in Path(audio_root).glob('cmn-*.mp3'):
        match=re.fullmatch(r'cmn-_?([a-zv]+)[1-5]\.mp3',path.name)
        if not match:
            continue
        value=match.group(1)
        vocab.add('ju' if value=='jv' else value)
    if len(vocab)<400:
        raise SystemExit(f'Expected the audio-cmn syllable inventory under {audio_root}')
    return sorted(vocab,key=lambda value:(-len(value),value))


def normalized_pinyin(value):
    value=value.split('/',1)[0]
    value=re.sub(r'\([^)]*\)','',value)
    letters=[]
    boundaries=set()
    for char in value:
        normalized=plain(char)
        if normalized:
            letters.append(normalized)
        elif char in " -'’·":
            boundaries.add(sum(len(part) for part in letters))
    return ''.join(letters),boundaries


def split(item, vocab):
    text,boundaries=normalized_pinyin(item.get('pinyin',''))
    n=len(item.get('lexical_tones',[]))
    if not text or not n:
        return None

    @lru_cache(maxsize=None)
    def walk(pos,left):
        if left==0:
            return ((),) if pos==len(text) else ()
        results=[]
        for x in vocab:
            if text.startswith(x,pos):
                end=pos+len(x)
                if any(pos<boundary<end for boundary in boundaries):
                    continue
                for rest in walk(end,left-1):
                    results.append((x,)+rest)
        return tuple(results[:200])

    candidates=walk(0,n)
    if not candidates:
        return None

    def score(parts):
        position=0
        penalty=0
        for index,part in enumerate(parts):
            if index and position not in boundaries and part[0] in 'aeo':
                penalty+=100
            if part in {'m','n','ng','hm','hng','nia'}:
                penalty+=5
            position+=len(part)
        penalty-=sum(1 for boundary in boundaries if boundary in {
            sum(len(part) for part in parts[:index]) for index in range(1,len(parts))
        })
        return penalty

    best_score=min(score(parts) for parts in candidates)
    best=sorted(set(parts for parts in candidates if score(parts)==best_score))
    return list(best[0]) if len(best)==1 else None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--hsk-json',default='data/hsk_words.json')
    parser.add_argument('--audio-root',default='audio/audio_cmn/syllabs')
    args=parser.parse_args()
    path=Path(args.hsk_json); words=json.loads(path.read_text(encoding='utf8'))
    vocab=load_vocab(args.audio_root)
    invalid=[]
    for item in words:
        syllables=split(item,vocab)
        item['pinyin_syllables']=syllables or []
        if not syllables:
            invalid.append((item.get('word',''),item.get('pinyin','')))
    path.write_text(json.dumps(words,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f'Updated {len(words)} words; invalid={len(invalid)}')
    for word,pinyin in invalid:
        print(f'  {word}\t{pinyin}')
if __name__=='__main__':main()
