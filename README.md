# BSXU - B2 Sharex Uploader

BSXU is a continuation of [PSXU](https://github.com/markski1/PSXU), my previous ShareX upload thing.

Key differences:

- Implemented as a Flask application.
- Files are uploaded to a Backblaze B2 bucket (optional).
- Removes EXIF from images.
- Server filesystem storage otherwise. In B2 mode, the filesystem is used as a cache to avoid B2 egress fees.
- Web panel for manual uploading, stats, fetching links.
- A lot more configurable.
- In addition to ShareX, a method to use Flameshot is also provided.

### Setup

You can follow [this guide](https://markski.ar/blog/bsxu-b2-hosting) in my blog.

### Contributing

Find issue, don't want to make change, open issue.

Find issue, want to make change, open PR.
