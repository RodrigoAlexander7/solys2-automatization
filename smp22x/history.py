"""Consultas por intervalo que unen la base activa y el archivo Parquet sin duplicar."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .archive import day_range, days_between, read_range
from .result import Err, Failure, Ok, Result, StorageFailure
from .storage import Reader


def iter_samples(reader: Reader, archive_dir: Path, start_ms: int, end_ms: int,
                 addresses: list[int] | None = None, page: int = 5000) -> Iterator[list[dict]]:
    """Páginas de muestras en orden temporal. Lanza StorageFailure si la base falla."""
    manifest = reader.manifest()
    if isinstance(manifest, Err):
        raise StorageFailure(manifest.error)
    for day in days_between(start_ms, end_ms):
        d_start, d_end = day_range(day)
        lo, hi = max(start_ms, d_start), min(end_ms, d_end)
        entry = manifest.value.get(day)
        archived: list[dict] = []
        if entry is not None and entry["pruned_ms"] is not None:
            archived = read_range(archive_dir, lo, hi, addresses, days=[day])
        seen = {r["id"] for r in archived}
        db_rows: list[dict] = []
        after = None
        while True:
            result = reader.range_samples(lo, hi, addresses, after, page)
            if isinstance(result, Err):
                raise StorageFailure(result.error)
            chunk = [r for r in result.value["items"] if r["id"] not in seen]
            if not archived:
                if chunk:
                    yield chunk
            else:
                db_rows.extend(chunk)
            after = result.value["next"]
            if after is None:
                break
        if archived:
            merged = sorted(archived + db_rows, key=lambda r: (r["t_ms"], r["id"]))
            for i in range(0, len(merged), page):
                yield merged[i:i + page]


def range_samples(reader: Reader, archive_dir: Path, start_ms: int, end_ms: int,
                  addresses: list[int] | None = None, limit: int = 50_000) -> Result[dict]:
    items: list[dict] = []
    try:
        for chunk in iter_samples(reader, archive_dir, start_ms, end_ms, addresses):
            items.extend(chunk)
            if len(items) > limit:
                return Ok({"items": items[:limit], "truncated": True})
    except StorageFailure as exc:
        return Err(exc.error)
    except OSError as exc:
        return Err(Failure("archive_failure", str(exc)))
    return Ok({"items": items, "truncated": False})
