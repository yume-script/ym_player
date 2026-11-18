#!/usr/bin/env python3
"""텔레그램 채널 비교 (목록 카드에 포스터가 왜 안 붙는지 보기) - 읽기만 하고 아무것도 바꾸지 않는다.

  cd /opt/ym_player
  venv/bin/python tools/tg_channel_report.py                       # 기본: "contentSave"와 "최신 영화" 채널
  venv/bin/python tools/tg_channel_report.py contentSave "최신 영화(plex)" -n 60
  venv/bin/python tools/tg_channel_report.py contentSave --missing   # 포스터 없는 것만
  venv/bin/python tools/tg_channel_report.py contentSave --csv /tmp/report.csv
  (데이터 폴더가 기본 위치가 아니면 --data /경로/data 또는 MP_DATA_DIR)

채널은 설정 > 소스 > 텔레그램에서 고른 채널 이름의 일부로 찾는다 (대소문자 무시). 채널마다:
  - 요약: 영상 수, 카드 그림이 어디서 오나 (Plex 포스터 / TMDB 포스터 / 채널에 올린 그림 / 영상 썸네일뿐)
    TMDB 상태 (찾음+포스터 / 찾음인데 포스터 없음 / 못 찾음 / 아직 안 찾아 봄 / 직접 고름), 그 소스의 TMDB 키 여부
  - 최근 영상 N개: 제목·파일 이름, 카드 그림, TMDB로 찾을 이름·연도(mkey)와 상태
그리고 두 채널에 같은 작품(같은 mkey)이 있으면 한쪽만 포스터가 있는 것을 보여 준다.
"""
import argparse
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    p = argparse.ArgumentParser(description="텔레그램 채널 비교 (포스터 진단, 읽기만)")
    p.add_argument("channels", nargs="*", default=["contentSave", "최신 영화"], help="채널 이름(일부). 기본: contentSave, 최신 영화")
    p.add_argument("-n", "--limit", type=int, default=40, help="채널마다 보여 줄 최근 영상 수 (기본 40, 0이면 전부)")
    p.add_argument("--missing", action="store_true", help="포스터가 없는 영상만")
    p.add_argument("--csv", help="결과를 CSV로도 저장")
    p.add_argument("--data", help="YMPlayer 데이터 폴더 (기본: MP_DATA_DIR 또는 ym_player/data)")
    return p.parse_args()


