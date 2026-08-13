#!/usr/bin/env python3
"""Download a small OpenAI Mandarin TTS sample into the local library.

Requires OPENAI_API_KEY. Outputs are synthetic and intentionally kept separate
from human Mandarin recordings.
"""
import argparse, json, os, urllib.request
from pathlib import Path

WORDS = {
    '公司': 'gōngsī',
    '你好': 'nǐ hǎo',
    '谢谢': 'xièxie',
}
VOICES = ('coral', 'marin', 'cedar')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='imports/openai_tts')
    ap.add_argument('--model',default='gpt-4o-mini-tts')
    ap.add_argument('--words',default=','.join(WORDS))
    ap.add_argument('--voices',default=','.join(VOICES))
    ap.add_argument('--language-instruction',default='Speak this Mandarin Chinese word naturally and clearly. Do not translate it. Say only the Chinese word.')
    args=ap.parse_args()
    key=os.getenv('OPENAI_API_KEY')
    if not key: raise SystemExit('Set OPENAI_API_KEY before running this downloader.')
    words=[w for w in args.words.split(',') if w in WORDS]
    voices=[v for v in args.voices.split(',') if v]
    if not words: raise SystemExit('No recognized sample words selected.')
    for word in words:
        for voice in voices:
            out=Path(args.output)/word/f'{voice}.mp3'; out.parent.mkdir(parents=True,exist_ok=True)
            if out.exists() and out.stat().st_size: print('existing',out); continue
            payload=json.dumps({'model':args.model,'voice':voice,'input':word,'instructions':args.language_instruction,'response_format':'mp3'}).encode()
            req=urllib.request.Request('https://api.openai.com/v1/audio/speech',data=payload,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json','User-Agent':'MandarinToneTrainer/0.1'})
            with urllib.request.urlopen(req,timeout=120) as response: data=response.read()
            out.write_bytes(data)
            out.with_suffix('.json').write_text(json.dumps({'speaker':f'OpenAI {voice}','source':'openai_tts','source_recording_id':f'{args.model}/{voice}/{word}','language_code':'zh','language_status':'synthetic_mandarin','license':'OpenAI API output','recording_type':'synthetic','voice':voice,'model':args.model,'word':word,'pinyin':WORDS[word]},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            print('downloaded',out,len(data))

if __name__=='__main__': main()
