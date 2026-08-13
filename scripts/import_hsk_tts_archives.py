#!/usr/bin/env python3
"""Import the packaged HSK eSpeak Mandarin TTS archives."""
import argparse
import csv
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

MP3_MAGIC=(b'ID3',b'\xff\xfb',b'\xff\xf3',b'\xff\xf2')
BATCH_RE=re.compile(r'_batch_(\d{3})_')


def validate_row(row,hsk_item,ordinal=None):
    expected={
        'word':hsk_item.get('word',''),
        'traditional':hsk_item.get('traditional',''),
        'pinyin':hsk_item.get('pinyin',''),
        'lexical_pattern':hsk_item.get('lexical_pattern',''),
        'default_surface_pattern':hsk_item.get('default_surface_pattern',''),
    }
    for field,value in expected.items():
        if row.get(field,'')!=value:
            raise ValueError(f"{row.get('hsk_id')}: {field} mismatch ({row.get(field)!r} != {value!r})")
    path=PurePosixPath(row.get('audio_path',''))
    if not path.parts or path.parts[0]!='audio' or '..' in path.parts or path.suffix.lower()!='.mp3':
        raise ValueError(f"{row.get('hsk_id')}: unsafe audio path {path}")
    index=int(row.get('index','0'))
    if ordinal is not None and index!=ordinal:
        raise ValueError(f"{row.get('hsk_id')}: index mismatch ({index} != {ordinal})")
    expected_name=f"{index:05d}_{row['word']}.mp3"
    if path.name!=expected_name:
        raise ValueError(f"{row.get('hsk_id')}: filename mismatch ({path.name!r} != {expected_name!r})")
    expected_batch=f'batch_{(index-1)//100+1:03d}'
    if len(path.parts)<3 or path.parts[1]!=expected_batch:
        raise ValueError(f"{row.get('hsk_id')}: batch mismatch ({path.parts[1] if len(path.parts)>1 else ''} != {expected_batch})")
    return path


def recording(row,audio_path):
    filename=Path(audio_path).name
    return {
        'word':row['word'],
        'source':'hsk_tts_espeak',
        'filename':filename,
        'audio_path':audio_path.as_posix(),
        'speaker':'eSpeak Mandarin',
        'sex':row.get('voice_gender') or None,
        'country':None,
        'region':None,
        'rate':None,
        'surface_pattern':row.get('default_surface_pattern') or None,
        'lexical_pattern':row.get('lexical_pattern') or None,
        'pinyin':row.get('pinyin') or None,
        'hsk_id':row.get('hsk_id') or None,
        'source_url':None,
        'notes':'Synthetic Mandarin pronunciation generated with eSpeak.',
        'source_recording_id':f"hsk-tts-espeak/{row['hsk_id']}",
        'language_code':'zh',
        'language_status':'synthetic_mandarin',
        'license':None,
        'recording_type':'isolated_word',
        'synthetic':True,
        'engine':row.get('engine') or 'eSpeak Mandarin (asia/zh)',
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('archives',help='folder containing full_manifest.csv and batches/*.zip')
    parser.add_argument('--hsk-json',default='data/hsk_words.json')
    parser.add_argument('--audio-root',default='audio/hsk_tts_espeak')
    parser.add_argument('--manifest',default='data/tts_recordings.json')
    args=parser.parse_args()

    root=Path(args.archives)
    rows=list(csv.DictReader((root/'full_manifest.csv').open(encoding='utf-8-sig',newline='')))
    hsk_items=json.loads(Path(args.hsk_json).read_text(encoding='utf-8'))
    hsk_by_id={item['id']:item for item in hsk_items}
    source_paths=[row.get('audio_path') for row in rows]
    if len(rows)!=len(hsk_items) or len({row['hsk_id'] for row in rows})!=len(rows) or len(set(source_paths))!=len(rows):
        raise SystemExit('TTS manifest must contain exactly one row per HSK entry')

    archives={}
    for path in (root/'batches').glob('*.zip'):
        match=BATCH_RE.search(path.name)
        if match:
            archives[int(match.group(1))]=path

    grouped=defaultdict(list)
    for ordinal,row in enumerate(rows,1):
        hsk_item=hsk_by_id.get(row['hsk_id'])
        if not hsk_item or hsk_items[ordinal-1].get('id')!=row['hsk_id']:
            raise SystemExit(f"Unknown HSK ID: {row['hsk_id']}")
        source_path=validate_row(row,hsk_item,ordinal)
        batch_match=re.fullmatch(r'batch_(\d{3})',source_path.parts[1])
        if not batch_match:
            raise SystemExit(f'Invalid batch path: {source_path}')
        grouped[int(batch_match.group(1))].append((row,source_path))

    audio_root=Path(args.audio_root)
    extracted=0; existing=0; recordings=[]
    for batch,items in sorted(grouped.items()):
        archive_path=archives.get(batch)
        if not archive_path:
            raise SystemExit(f'Missing ZIP archive for batch {batch:03d}')
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip():
                raise SystemExit(f'Corrupt ZIP archive: {archive_path}')
            names=set(archive.namelist())
            for row,source_path in items:
                member=source_path.as_posix()
                if member not in names:
                    raise SystemExit(f'Missing {member} in {archive_path.name}')
                relative=Path(*source_path.parts[1:])
                destination=audio_root/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                data=archive.read(member)
                if not data.startswith(MP3_MAGIC):
                    raise SystemExit(f'Invalid MP3: {member}')
                if destination.exists() and destination.read_bytes()==data:
                    existing+=1
                else:
                    temporary=destination.with_suffix('.mp3.part')
                    temporary.write_bytes(data)
                    temporary.replace(destination)
                    extracted+=1
                recordings.append(recording(row,destination))
        print(f'batch {batch:03d}: {len(items)} files',flush=True)

    manifest=Path(args.manifest)
    manifest.parent.mkdir(parents=True,exist_ok=True)
    temporary=manifest.with_suffix('.json.part')
    temporary.write_text(json.dumps(recordings,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(manifest)

    print(f'Complete: extracted={extracted} existing={existing} indexed={len(recordings)}')


if __name__=='__main__':
    main()
