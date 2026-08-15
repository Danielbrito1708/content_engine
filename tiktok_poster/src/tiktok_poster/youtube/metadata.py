"""Metadata do post de YouTube no Buffer (`YoutubePostMetadataInput`).

O TikTok aceita um post com texto e vídeo e nada mais. O YouTube não: `title` e
`categoryId` são **obrigatórios na criação** — sem eles o Buffer recusa a
mutation inteira. Este módulo é o que transforma o que o pipeline já sabe sobre
a história (o `content_type` da classificação, o número da parte) nos campos
que a API exige, e é puro para que essa tradução seja testável sem rede.
"""

#: Teto do título no YouTube. A API recusa acima disso.
MAX_TITLE_CHARS = 100

#: "People & Blogs". É onde história narrada em primeira pessoa se encaixa, e é
#: o destino de qualquer `content_type` que não tenha categoria mais específica.
DEFAULT_CATEGORY_ID = "22"

#: IDs da lista oficial do YouTube, documentada no próprio schema do Buffer.
#: Só mapeia o que tem correspondência honesta: "drama" e "suspense" não têm
#: categoria própria no YouTube e ficam melhor em People & Blogs que forçados
#: em Entertainment.
CATEGORY_BY_CONTENT_TYPE = {
    "comédia": "23",  # Comedy
    "comedia": "23",
    "educativo": "27",  # Education
    "entretenimento": "24",  # Entertainment
    "motivacional": "22",  # People & Blogs
}


def youtube_category_id(content_type: str | None, default: str = DEFAULT_CATEGORY_ID) -> str:
    """Categoria do vídeo a partir do `content_type` da classificação.

    Cai no default para qualquer valor desconhecido: a categoria é campo
    obrigatório, então errar para People & Blogs é o único desfecho que ainda
    publica o vídeo.
    """
    key = (content_type or "").strip().lower()
    return CATEGORY_BY_CONTENT_TYPE.get(key, default)


def compose_title(title: str, part_number: int, total_parts: int) -> str:
    """Título final, com o rótulo de parte quando a história foi dividida.

    O rótulo entra aqui e não no `llm_service` porque quem sabe quantas partes
    a história tem é o pipeline, não quem escreveu o título — a mesma razão pela
    qual `total_parts` vem no request e não da classificação.

    O corte protege o rótulo, não o título: numa série, "(Parte 2/3)" é a
    informação que não pode faltar, então é o título que encolhe para caber.
    """
    base = " ".join((title or "").split())
    label = f" (Parte {part_number}/{total_parts})" if total_parts > 1 else ""

    room = MAX_TITLE_CHARS - len(label)
    if len(base) > room:
        base = base[:room].rsplit(" ", 1)[0].strip().rstrip(" ,;:-–—") or base[:room].strip()

    return f"{base}{label}"


def build_metadata(
    title: str,
    category_id: str,
    privacy: str = "public",
    made_for_kids: bool = False,
    notify_subscribers: bool = True,
    ai_disclosed: bool = True,
) -> dict:
    """Bloco `metadata.youtube` do `createPost`.

    ``ai_disclosed`` sai `True` por padrão: a narração é voz sintética, e o
    YouTube pede que conteúdo gerado por IA seja declarado. É config e não
    constante porque é uma declaração sobre o vídeo, mas o default é o honesto.
    """
    return {
        "youtube": {
            "title": title,
            "categoryId": str(category_id),
            "privacy": privacy,
            "madeForKids": made_for_kids,
            "notifySubscribers": notify_subscribers,
            "isAiGenerated": ai_disclosed,
        }
    }
