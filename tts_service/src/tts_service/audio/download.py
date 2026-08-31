"""Baixa o áudio de uma URL de vídeo para virar matéria-prima de roteiro.

Existe por causa de uma regra de produto, não de uma necessidade técnica: roteiro
de vídeo que já viralizou entra no pipeline como **input do refino**, nunca como
roteiro final (``docs/vision.md`` → "Roteiro viral entra como matéria-prima").
Para o refino reescrever, o texto precisa existir — e num TikTok ele só existe
como áudio narrado.

**O TikTok não é uma API pública e se defende como tal.** Medido em 27/08/2026,
os três obstáculos e o que cada um custa:

1. *Fingerprint de TLS.* Sem impersonation o TikTok responde ``200`` com uma
   casca de ~1,4 KB, sem o blob de dados — o extractor então falha com
   "Unexpected response from webpage request", que parece extractor quebrado e
   não é. Requer ``curl_cffi`` instalado; sem ele o yt-dlp aceita a opção e a
   ignora, voltando ao erro anterior. Daí a checagem explícita em
   :func:`_impersonate_target`.
2. *Desafio JS.* Resolvido pelo próprio yt-dlp, sem navegador. Não custa nada.
3. *Parser da página web falhando mesmo com 1 e 2.* Vira "Unable to extract
   universal data for rehydration" e afeta vídeos específicos, não a conta toda.
   O caminho da API mobile passa, então ele é a segunda tentativa — nunca a
   primeira, porque é o caminho menos estável dos dois.

Há também rate limit por IP: URLs em sequência rápida derrubam até a que
funcionou segundos antes. ``sleep_interval_requests`` é o freio.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from structlog import get_logger

log = get_logger(__name__)


class DownloadError(RuntimeError):
    """O áudio não pôde ser obtido. A mensagem é do yt-dlp, encurtada."""


@dataclass(frozen=True)
class DownloadedMedia:
    """O áudio no disco mais o que a plataforma contou sobre o vídeo.

    O metadata não é enfeite: ``view_count`` é o único sinal de retenção real que
    este caminho traz, e é a razão de existir dele — o upvote do Reddit diz
    quantos votaram, a visualização diz que a história prendeu. Fica no
    ``metadata`` do run para poder ser comparado depois contra o desempenho do
    vídeo que saiu daqui.
    """

    path: Path
    video_id: str
    title: str
    uploader: str
    duration: float
    view_count: int | None
    like_count: int | None
    webpage_url: str

    def as_metadata(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "uploader": self.uploader,
            "video_duration": round(self.duration),
            "view_count": self.view_count,
            "like_count": self.like_count,
            "video_url": self.webpage_url,
        }


def _impersonate_target(client: str):
    """Alvo de impersonation, ou erro claro quando ``curl_cffi`` não está lá.

    O yt-dlp aceita ``impersonate`` sem a dependência e falha depois, no meio da
    extração, com a mensagem genérica do TikTok. Levantar aqui troca um mistério
    por uma linha de requirements.
    """
    from yt_dlp.networking.impersonate import ImpersonateTarget

    target = ImpersonateTarget(client)
    try:
        from curl_cffi import requests  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise DownloadError(
            f"impersonation '{client}' indisponível: curl_cffi não está instalado. "
            "Sem ele o TikTok devolve uma página vazia com HTTP 200."
        ) from exc
    return target


def _ydl_opts(dest: Path, impersonate: str, sleep_requests: int, retries: int) -> dict[str, Any]:
    return {
        # `ba/b`: o TikTok normalmente não expõe faixa de áudio separada, então a
        # queda para `b` (melhor combinado) é o caso comum, não a exceção.
        "format": "ba/b",
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "impersonate": _impersonate_target(impersonate),
        "extractor_retries": retries,
        "sleep_interval_requests": sleep_requests,
    }


def _extract(url: str, opts: dict[str, Any]) -> dict:
    from yt_dlp import YoutubeDL

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info is None:
        raise DownloadError("yt-dlp não devolveu informação do vídeo")
    return info


def download_audio(
    url: str,
    dest: Path,
    *,
    impersonate: str = "chrome",
    api_hostname: str = "",
    sleep_requests: int = 8,
    retries: int = 4,
    max_duration_seconds: int = 0,
) -> DownloadedMedia:
    """Baixa o áudio de ``url`` em ``dest`` e devolve o arquivo mais o metadata.

    A segunda tentativa com ``api_hostname`` só roda quando a primeira falha: o
    caminho da API mobile resolve vídeos que o parser da página recusa, mas é o
    mais sujeito a mudar sem aviso, então não vira o caminho padrão.

    ``max_duration_seconds`` corta **antes** de transcrever, que é onde o custo
    está — um vídeo longo por engano custaria minutos de CPU antes de alguém
    perceber. ``0`` desliga o teto.
    """
    dest.mkdir(parents=True, exist_ok=True)
    opts = _ydl_opts(dest, impersonate, sleep_requests, retries)

    try:
        info = _extract(url, opts)
    except DownloadError:
        raise
    except Exception as first:  # noqa: BLE001 — o yt-dlp levanta uma família larga
        if not api_hostname:
            raise DownloadError(str(first)[-400:]) from first
        log.warning("download_retry_mobile_api", url=url, error=str(first)[-200:])
        fallback = dict(opts, extractor_args={"tiktok": {"api_hostname": [api_hostname]}})
        try:
            info = _extract(url, fallback)
        except Exception as second:  # noqa: BLE001
            raise DownloadError(str(second)[-400:]) from second

    duration = float(info.get("duration") or 0)
    if max_duration_seconds and duration > max_duration_seconds:
        # Apaga o que baixou: o arquivo não vai ser usado e o diretório é
        # temporário só quando o chamador quer que seja.
        shutil.rmtree(dest, ignore_errors=True)
        raise DownloadError(
            f"vídeo de {duration:.0f}s passa do teto de {max_duration_seconds}s"
        )

    files = sorted(p for p in dest.iterdir() if p.is_file() and p.suffix != ".part")
    if not files:
        raise DownloadError("yt-dlp terminou sem erro mas não gravou arquivo")

    return DownloadedMedia(
        path=files[0],
        video_id=str(info.get("id") or ""),
        title=str(info.get("title") or ""),
        uploader=str(info.get("uploader") or ""),
        duration=duration,
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        webpage_url=str(info.get("webpage_url") or url),
    )
