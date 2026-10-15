"""Index a video, find a moment, and save full-resolution frames around it.

TYPESAFE_API_KEY=... uv run python examples/quickstart.py path/to/video.mp4 "a slide with a chart"
"""

import asyncio
import sys

from jevreel import Reel


async def main(video_path: str, query: str) -> None:
    async with Reel() as reel:
        video = await reel.index(video_path)
        print(f"indexed {video.title} ({video.duration}, {video.sheets} sheets, ${video.estimated_usd})")

        result = await reel.ask(video.id, query)
        print(f"{len(result.hits)} hits, {result.requests} requests, ${result.estimated_usd}")
        for hit in result.hits:
            print(f"  {hit.time}  p={hit.probability}  window {hit.window}")
        if not result.hits:
            print("  not found; weak candidates:", [h.time for h in result.weak])
            return

        best = result.hits[0]
        for frame in reel.zoom(video.id, best.at - 2, best.at + 2, count=3):
            print(f"  frame {frame.time}: {frame.path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
