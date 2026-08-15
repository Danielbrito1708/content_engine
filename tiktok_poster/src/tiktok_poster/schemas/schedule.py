from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleRequest(BaseModel):
    video_key: str
    classification: dict
    part_number: int
    series_id: str
    #: Quantas partes a história tem ao todo. Vem do orchestrador, que é quem
    #: conta as partes criadas — antes era lido de `classification["parts"]`,
    #: chave que o `llm_service` nunca preencheu, então a caption dizia sempre
    #: "1/1" mesmo em série dividida.
    total_parts: int = Field(default=1, ge=1)
    #: Horário já agendado da parte anterior. Presente só em partes 2+: é o que
    #: faz a continuação sair um intervalo depois dela, em vez de cair no
    #: próximo horário livre do calendário.
    follows_at: datetime | None = None
    #: Título do vídeo no YouTube, vindo do refino. Opcional porque o TikTok não
    #: tem título e um orchestrador antigo não manda o campo — nesse caso o
    #: poster cai no CTA da parte, que é o único texto que sobra.
    youtube_title: str | None = None


class ScheduleResponse(BaseModel):
    scheduled_at: datetime
    #: ID do post no TikTok. O nome genérico é histórico — quando havia um
    #: destino só, "o post do Buffer" e "o post do TikTok" eram a mesma coisa.
    buffer_update_id: str
    #: ID do post no YouTube. `None` quando o destino está desligado ou quando o
    #: agendamento lá falhou; `youtube_error` diz qual dos dois.
    youtube_update_id: str | None = None
    #: Por que o vídeo não foi ao YouTube. Existe porque a falha é **degradável**
    #: — o run segue `scheduled` com o TikTok agendado —, e sem este campo a
    #: ausência do post no YouTube não apareceria em lugar nenhum.
    youtube_error: str | None = None
    #: Se o destino estava ligado (canal configurado + `[youtube] enabled`).
    #: Separa "não publicou porque está desligado" de "não publicou e devia ter
    #: publicado" — só o segundo é degradação. Sem essa distinção, todo run
    #: dispararia aviso enquanto o canal ainda não estivesse conectado.
    youtube_enabled: bool = False
