# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/DeepAleph/jevreel/security/advisories/new), not in a
public issue. You should hear back within a week.

## What to know when running jevreel

- **Videos and URLs are untrusted input.** jevreel hands them to FFmpeg and, for URLs, to yt-dlp.
  Keep both up to date, and don't index files you wouldn't otherwise open.
- **Frames leave your machine.** Contact sheets, frames, crops and transcripts are sent to
  TypeSafe's API when you ask Jev about a video. Don't index footage you are not allowed to share
  with a third-party service.
- **Your API key** is read from `TYPESAFE_API_KEY`. jevreel never writes it to disk or logs it.
  Keep it out of `.mcp.json` files you commit, and out of shell history where you can.
- **Local data** (sheets, frames, crops, transcripts, downloaded videos) is stored under
  `JEVREEL_HOME`. Delete a video's folder there to remove everything jevreel kept about it.
