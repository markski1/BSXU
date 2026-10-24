# BSXU — B2 ShareX uploader

BSXU is a Flask application for uploading files from ShareX, Flameshot or a private web panel. Files are stored locally, with an optional private Backblaze B2 backend and a bounded local cache.

The panel provides search, sorting, pagination, image/video previews, upload progress, multiple files, copyable links, link expiry/revocation, upload-only device keys and persistent request statistics. Supported images are processed in a limited subprocess to remove metadata and preserve orientation and animation. Videos play inline; active documents are downloaded as attachments.

Use Python 3.11 or newer. All server dependency files and deployment material are in src.

Read the [deployment and migration instructions](src/DEPLOYMENT.md) before upgrading. A separate ADMIN_KEY and upload authentication headers are now required. Existing state databases are migrated in place; preserve their location and back them up.

From src:

    sfw uv sync --locked --no-dev
    .venv/bin/python -m unittest discover -s tests -v

On Windows, use .venv\Scripts\python.exe for the test command.

Dependencies are locked, with hashed requirements exports. The normal release-age restriction is five days; four security fixes have explicit 24-hour exceptions. Server installations exclude the Flameshot-only clipboard dependency.

Contributions and issue reports are welcome.
