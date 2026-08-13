#!/usr/bin/env python3
"""Download the public-domain mp3-chinese-pinyin-sound correction corpus."""
import argparse
import io
import json
import re
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_URL='https://codeload.github.com/davinfifield/mp3-chinese-pinyin-sound/zip/refs/heads/master'
SOURCE_URL='https://github.com/davinfifield/mp3-chinese-pinyin-sound'
KEY_RE=re.compile(r'([a-zv]+[1-4])\.mp3')
MP3_MAGIC=(b'ID3',b'\xff\xfb',b'\xff\xf3',b'\xff\xf2')


def corpus_members(archive):
    members={}
    for name in archive.namelist():
        path=PurePosixPath(name)
        if len(path.parts)<3 or path.parts[-2]!='mp3':
            continue
        match=KEY_RE.fullmatch(path.name)
        if match:
            members[match.group(1)]=name
    return members


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--audio-root',default='audio/pinyin_public')
    parser.add_argument('--manifest',default='data/pinyin_public_recordings.json')
    args=parser.parse_args()

    request=urllib.request.Request(ARCHIVE_URL,headers={'User-Agent':'MandarinToneTrainer/0.1'})
    with urllib.request.urlopen(request,timeout=120) as response:
        payload=response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip():
            raise SystemExit('Downloaded pinyin corpus ZIP is corrupt')
        members=corpus_members(archive)
        if len(members)<1600:
            raise SystemExit(f'Expected at least 1600 pinyin clips, found {len(members)}')
        root=Path(args.audio_root); root.mkdir(parents=True,exist_ok=True)
        recordings={}; downloaded=0; existing=0
        for key,member in sorted(members.items()):
            data=archive.read(member)
            if not data.startswith(MP3_MAGIC):
                raise SystemExit(f'Invalid MP3 for {key}')
            destination=root/f'{key}.mp3'
            if destination.exists() and destination.read_bytes()==data:
                existing+=1
            else:
                temporary=destination.with_suffix('.mp3.part')
                temporary.write_bytes(data)
                temporary.replace(destination)
                downloaded+=1
            recordings[key]={
                'audio_path':destination.as_posix(),
                'source':'mp3_chinese_pinyin_sound',
                'license':'Unlicense',
                'source_url':f'{SOURCE_URL}/blob/master/mp3/{key}.mp3',
            }

    manifest=Path(args.manifest); manifest.parent.mkdir(parents=True,exist_ok=True)
    temporary=manifest.with_suffix('.json.part')
    temporary.write_text(json.dumps(recordings,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(manifest)
    print(f'Complete: downloaded={downloaded} existing={existing} indexed={len(recordings)}')


if __name__=='__main__':
    main()
