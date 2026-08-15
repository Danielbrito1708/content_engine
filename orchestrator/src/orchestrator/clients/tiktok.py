from datetime import datetime

import httpx

from src.core import settings
from src.orchestrator.clients.http import request


class BufferQueueFull(Exception):
    """O poster não tem como agendar agora, e isso passa sozinho.

    Deliberately not a failure. Nothing is wrong with the run — its videos are
    rendered and waiting — so the worker leaves it in ``scheduling`` and retries
    later instead of burning an hour of LLM, speech and render work because the
    queue happened to be full at that minute.

    **Cobre duas esperas diferentes**, e é o `error` que as separa:
    `buffer_queue_full` (a fila do canal está no teto — abre publicando) e
    `buffer_rate_limited` (a cota da API do Buffer estourou — abre na virada da
    janela). O desfecho aqui é o mesmo, mas mandam procurar em lugares opostos.
    """

    def __init__(
        self,
        pending_count: int | None = None,
        rejected_by_buffer: str | None = None,
        error: str = "buffer_queue_full",
        retry_after: int | None = None,
    ):
        self.pending_count = pending_count
        #: Mensagem da recusa, quando o teto só apareceu ao tentar criar o post
        #: em vez de na contagem do poster. `None` no caso comum. Separa "a fila
        #: já estava cheia" de "o Buffer disse que estava" — dois caminhos com o
        #: mesmo desfecho e diagnósticos diferentes.
        self.rejected_by_buffer = rejected_by_buffer
        self.error = error
        #: Segundos até a cota reabrir. Só no `buffer_rate_limited`, e pode ser
        #: horas: o teto diário do plano é 250 chamadas.
        self.retry_after = retry_after
        super().__init__(f"{error} (pending={pending_count}, retry_after={retry_after})")

    @property
    def is_rate_limit(self) -> bool:
        return self.error == "buffer_rate_limited"


class TikTokClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tiktok_url

    async def schedule(
        self,
        video_key: str,
        classification: dict,
        part_number: int,
        series_id: str,
        total_parts: int = 1,
        follows_at: datetime | None = None,
        youtube_title: str | None = None,
    ) -> dict:
        """Agenda a parte nos destinos do poster (TikTok e, se houver, YouTube).

        Devolve ``{"scheduled_at", "buffer_update_id", "youtube_update_id",
        "youtube_error"}`` — os dois últimos podem vir nulos, porque o YouTube é
        destino secundário e sua ausência não é falha do run.

        ``follows_at`` é o horário já agendado da parte anterior. Mandado só em
        partes 2+: é o que faz a continuação sair um intervalo depois dela, em
        vez de cair no próximo horário livre do calendário do poster.

        ``youtube_title`` é o título do vídeo lá. Omitido do payload quando
        vazio (não vai `null`), como o `narrator_gender` no `tts_service`.
        """
        payload: dict = {
            "video_key": video_key,
            "classification": classification,
            "part_number": part_number,
            "series_id": series_id,
            "total_parts": total_parts,
        }
        if follows_at is not None:
            payload["follows_at"] = follows_at.isoformat()
        if youtube_title:
            payload["youtube_title"] = youtube_title

        try:
            resp = await request(
                "POST",
                f"{self._base}/schedule",
                timeout=30,
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise _queue_full_from(exc.response) from exc
            raise
        return resp.json()


def _queue_full_from(response: httpx.Response) -> BufferQueueFull:
    """Monta a exceção a partir do `detail` do poster; leitura best-effort.

    Um corpo malformado não pode mascarar o sinal: o que importa é o `429`, e
    todo o resto é detalhe de log.
    """
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 — a malformed body must not mask the queue-full signal
        detail = None
    if not isinstance(detail, dict):
        return BufferQueueFull()
    return BufferQueueFull(
        pending_count=detail.get("pending_count"),
        rejected_by_buffer=detail.get("rejected_by_buffer"),
        error=detail.get("error") or "buffer_queue_full",
        retry_after=detail.get("retry_after"),
    )
