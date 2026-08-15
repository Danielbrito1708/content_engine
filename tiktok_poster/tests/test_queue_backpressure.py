"""A recusa do Buffer que é teto de fila disfarçado.

A fila cheia que a contagem local enxerga já virava `429` — pausa, e o run
espera vaga. A que só aparece na recusa do `createPost` virava `500`, e um `500`
marca o run `failed` para sempre: a varredura de retry do orchestrador só olha
runs em `scheduling`. Dois runs terminaram assim em 15/08/2026, com o vídeo
renderizado e nenhum caminho de volta.

O que estes testes fixam é a fronteira: recusa que é teto vira pausa, recusa que
é erro de verdade continua sendo erro.
"""

import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock, patch

from src.tiktok_poster.buffer.client import BufferClient, BufferRateLimited, BufferRejected
from tests.conftest import BUFFER_CREATE_RESPONSE, SAMPLE_REQUEST

#: Fila abaixo do teto (10): a pré-checagem passa e o post chega a ser tentado.
ROOMY_QUEUE = [{"scheduled_at": 1700000000 + i * 3600} for i in range(3)]
FULL_QUEUE = [{"scheduled_at": 1700000000 + i * 3600} for i in range(10)]


def _mock_buffer(mock, *, pending, create):
    mock.return_value.get_pending_posts = AsyncMock(side_effect=pending)
    mock.return_value.create_post = AsyncMock(side_effect=create)
    return mock


async def test_rejection_that_names_a_limit_becomes_backpressure(client):
    """Mensagem de teto → `429`, mesmo com a contagem local folgada."""
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        _mock_buffer(
            mock_buf,
            pending=[ROOMY_QUEUE, ROOMY_QUEUE],
            create=BufferRejected("You have reached the limit of 10 posts for this channel"),
        )
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "buffer_queue_full"
    # A mensagem crua sobe junto: é o que distingue este caminho do outro no log
    # do orchestrador, e é a única pista se a heurística um dia errar.
    assert "limit of 10 posts" in detail["rejected_by_buffer"]


async def test_rejection_with_queue_at_limit_becomes_backpressure(client):
    """Sem marcador na mensagem, mas a fila **está** no teto → `429`.

    É o caso da contagem que não enxerga tudo: o poster filtra `scheduled` de um
    canal, e o teto do Buffer não é obrigado a contar do mesmo jeito. A segunda
    consulta, no caminho de erro, é o que revela isso.
    """
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        _mock_buffer(
            mock_buf,
            pending=[ROOMY_QUEUE, FULL_QUEUE],
            create=BufferRejected("something the API did not explain"),
        )
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    assert resp.json()["detail"]["pending_count"] == 10


async def test_real_rejection_still_fails(client):
    """Recusa que não é teto continua subindo — pausa infinita seria pior.

    Um run parado em `scheduling` é reoferecido a cada 15 minutos para sempre.
    Traduzir *toda* recusa em backpressure trocaria um erro visível por um loop
    silencioso, então o erro de verdade continua sendo erro.
    """
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        _mock_buffer(
            mock_buf,
            pending=[ROOMY_QUEUE, ROOMY_QUEUE],
            create=BufferRejected("video asset is corrupted"),
        )
        with pytest.raises(BufferRejected):
            await client.post("/schedule", json=SAMPLE_REQUEST)


async def test_failed_recount_does_not_mask_the_rejection(client):
    """Se a segunda consulta ao Buffer também falhar, vale a recusa original."""
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        _mock_buffer(
            mock_buf,
            pending=[ROOMY_QUEUE, ConnectionError("buffer unreachable")],
            create=BufferRejected("video asset is corrupted"),
        )
        with pytest.raises(BufferRejected):
            await client.post("/schedule", json=SAMPLE_REQUEST)


async def test_rate_limit_on_counting_becomes_backpressure(client):
    """Cota estourada na **primeira** chamada — nem chega a tentar publicar.

    O teto diário do plano é 250 chamadas, e um agendamento custa ~4. Quando ele
    estoura, o `429` da API subia como `HTTPStatusError` → 500 → run `failed`.
    """
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(
            side_effect=BufferRateLimited(30837, '"250-in-1day"; r=0')
        )
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "buffer_rate_limited"
    assert detail["retry_after"] == 30837


async def test_rate_limit_on_creating_becomes_backpressure(client):
    """Cota estourada **entre** a contagem e a criação: mesma espera."""
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        _mock_buffer(
            mock_buf,
            pending=[ROOMY_QUEUE],
            create=BufferRateLimited(90, '"100-in-15min"; r=0'),
        )
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "buffer_rate_limited"


def test_rate_limit_is_read_from_the_response_headers():
    """`retry-after` e os tetos saem dos headers da resposta, não de adivinhação."""
    from src.tiktok_poster.buffer.client import _rate_limit_detail, _retry_after

    resp = Response(429, headers=[
        ("retry-after", "30837"),
        ("ratelimit", '"100-in-15min"; r=87; t=477'),
        ("ratelimit", '"250-in-1day"; r=0; t=30837'),
    ])
    assert _retry_after(resp) == 30837
    detail = _rate_limit_detail(resp)
    assert "250-in-1day" in detail and "100-in-15min" in detail
    # Header ausente ou em formato de data não pode derrubar o caminho de erro.
    assert _retry_after(Response(429)) is None
    assert _retry_after(Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None


@respx.mock
async def test_client_turns_the_apis_429_into_the_rate_limit_type():
    """A costura que faltava: `429` da API vira tipo próprio, não `HTTPStatusError`.

    Era aqui que o `raise_for_status` transformava cota estourada em 500. Os
    testes acima mockam o cliente; este exercita o cliente de verdade contra uma
    resposta de verdade.
    """
    respx.post("https://api.buffer.com").mock(
        return_value=Response(
            429,
            headers=[("retry-after", "30837"), ("ratelimit", '"250-in-1day"; r=0; t=30837')],
            json={"errors": [{"message": "Too many requests from this client."}]},
        )
    )

    with pytest.raises(BufferRateLimited) as caught:
        await BufferClient(channel_id="ch_1").get_pending_posts()

    assert caught.value.retry_after == 30837
    assert "250-in-1day" in str(caught.value)


async def test_happy_path_queries_the_queue_once(client):
    """Sem recusa, nada muda: uma consulta de fila por agendamento."""
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        pending_mock = AsyncMock(return_value=ROOMY_QUEUE)
        mock_buf.return_value.get_pending_posts = pending_mock
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    assert pending_mock.await_count == 1
