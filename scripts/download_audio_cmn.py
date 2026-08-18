#!/usr/bin/env python3
"""Download the audio-cmn isolated-word Mandarin corpus.

audio-cmn publishes one MP3 per Hanzi spelling under:
  https://raw.githubusercontent.com/hugolpz/audio-cmn/master/96k/hsk/

The downloader is resumable: existing files are left untouched, and each
download gets a sidecar containing the provenance and Mandarin validation
metadata required by the trainer.  It downloads every matching corpus file;
there is intentionally no "best recording" selection step.
"""
import argparse
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/hugolpz/audio-cmn/{revision}/{quality}/hsk/"
REPO_URL = "https://github.com/hugolpz/audio-cmn"
TREE_URL = "https://api.github.com/repos/hugolpz/audio-cmn/git/trees/{revision}?recursive=1"
MP3_MAGIC = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def safe_word(value):
    return value.replace("/", "／").replace("\\", "＼")


def download_one(word, args):
    word_dir = Path(args.imports) / "audio_cmn" / safe_word(word)
    target = word_dir / f"cmn-{safe_word(word)}.mp3"
    sidecar = target.with_suffix(".json")
    if target.exists() and target.stat().st_size > 0:
        if not sidecar.exists():
            write_sidecar(sidecar, word, target.name, args)
        return word, "existing", target.stat().st_size

    url = BASE_URL.format(revision=args.revision, quality=args.quality)
    url += urllib.parse.quote(f"cmn-{word}.mp3", safe="")
    request = urllib.request.Request(url, headers={"User-Agent": "MandarinToneTrainer/0.1"})
    for attempt in range(args.retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                data = response.read()
            if not data.startswith(MP3_MAGIC):
                raise ValueError("response is not an MP3")
            word_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".mp3.part")
            temporary.write_bytes(data)
            temporary.replace(target)
            write_sidecar(sidecar, word, target.name, args)
            return word, "downloaded", len(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return word, "missing", 0
            if attempt == args.retries:
                return word, f"failed: {exc}", 0
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(60, 2 ** attempt + random.random())
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            if attempt == args.retries:
                return word, f"failed: {exc}", 0
            time.sleep(min(30, 2 ** attempt + random.random()))
    return word, "failed: exhausted retries", 0


def write_sidecar(path, word, filename, args):
    metadata = {
        "speaker": "audio-cmn",
        "source": "audio_cmn",
        "source_recording_id": f"audio-cmn/{args.revision}/{args.quality}/hsk/cmn-{word}.mp3",
        "language_code": "zh",
        "language_status": "verified_mandarin",
        "license": "CC-BY-SA",
        "recording_type": "isolated_word",
        "filename": filename,
        "source_url": f"{REPO_URL}/blob/{args.revision}/{args.quality}/hsk/cmn-{word}.mp3",
        "surface_pattern": None,
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hsk-json", default="data/hsk_words.json")
    parser.add_argument("--imports", default="imports")
    parser.add_argument("--quality", choices=["96k", "64k", "24k-abr", "18k-abr"], default="96k")
    parser.add_argument("--revision", default="master")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--all-source", action="store_true", help="enumerate and download every source HSK MP3, including words outside the local HSK list")
    args = parser.parse_args()

    if args.all_source:
        tree_url = TREE_URL.format(revision=urllib.parse.quote(args.revision, safe=""))
        request = urllib.request.Request(tree_url, headers={"User-Agent": "MandarinToneTrainer/0.1"})
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            tree = json.load(response)
        prefix = f"{args.quality}/hsk/cmn-"
        paths = [item["path"] for item in tree.get("tree", []) if item.get("type") == "blob" and item["path"].startswith(prefix) and item["path"].endswith(".mp3")]
        words = [path[len(prefix):-4] for path in paths]
        if tree.get("truncated"):
            raise SystemExit("GitHub source manifest was truncated; refusing an incomplete download")
    else:
        words = json.loads(Path(args.hsk_json).read_text(encoding="utf-8"))
        words = [item["word"] for item in words]
    if args.limit:
        words = words[:args.limit]

    counts = {"downloaded": 0, "existing": 0, "missing": 0, "failed": 0}
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_one, word, args) for word in words]
        for index, future in enumerate(as_completed(futures), 1):
            word, status, size = future.result()
            category = status.split(":", 1)[0]
            if category in counts:
                counts[category] += 1
            if category == "failed":
                failures.append((word, status))
            if index % 100 == 0 or index == len(futures):
                print(f"{index}/{len(futures)} downloaded={counts['downloaded']} existing={counts['existing']} missing={counts['missing']} failed={counts['failed']}", flush=True)

    if failures:
        print("Failures:")
        for word, status in sorted(failures):
            print(f"  {word}: {status}")
        raise SystemExit(1)
    print(f"Complete: downloaded={counts['downloaded']} existing={counts['existing']} missing={counts['missing']} failed={counts['failed']}")
    print(f"Audio is under {Path(args.imports) / 'audio_cmn'}")


if __name__ == "__main__":
    main()
