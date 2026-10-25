# SatyaLens — Segment 4: Evidence Upload & Consent

## Supported Formats

The following MIME types are accepted. Both the client-declared type **and** file
magic bytes are checked for types that have known signatures.

| Format | MIME type | Magic-byte check |
|--------|-----------|-----------------|
| JPEG | `image/jpeg` | ✅ `FF D8 FF` |
| PNG | `image/png` | ✅ `89 PNG` |
| GIF | `image/gif` | ✅ `GIF` |
| WebP | `image/webp` | — |
| PDF | `application/pdf` | ✅ `%PDF` |
| MP3 | `audio/mpeg` | — |
| WAV | `audio/wav` | — |
| OGG | `audio/ogg` | — |
| M4A (audio) | `audio/mp4` | — |
| WebM audio | `audio/webm` | — |
| MP4 video | `video/mp4` | — |
| WebM video | `video/webm` | — |
| Plain text | `text/plain` | — |

To change the allowed set, edit `allowed_mime_types` in `.env` or `config.py`.

## Size Limit

Default maximum: **10 MiB** (`max_upload_bytes = 10 * 1024 * 1024`).

Override via environment variable:
```
MAX_UPLOAD_BYTES=5242880   # 5 MiB
```

## Storage Location

Evidence files are stored in the directory specified by `upload_dir` (default: `backend/uploads/`).

- This directory is **outside the public frontend** directory.
- Files are **never served** by the API. Absolute paths are never logged or returned.
- Each file is stored under a server-generated UUID filename: `<uuid>.<ext>`.

Override via environment variable:
```
UPLOAD_DIR=/data/satyalens/uploads
```

## Consent Workflow

1. **Create case** — `POST /api/cases` → `consent_status: "pending"`
2. **User reads consent panel** — shown in the UI; explains what is collected, why, and retention
3. **User ticks checkbox and submits** — `POST /api/cases/{case_id}/evidence/consent`
   - Records `consent_status: "granted"` and `consent_at` timestamp on the case
   - Idempotent: calling twice while already granted returns 200 without error
4. **Upload evidence** — `POST /api/cases/{case_id}/evidence` (multipart)
   - Blocked with 403 if consent is still pending
5. **Delete evidence** — `DELETE /api/cases/{case_id}/evidence/{evidence_id}`
   - Removes DB metadata and stored file; 204 on success

## SHA-256 Integrity Hash

- Computed **server-side** from the exact bytes received.
- Stored in the `sha256_hash` column (64-character hex string).
- Displayed in the UI with a clear disclaimer:

> The SHA-256 hash records which bytes were received. It can detect changes after
> upload. It does **not** prove the file is genuine, unaltered before upload, or
> that it constitutes authentic evidence.

## Retention and Deletion

| Action | What is removed |
|--------|----------------|
| `DELETE /api/cases/{case_id}` | Case row, all evidence rows (cascade), all stored files |
| `DELETE /api/cases/{case_id}/evidence/{id}` | One evidence row + its stored file |
| File already gone on disk | DB row still removed cleanly |

No automated retention expiry is implemented in the prototype. Cases remain until
explicitly deleted.

## Security Controls

| Control | Implementation |
|---------|---------------|
| MIME allow-list | `ALLOWED_MIME_TYPES` frozenset in `routes/evidence.py` |
| Magic-byte validation | `MAGIC_SIGNATURES` dict checked against first N bytes |
| Size limit | Streamed in 64 KiB chunks; 413 raised as soon as limit exceeded |
| Server-side filename | `<uuid>.<safe-ext>` — never uses the original filename on disk |
| Path traversal guard | `dest_path.resolve().relative_to(storage_dir)` check |
| No file execution | Files are stored as opaque bytes; server never executes them |
| No credential collection | UI explicitly warns against uploading OTPs, PINs, passwords |
| Atomicity on failure | File removed from disk if DB write fails after storage |

## Privacy Limitations

- This is a **prototype**. Data is stored locally with no encryption at rest.
- No access control beyond case ownership is implemented.
- Do not use in production without adding authentication, authorisation, and encryption.
- Do not upload real OTPs, PINs, banking passwords, or account credentials.