def when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def cut(s, n):
    s = str(s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def main():
    args = parse_args()
    if args.data:
        os.environ["MP_DATA_DIR"] = os.path.abspath(args.data)
    sys.path.insert(0, ROOT)
    from ym_player import config_store, telegram_src as t

    cfg = config_store.load()
    print(f"데이터 폴더: {config_store.DATA_DIR}")
    chans = []   # (소스, 채널 id, 채널 이름)
    for s in cfg.get("sources", []):
        if s.get("type") != "telegram":
            continue
        for c in s.get("channels") or []:
            chans.append((s, int(c["id"]), str(c.get("title") or c["id"])))
    if not chans:
        sys.exit("설정에 텔레그램 채널이 없습니다.")
    picked = []
    for want in args.channels:
        hits = [x for x in chans if want.casefold() in x[2].casefold() or want == str(x[1])]
        if not hits:
            print(f"\n'{want}'에 맞는 채널이 없습니다. 고를 수 있는 채널:")
            for s, cid, name in chans:
                print(f"  - {name}  (소스 {s.get('name') or s['id']}, id {cid})")
            continue
        picked += [h for h in hits if h not in picked]
    if not picked:
        sys.exit(1)

    rows_out, by_mkey = [], {}
    for src, cid, name in picked:
        sid = src["id"]
        key_here = bool(str(src.get("tmdb_key") or "").strip())
        key_any = key_here or any(str(s.get("tmdb_key") or "").strip() for s in cfg.get("sources", []) if s.get("type") == "telegram")
        with t.db() as conn:
            vids = conn.execute("SELECT * FROM videos WHERE source=? AND channel=? AND kind='video' ORDER BY msg DESC",
                                (sid, cid)).fetchall()
            keys = [(cid, r["msg"]) for r in vids]
            pics = t._plex_posters(conn, sid, keys) if keys else {}
            plex_rows = {(r["channel"], r["msg"]) for r in conn.execute(
                "SELECT channel, msg FROM plex_meta WHERE source=? AND channel=?", (sid, cid))}
            chan_pics = {(p["channel"], p["video"]) for p in conn.execute(
                "SELECT channel, video FROM posters WHERE source=? AND channel=?", (sid, cid))}
            meta = {r["mkey"]: r for r in conn.execute("SELECT mkey, tmdb_id, data, fetched, manual FROM meta WHERE source=?", (sid,))}
        print("\n" + "=" * 100)
        print(f"채널: {name}  (소스 {src.get('name') or sid}, id {cid})")
        print(f"TMDB 키: {'이 소스에 있음' if key_here else ('다른 텔레그램 소스에 있음' if key_any else '없음 - 포스터를 찾을 수 없음')}")
        stats = {"Plex 포스터": 0, "TMDB 포스터": 0, "채널 그림": 0, "썸네일뿐": 0}
        tstats = {"찾음+포스터": 0, "찾음·포스터 없음": 0, "못 찾음": 0, "안 찾아 봄": 0, "직접 고름": 0, "Plex 정보 사본(TMDB 안 씀)": 0, "찾을 이름 없음": 0}
        lines = []
        for r in vids:
            k = (cid, r["msg"])
            target = t.meta_target(sid, cid, msg=r["msg"])
            mkey = target["mkey"] if target else ""
            m = meta.get(mkey) if mkey else None
            data = json.loads(m["data"]) if m and m["data"] else None
            if k in plex_rows:
                tstate = "Plex 정보 사본(TMDB 안 씀)"
            elif not target:
                tstate = "찾을 이름 없음"
            elif m and m["manual"]:
                tstate = "직접 고름"
            elif data and data.get("poster"):
                tstate = "찾음+포스터"
            elif data:
                tstate = "찾음·포스터 없음"
            elif m:
                tstate = "못 찾음"
            else:
                tstate = "안 찾아 봄"
            tstats[tstate] += 1
            if k in pics:
                card = "Plex 포스터" if k in plex_rows else "TMDB 포스터"
            elif k in chan_pics:
                card = "채널 그림"
            else:
                card = "썸네일뿐"
            stats[card] += 1
            row = {"channel": name, "msg": r["msg"], "date": (r["date"] or "")[:10], "title": r["title"], "file_name": r["file_name"],
                   "card": card, "tmdb": tstate, "mkey": mkey, "search": " / ".join(x for x in (target or {}).get("titles") or [] if x),
                   "year": (target or {}).get("year") or "", "tmdb_id": (m["tmdb_id"] if m else "") or "",
                   "tmdb_title": (data or {}).get("title") or "", "tmdb_year": (data or {}).get("year") or "",
                   "fetched": when(m["fetched"]) if m else ""}
            rows_out.append(row)
            if mkey:
                by_mkey.setdefault(mkey, []).append(row)
            if not args.missing or card in ("썸네일뿐", "채널 그림"):
                lines.append(row)
        print(f"영상 {len(vids)}개  ·  카드 그림: " + " / ".join(f"{a} {b}" for a, b in stats.items()))
        print("TMDB: " + " / ".join(f"{a} {b}" for a, b in tstats.items() if b))
        show = lines if not args.limit else lines[:args.limit]
        print(f"\n최근 {len(show)}개{' (포스터 없는 것만)' if args.missing else ''}:")
        print(f"{'msg':>7} {'날짜':10} {'카드':8} {'TMDB 상태':16} {'찾을 이름 (연도)':34} {'찾은 작품':26} 제목 · 파일")
        for x in show:
            found = f"#{x['tmdb_id']} {x['tmdb_title']} ({x['tmdb_year']})" if x["tmdb_id"] else (f"없음 {x['fetched']}" if x["fetched"] else "")
            print(f"{x['msg']:>7} {x['date']:10} {x['card']:8} {x['tmdb']:16} {cut(x['search'] + (' (' + x['year'] + ')' if x['year'] else ''), 34):34} "
                  f"{cut(found, 26):26} {cut(x['title'], 40)} · {cut(x['file_name'], 60)}")

    if len(picked) > 1:
        print("\n" + "=" * 100)
        print("두 채널에 같은 작품(같은 찾을 이름)이 있는데 한쪽만 포스터가 있는 것:")
        n = 0
        for mkey, rs in by_mkey.items():
            names = {x["channel"] for x in rs}
            if len(names) < 2:
                continue
            has = {x["channel"] for x in rs if x["card"] in ("Plex 포스터", "TMDB 포스터")}
            if has and has != names:
                n += 1
                for x in rs:
                    print(f"  [{x['channel']}] {x['card']:8} {x['tmdb']:16} {cut(x['title'], 40)} · {cut(x['file_name'], 60)}")
                print(f"     (찾을 이름 {mkey})")
        if not n:
            print("  없음")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else ["channel"])
            w.writeheader()
            w.writerows(rows_out)
        print(f"\nCSV: {args.csv} ({len(rows_out)}줄)")


if __name__ == "__main__":
    main()
