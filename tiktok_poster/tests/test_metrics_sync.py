"""`POST /metrics/sync` — coleta de métricas do Buffer para publicações elegíveis.

Buffer é mockado (`BufferClient.get_post_metrics`), nunca chamado de verdade —
mesmo padrão de `test_schedule.py`. Publicações são semeadas direto no banco
pelo fixture `session`, sem passar por `/schedule`: o que importa aqui é a
elegibilidade (idade, cooldown), não como a linha chegou lá.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from src.tiktok_poster.db.models import PostMetric, Publication

NOW = datetime.now(tz=timezone.utc)


async def _make_publication(session, *, scheduled_at, channel="tiktok", buffer_post_id=None) -> Publication:
    pub = Publication(
        series_id=uuid.uuid4(),
        part_number=1,
        channel=channel,
        buffer_post_id=buffer_post_id or f"buf_{uuid.uuid4().hex[:12]}",
        scheduled_at=scheduled_at,
        hashtag_variant=f"{channel}:seed",
        timing_bucket="11h",
    )
    session.add(pub)
    await session.commit()
    await session.refresh(pub)
    return pub


async def _make_metric(session, *, publication_id, fetched_at, metric_type="views", value=1.0):
    metric = PostMetric(
        publication_id=publication_id,
        metric_type=metric_type,
        value=value,
        unit="count",
        fetched_at=fetched_at,
    )
    session.add(metric)
    await session.commit()
    return metric


METRICS_RESPONSE = {
    "metrics": [{"type": "views", "name": "Views", "value": 1234, "unit": "count"}],
    "metrics_updated_at": "2026-09-05T12:00:00Z",
}


async def test_publication_younger_than_24h_is_not_synced(client, session):
    await _make_publication(session, scheduled_at=NOW - timedelta(hours=1))

    with patch("src.tiktok_poster.api.routes.metrics.BufferClient") as mock_buf:
        get_metrics = AsyncMock(return_value=METRICS_RESPONSE)
        mock_buf.return_value.get_post_metrics = get_metrics
        resp = await client.post("/metrics/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["synced"] == 0
    assert data["metrics_written"] == 0
    get_metrics.assert_not_awaited()


async def test_publication_24h_old_and_never_synced_is_synced(client, session):
    pub = await _make_publication(session, scheduled_at=NOW - timedelta(hours=25))

    with patch("src.tiktok_poster.api.routes.metrics.BufferClient") as mock_buf:
        get_metrics = AsyncMock(return_value=METRICS_RESPONSE)
        mock_buf.return_value.get_post_metrics = get_metrics
        resp = await client.post("/metrics/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["synced"] == 1
    assert data["metrics_written"] == 1
    assert data["failed"] == 0
    get_metrics.assert_awaited_once_with(pub.buffer_post_id)

    rows = (
        await session.execute(select(PostMetric).where(PostMetric.publication_id == pub.id))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].metric_type == "views"
    assert rows[0].value == 1234


async def test_publication_already_synced_within_20h_is_skipped(client, session):
    pub = await _make_publication(session, scheduled_at=NOW - timedelta(hours=30))
    await _make_metric(session, publication_id=pub.id, fetched_at=NOW - timedelta(hours=5))

    with patch("src.tiktok_poster.api.routes.metrics.BufferClient") as mock_buf:
        get_metrics = AsyncMock(return_value=METRICS_RESPONSE)
        mock_buf.return_value.get_post_metrics = get_metrics
        resp = await client.post("/metrics/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["synced"] == 0
    assert data["skipped"] == 1
    get_metrics.assert_not_awaited()


async def test_buffer_error_on_one_publication_does_not_stop_the_batch(client, session):
    older = await _make_publication(session, scheduled_at=NOW - timedelta(hours=48))
    newer = await _make_publication(session, scheduled_at=NOW - timedelta(hours=25))

    async def side_effect(post_id):
        if post_id == older.buffer_post_id:
            raise ConnectionError("buffer unreachable")
        return METRICS_RESPONSE

    with patch("src.tiktok_poster.api.routes.metrics.BufferClient") as mock_buf:
        get_metrics = AsyncMock(side_effect=side_effect)
        mock_buf.return_value.get_post_metrics = get_metrics
        resp = await client.post("/metrics/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["failed"] == 1
    assert data["synced"] == 1
    assert data["metrics_written"] == 1

    newer_metrics = (
        await session.execute(select(PostMetric).where(PostMetric.publication_id == newer.id))
    ).scalars().all()
    assert len(newer_metrics) == 1
    older_metrics = (
        await session.execute(select(PostMetric).where(PostMetric.publication_id == older.id))
    ).scalars().all()
    assert older_metrics == []


async def test_get_post_metrics_returning_none_is_handled_without_error(client, session):
    await _make_publication(session, scheduled_at=NOW - timedelta(hours=25))

    with patch("src.tiktok_poster.api.routes.metrics.BufferClient") as mock_buf:
        get_metrics = AsyncMock(return_value=None)
        mock_buf.return_value.get_post_metrics = get_metrics
        resp = await client.post("/metrics/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["synced"] == 0
    assert data["metrics_written"] == 0
    assert data["failed"] == 0


async def test_sync_respects_the_limit_parameter(client, session):
    for _ in range(3):
        await _make_publication(session, scheduled_at=NOW - timedelta(hours=25))

    with patch("src.tiktok_poster.api.routes.metrics.BufferClient") as mock_buf:
        get_metrics = AsyncMock(return_value=METRICS_RESPONSE)
        mock_buf.return_value.get_post_metrics = get_metrics
        resp = await client.post("/metrics/sync?limit=2")

    assert resp.status_code == 200
    assert resp.json()["synced"] == 2
    assert get_metrics.await_count == 2
