#!/usr/bin/env python3
"""Seed autoplay video pools via PiCast API.

Run after deploying v0.18.0 with pool_mode enabled.
Usage: python3 seed_autoplay_pools.py [HOST]
Default HOST: http://10.0.0.25:5050
"""

import json
import sys
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "http://10.0.0.25:5050"

# Video pools: block_name -> [(url, title, tags)]
POOLS = {
    "morning-foundation": [
        ("https://www.youtube.com/watch?v=hlWiI4xVXKY", "Sunny Mornings - Piano Guitar & Birds", "morning,piano,birds,peaceful"),
        ("https://www.youtube.com/watch?v=sUDuLqEJul8", "Morning Serenade - Tim Janis", "morning,instrumental,peaceful"),
        ("https://www.youtube.com/watch?v=aCFd0SeFBMQ", "Morning Light - Tim Janis", "morning,instrumental,nature"),
        ("https://www.youtube.com/watch?v=wuLKvcn-c7A", "Morning Positive Feelings & Energy", "morning,positive,relaxing"),
    ],
    "creation-stack": [
        ("https://www.youtube.com/watch?v=tlYCsHH282w", "Smooth LoFi for Deep Focus & Productivity", "lofi,focus,creative"),
        ("https://www.youtube.com/watch?v=d6GuMkHqnFQ", "Lofi for Reading and Writing - Deep Focus", "lofi,writing,focus"),
        ("https://www.youtube.com/watch?v=RuiASRXt7NY", "Writing Dreams Together - Chill Lofi", "lofi,writing,chill"),
        ("https://www.youtube.com/watch?v=1qKWinnp500", "LoFi for a Clear Mind - Ambient Beats", "lofi,ambient,focus"),
    ],
    "power-hour": [
        ("https://www.youtube.com/watch?v=jHAh4jTY1V4", "High-Energy Focus Music - Power Through Work", "energy,focus,power"),
        ("https://www.youtube.com/watch?v=ubnDMTUui1Y", "Good Vibes Only - Upbeat Instrumental", "upbeat,energy,vibes"),
        ("https://www.youtube.com/watch?v=sjkrrmBnpGE", "4 Hours Music for Studying Concentration", "study,concentration,long"),
        ("https://www.youtube.com/watch?v=wFJQmPlkv_U", "High-Energy Focus - Power Through Work #2", "energy,focus,instrumental"),
    ],
    "development": [
        ("https://www.youtube.com/watch?v=xAR6N9N8e6U", "Deep Focus Mix for Programming Coding", "coding,focus,programming"),
        ("https://www.youtube.com/watch?v=XNY5JJzLo08", "Deep Coding Music & Ambient Soundscape", "coding,ambient,deep"),
        ("https://www.youtube.com/watch?v=k55u2Rq8pMk", "Coding Music for Deep Focus - Hacker", "coding,focus,hacker"),
        ("https://www.youtube.com/watch?v=ZDBQwmjMGKg", "Deep Focus Cinematic Ambient for Coding", "coding,cinematic,ambient"),
    ],
    "clean-mama": [
        ("https://www.youtube.com/watch?v=8pBB-s9nbB0", "Cleaning Day Vintage Playlist - Old Time Radio", "oldies,cleaning,vintage"),
        ("https://www.youtube.com/watch?v=bx8NUxTky9U", "Vintage Music Playlist - Spring Cleaning", "vintage,cleaning,spring"),
        ("https://www.youtube.com/watch?v=Rd8v2m2h5WI", "Best 60s & 70s Songs - Golden Oldies", "oldies,60s,70s"),
        ("https://www.youtube.com/watch?v=Lfh8I3ySGsA", "Super Oldies Of The 50s - Original Mix", "oldies,50s,classics"),
        ("https://www.youtube.com/watch?v=LbYhsxOeO4w", "Saturday Mornin Cleanin - Old School Funk R&B", "funk,rnb,cleaning,soul"),
    ],
    "execution": [
        ("https://www.youtube.com/watch?v=n4YghVcjbpw", "SUPER FOCUS - Binaural Beats 40Hz", "binaural,focus,40hz"),
        ("https://www.youtube.com/watch?v=Z8ANihFXlgU", "40Hz GAMMA Binaural Beats - Ambient Study", "binaural,gamma,study"),
        ("https://www.youtube.com/watch?v=U0eLmyJkQBc", "Focus Music Binaural Beats Concentration", "binaural,concentration,focus"),
        ("https://www.youtube.com/watch?v=g1LNTAdIi8k", "Deep Focus Music - Binaural Beats Study", "binaural,deep-focus,study"),
    ],
    "pm-reflection": [
        ("https://www.youtube.com/watch?v=WLWJy1eXX2c", "8 Hours Classical Piano - Chopin Debussy Mozart", "classical,piano,chopin"),
        ("https://www.youtube.com/watch?v=y6TZHLAzg5o", "Classical Piano & Fireplace 24/7 - Mozart Chopin", "classical,piano,fireplace"),
        ("https://www.youtube.com/watch?v=EhO_MrRfftU", "4 Hours Peaceful Relaxing Piano Music", "piano,peaceful,relaxing"),
        ("https://www.youtube.com/watch?v=cGYyOY4XaFs", "Most Beautiful Classical Piano for Relax & Study", "classical,piano,beautiful"),
    ],
    "night-lab": [
        ("https://www.youtube.com/watch?v=iWu609TPXAc", "Chill Electronic Ambient Music for Studying", "electronic,ambient,chill"),
        ("https://www.youtube.com/watch?v=iXhy8sG91ok", "Chill Work Music - 12 Hours Focus & Inspiration", "electronic,chill,focus"),
        ("https://www.youtube.com/watch?v=KuDWifo1q1U", "Lounge Chillout Music - Wonderful Chill Out", "electronic,lounge,chillout"),
        ("https://www.youtube.com/watch?v=F2HSr-O2GcQ", "Atmospheric Ambient Electronic Music 2 Hours", "electronic,atmospheric,ambient"),
    ],
    "family-time": [
        ("https://www.youtube.com/watch?v=FxAgAyZYXJ8", "4K Deep Forest - 8 Hours NO LOOP Birdsong", "nature,forest,birds,4k"),
        ("https://www.youtube.com/watch?v=Qm846KdZN_c", "Forest Birdsong - Relaxing Nature Sounds", "nature,forest,birdsong"),
        ("https://www.youtube.com/watch?v=xNN7iTA57jM", "Forest Sounds - Woodland Ambience Bird Song", "nature,forest,woodland"),
        ("https://www.youtube.com/watch?v=1GzKYoyrlkA", "Forest River Sounds - Beautiful Birdsong", "nature,forest,river,birds"),
    ],
    "night-restoration": [
        ("https://www.youtube.com/watch?v=rulvcTfez5w", "10 Hours Deep Sleep Music & Black Screen", "sleep,black-screen,deep"),
        ("https://www.youtube.com/watch?v=Lq6IPc0yokc", "8 Hours Sleep Music - Delta Waves White Noise", "sleep,delta,white-noise"),
        ("https://www.youtube.com/watch?v=eqmHAsooPJo", "8 Hours Black Screen Sleep Music - Insomnia", "sleep,black-screen,insomnia"),
        ("https://www.youtube.com/watch?v=3p7Em4GgExA", "Nervous System Reset - Tibetan Singing Bowls", "sleep,tibetan,singing-bowls"),
    ],
}


def add_video(block_name, url, title, tags):
    data = json.dumps({"url": url, "title": title, "tags": tags}).encode()
    req = urllib.request.Request(
        f"{HOST}/api/autoplay/pool/{block_name}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return True
    except Exception as e:
        if "409" in str(e):
            return False  # Already exists
        print(f"  ERROR: {e}")
        return False


def main():
    print(f"Seeding autoplay pools on {HOST}...")
    total = 0
    skipped = 0
    for block_name, videos in POOLS.items():
        added = 0
        for url, title, tags in videos:
            ok = add_video(block_name, url, title, tags)
            if ok:
                added += 1
                total += 1
            else:
                skipped += 1
        print(f"  {block_name}: {added} added ({len(videos) - added} skipped)")
    print(f"\nDone: {total} added, {skipped} skipped (already in pool)")


if __name__ == "__main__":
    main()
