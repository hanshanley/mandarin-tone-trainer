#!/usr/bin/env python3
"""Add sourced English definitions from CC-CEDICT to the HSK JSON."""
import argparse
import gzip
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

CEDICT_URL='https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz'
ENTRY=re.compile(r'^(\S+) (\S+) \[([^]]+)\] /(.+)/$')


def normalize_reading(value):
    tokens=[]
    for token in value.replace('u:','v').replace('ü','v').lower().split():
        match=re.fullmatch(r'([a-zv]+)([1-5])',token)
        if match:
            tokens.append(match.group(1)+match.group(2))
    return tuple(tokens)


def expected_reading(item):
    return tuple(
        syllable.replace('ü','v')+str(5 if tone==0 else tone)
        for syllable,tone in zip(item.get('pinyin_syllables') or [],item.get('lexical_tones') or [])
    )


def clean_definitions(values):
    definitions=[]
    for value in values:
        value=value.strip()
        if not value or value.startswith('CL:'):
            continue
        definitions.append(value)
        if len(definitions)==4:
            break
    return definitions


def parse_cedict(data):
    entries=defaultdict(list)
    for line in data.splitlines():
        if not line or line.startswith('#'):
            continue
        match=ENTRY.match(line)
        if not match:
            continue
        traditional,simplified,pinyin,definitions=match.groups()
        entries[simplified].append({
            'traditional':traditional,
            'pinyin':pinyin,
            'reading':normalize_reading(pinyin),
            'definitions':definitions.split('/'),
        })
    return entries


def choose_definition(item,candidates):
    expected=expected_reading(item)
    if expected:
        candidates=[candidate for candidate in candidates if candidate['reading']==expected]
    elif len(candidates)!=1:
        return ''
    ranked=[]
    for candidate in candidates:
        definitions=clean_definitions(candidate['definitions'])
        if not definitions:
            continue
        score=0
        if candidate['traditional']!=item.get('traditional'):
            score+=5
        if any(char.isupper() for char in candidate['pinyin']):
            score+=3
        ranked.append((score,'; '.join(definitions)))
    return min(ranked)[1] if ranked else ''


def load_cedict(path):
    if path:
        return gzip.decompress(Path(path).read_bytes()).decode('utf-8')
    request=urllib.request.Request(CEDICT_URL,headers={'User-Agent':'MandarinToneTrainer/0.1'})
    with urllib.request.urlopen(request,timeout=60) as response:
        return gzip.decompress(response.read()).decode('utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--hsk-json',default='data/hsk_words.json')
    parser.add_argument('--out',default='data/definitions.json')
    parser.add_argument('--cedict',help='optional local CC-CEDICT .txt.gz file')
    args=parser.parse_args()
    path=Path(args.hsk_json)
    words=json.loads(path.read_text(encoding='utf-8'))
    entries=parse_cedict(load_cedict(args.cedict))
    definitions={}
    for item in words:
        definition=choose_definition(item,entries.get(item.get('word',''),[]))
        if definition:
            definitions[item['id']]=definition
    output=Path(args.out)
    temporary=output.with_suffix('.json.part')
    temporary.write_text(json.dumps(definitions,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(output)
    print(f'Added definitions to {len(definitions)}/{len(words)} HSK entries')


if __name__=='__main__':
    main()
