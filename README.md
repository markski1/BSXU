# BSXU - B2 Sharex Uploader

BSXU is a continuation of [PSXU](https://github.com/markski1/PSXU), my previous ShareX upload thing.

Key differences:

- Implemented as a Flask application.
- Files are uploaded to a Backblaze B2 bucket.
- Server is only used for 'caching' (fresh files are stored and served from the server, instead of exposing B2 urls and risking egress fees).
- A lot more configurable.

### Setup

You can follow [this guide](https://markski.ar/blog/bsxu-b2-hosting) in my blog.

### Contributing

Find issue, don't want make change, open issue.

Find issue, want make change, open PR.

### TODO:

- Web interface with file viewer, management, manual uploading and downloading.
- Make use of 'CACHE_MAX_SIZE_MB'. (If the size of the cache folder exceeds the size, the oldest files should be truncated until it's size is 1/4 of CACHE_MAX_SIZE_MB)