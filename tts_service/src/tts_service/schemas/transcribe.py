from pydantic import BaseModel, Field


class TranscribeRequest(BaseModel):
    """Uma URL de vídeo para virar texto.

    Não há campo de modelo nem de idioma: quem chama é o ``content_scout``, que
    não tem motivo para opinar sobre qual whisper roda. Deixar isso na config do
    serviço é o que permite trocar o modelo sem redeploy de quem consome.
    """

    url: str = Field(min_length=8, max_length=2048)


class TranscribeSource(BaseModel):
    """O que a plataforma contou sobre o vídeo.

    ``view_count`` e ``like_count`` são opcionais porque nem toda plataforma os
    expõe — e ``None`` aqui significa "não informado", não "zero".
    """

    video_id: str
    title: str
    uploader: str
    video_duration: int
    view_count: int | None = None
    like_count: int | None = None
    video_url: str


class TranscribeResponse(BaseModel):
    text: str
    char_count: int
    #: Duração do áudio segundo o whisper, que pode divergir da duração que a
    #: plataforma reporta — é a medida do que foi de fato transcrito.
    audio_duration: float
    source: TranscribeSource
