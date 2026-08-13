#!/usr/bin/env python3
import argparse, csv, json, re, unicodedata
from pathlib import Path

TONE_MARKS = {
    'ā':1,'á':2,'ǎ':3,'à':4,'ē':1,'é':2,'ě':3,'è':4,
    'ī':1,'í':2,'ǐ':3,'ì':4,'ō':1,'ó':2,'ǒ':3,'ò':4,
    'ū':1,'ú':2,'ǔ':3,'ù':4,'ǖ':1,'ǘ':2,'ǚ':3,'ǜ':4,
    'Ā':1,'Á':2,'Ǎ':3,'À':4,'Ē':1,'É':2,'Ě':3,'È':4,
    'Ī':1,'Í':2,'Ǐ':3,'Ì':4,'Ō':1,'Ó':2,'Ǒ':3,'Ò':4,
    'Ū':1,'Ú':2,'Ǔ':3,'Ù':4,'Ǖ':1,'Ǘ':2,'Ǚ':3,'Ǜ':4,
}
CJK = re.compile(r'[\u3400-\u9fff]')


def cedict_tones(cedict):
    if not cedict:
        return []
    m = re.search(r'\[([^\]]+)\]', cedict)
    if not m:
        return []
    out=[]
    for tok in m.group(1).split():
        mm=re.search(r'([1-5])$', tok)
        if mm:
            t=int(mm.group(1))
            out.append(0 if t==5 else t)
    return out


def accent_tones(pinyin):
    # Fallback parser. It is intentionally conservative; CEDICT is preferred.
    if not pinyin:
        return []
    # Every tone mark terminates a non-neutral syllable. Neutral syllables are
    # difficult to segment from concatenated pinyin, so spaces are handled but
    # unsplit neutral suffixes are left for manual review.
    out=[]
    for chunk in re.split(r'[\s·\-]+', pinyin.strip()):
        if not chunk:
            continue
        marks=[TONE_MARKS[ch] for ch in chunk if ch in TONE_MARKS]
        out.extend(marks)
        if not marks and re.fullmatch(r'[A-Za-züÜvV]+', chunk):
            out.append(0)
    return out


def cjk_chars(text):
    return [c for c in text if CJK.fullmatch(c)]


def sandhi_surface(word, tones):
    """Return a useful word-level expected surface pattern + uncertainty tags.

    Recording-level labels remain authoritative. Longer T3 strings depend on
    prosodic grouping, so these are flagged as ambiguous rather than discarded.
    """
    surf=list(tones)
    tags=[]
    chars=cjk_chars(word)
    aligned = len(chars)==len(surf)

    # Neutral lexical tones are already surface-relevant.
    if 0 in surf:
        tags.append('neutral_tone')

    # A-not-A reductions: X不X and X一X commonly reduce the middle syllable.
    if aligned:
        for i,ch in enumerate(chars):
            if ch=='不' and 0<i<len(chars)-1 and chars[i-1]==chars[i+1]:
                surf[i]=0; tags.append('bu_a_not_a')
            if ch=='一' and 0<i<len(chars)-1 and chars[i-1]==chars[i+1]:
                surf[i]=0; tags.append('yi_reduplication')

    # 不: 4 -> 2 before 4 (unless already neutralized above).
    if aligned:
        for i,ch in enumerate(chars[:-1]):
            if ch=='不' and surf[i]!=0 and tones[i]==4 and tones[i+1]==4:
                surf[i]=2; tags.append('bu_before_t4')

    # 一: remains T1 in ordinals/isolated/final; otherwise T2 before T4 and T4
    # before T1/T2/T3. Reduplicative 一 was handled above.
    if aligned:
        for i,ch in enumerate(chars):
            if ch!='一' or surf[i]==0 or tones[i]!=1:
                continue
            if i==len(chars)-1 or (i>0 and chars[i-1]=='第'):
                continue
            nxt=tones[i+1] if i+1<len(tones) else None
            if nxt==4:
                surf[i]=2; tags.append('yi_before_t4')
            elif nxt in (1,2,3):
                surf[i]=4; tags.append('yi_before_non_t4')

    # Third-tone sandhi. For a contiguous run, all but the final T3 may surface
    # as rising if the run forms one prosodic domain. Longer runs are flagged.
    i=0
    while i<len(surf):
        if tones[i]!=3:
            i+=1; continue
        j=i
        while j<len(tones) and tones[j]==3:
            j+=1
        run=j-i
        if run>=2:
            tags.append('third_tone_sandhi')
            if run>=3:
                tags.append('third_tone_grouping_ambiguous')
            for k in range(i,j-1):
                surf[k]=2
        i=j

    return surf, sorted(set(tags)), (aligned or not any(t in tags for t in ['bu_before_t4','yi_before_t4','yi_before_non_t4']))


def pattern(ts):
    return '-'.join('N' if t==0 else str(t) for t in ts) if ts else ''


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('csv_path')
    ap.add_argument('--out-json', required=True)
    ap.add_argument('--out-csv', required=True)
    args=ap.parse_args()
    rows=[]
    with open(args.csv_path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            word=(r.get('Simplified') or '').split('|')[0].strip()
            trad=(r.get('Traditional') or '').split('|')[0].strip()
            pinyin=(r.get('Pinyin') or '').split('|')[0].strip()
            tones=cedict_tones(r.get('CEDICT') or '') or accent_tones(pinyin)
            surf,tags,aligned=sandhi_surface(word,tones)
            rows.append({
                'id':r.get('ID',''), 'word':word, 'traditional':trad,
                'pinyin':pinyin, 'pos':r.get('POS',''), 'level':r.get('Level',''),
                'lexical_tones':tones, 'lexical_pattern':pattern(tones),
                'default_surface_tones':surf, 'default_surface_pattern':pattern(surf),
                'sandhi_tags':tags, 'surface_label_needs_clip_review':('third_tone_grouping_ambiguous' in tags or not aligned),
                'forvo_url':'https://forvo.com/word/'+word+'/#zh',
            })
    Path(args.out_json).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['id','word','traditional','pinyin','pos','level','lexical_pattern','default_surface_pattern','sandhi_tags','surface_label_needs_clip_review','forvo_url']
    with open(args.out_csv,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            rr={k:r[k] for k in fields}; rr['sandhi_tags']=';'.join(rr['sandhi_tags']); w.writerow(rr)
    print(f'Wrote {len(rows)} HSK entries')
    print('Sandhi-sensitive:', sum(bool(r['sandhi_tags']) for r in rows))
    print('Needs recording-level surface review:', sum(r['surface_label_needs_clip_review'] for r in rows))

if __name__=='__main__': main()
