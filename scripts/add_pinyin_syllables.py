#!/usr/bin/env python3
"""Add machine-segmented pinyin syllables to the HSK JSON for audio lookup."""
import json, re, urllib.request
from pathlib import Path

LIST='https://raw.githubusercontent.com/hugolpz/audio-cmn/master/lists/pin1yin1-syllables-all-by-tones.txt'
def plain(s):
    value=(s.lower().replace('ü','v').replace('ǖ','v').replace('ǘ','v').replace('ǚ','v').replace('ǜ','v')
            .translate(str.maketrans('āáǎàēéěèīíǐìōóǒòūúǔù','aaaaeeeeiiiioooouuuu')))
    return re.sub(r'[^a-zv]', '', value)
def split(item, syllables):
    if ' ' in item['pinyin']:
        parts=item['pinyin'].split()
        if len(parts)==len(syllables): return [plain(x) for x in parts]
    text=plain(item['pinyin']).replace(' ',''); n=len(syllables)
    vocab=sorted({re.sub(r'[1-5]$','',x) for x in open('/private/tmp/audio_cmn_pinyin_list.txt',encoding='utf8').read().split()},key=len,reverse=True)
    def walk(pos,left):
        if left==0:return [] if pos==len(text) else None
        for x in vocab:
            if text.startswith(x,pos):
                rest=walk(pos+len(x),left-1)
                if rest is not None:return [x]+rest
        return None
    return walk(0,n) or [text]
def main():
    path=Path('data/hsk_words.json'); words=json.loads(path.read_text(encoding='utf8'))
    data=urllib.request.urlopen(LIST,timeout=30).read(); Path('/private/tmp/audio_cmn_pinyin_list.txt').write_bytes(data)
    for item in words:item['pinyin_syllables']=split(item,item.get('lexical_tones',[]))
    path.write_text(json.dumps(words,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f'Updated {len(words)} words')
if __name__=='__main__':main()
