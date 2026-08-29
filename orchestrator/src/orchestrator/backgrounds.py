"""Which background clip a given part gets.

The pipeline used to point every render at one fixed key, so every video in the
channel shared the same footage from the same second of the same file. Cutting
the source into clips and rotating between them is what makes two consecutive
posts not look like the same video with different words on top.
"""

import hashlib
from collections.abc import Mapping


def pick_background(
    keys: list[str],
    run_id: str,
    part_number: int,
    used: Mapping[str, int] | None = None,
) -> str:
    """Pick a clip for one part: the least-used one, ties broken deterministically.

    ``used`` maps clip key to how many parts already went out on it. The clip
    comes from the subset with the fewest uses, so **no clip repeats until the
    whole library has been through**. This is the part that was missing: the
    picker used to be ``sha256(run_id) % len(keys)``, a draw *with replacement*
    that only looked like a rotation. Over 47 posts on a 40-clip library it
    reused a clip 20 times, the first repeat landing on the second day — which
    is the opposite of what a rotation is for.

    Ties are broken by hash rather than by list order so a fresh cycle does not
    walk the library alphabetically, which would make consecutive posts share
    consecutive footage from the same source file.

    Deterministic for two reasons. A part re-rendered after a restart has to
    come back with the same footage, or a retry silently produces a different
    video than the one already reviewed — the caller also persists the choice,
    which is what holds across a change in ``used``. And the seed includes the
    part number, so the parts of a single run never land on the same clip, which
    is exactly where repetition would be most visible, in a series posted
    back-to-back.

    A clip added to the library later starts at zero uses, so it is picked
    before anything already in rotation — new footage reaches the channel
    without waiting out the current cycle.
    """
    if not keys:
        raise ValueError("no background clips available")

    counts = used or {}
    fewest = min(counts.get(key, 0) for key in keys)
    candidates = [key for key in keys if counts.get(key, 0) == fewest]

    seed = f"{run_id}:{part_number}".encode()
    index = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(candidates)
    return candidates[index]


def plan_segments(duration: float, segment_seconds: int, min_tail: int) -> list[int]:
    """Os offsets de início dos segmentos de um vídeo, em segundos.

    ``min_tail`` descarta a sobra final quando ela é curta demais para virar
    fundo — um resto de 11 s viraria um clipe que dá loop seis vezes debaixo de
    uma narração de um minuto, que é o defeito que a biblioteca ASMR já tem em
    quatro clipes. A sobra só entra se sozinha já valer um clipe.

    Vídeo inteiro menor que ``min_tail`` devolve lista vazia: não há segmento
    aproveitável, e é melhor a fonte sumir do manifesto do que entrar um clipe
    que ninguém quer ver repetido.
    """
    if duration < min_tail:
        return []
    inteiros = int(duration // segment_seconds)
    starts = [i * segment_seconds for i in range(inteiros)]
    sobra = duration - inteiros * segment_seconds
    if sobra >= min_tail:
        starts.append(inteiros * segment_seconds)
    return starts or ([0] if duration >= min_tail else [])


def segment_key(prefix: str, video_id: str, start: int) -> str:
    """Nome do objeto de um segmento.

    O offset entra no nome, não um índice sequencial: assim a chave é uma função
    da fonte e do trecho, e regerar o manifesto com outro `segment_seconds` não
    faz uma chave antiga apontar para outro pedaço de vídeo — ela simplesmente
    deixa de ser citada. Chave estável é o que permite o cache sobreviver a uma
    reconstrução do manifesto.
    """
    return f"{prefix}{video_id}_{int(start):05d}.mp4"


def manifest_keys(manifest: dict) -> list[str]:
    """As chaves candidatas de um manifesto, ordenadas.

    Ordenada pela mesma razão que ``list_keys`` é: o desempate da rotação é por
    hash, mas a lista precisa ser estável entre chamadas para que a escolha seja
    reproduzível.
    """
    return sorted(str(c["key"]) for c in manifest.get("clips", []) if c.get("key"))


def manifest_entry(manifest: dict, key: str) -> dict | None:
    """A entrada de uma chave, ou ``None`` se ela não vem do manifesto.

    ``None`` é a resposta esperada para um clipe subido à mão no bucket — eles
    convivem com os do manifesto e nunca precisam ser materializados.
    """
    for clip in manifest.get("clips", []):
        if clip.get("key") == key:
            return clip
    return None


#: Extensões aceitas como clipe de fundo.
CLIP_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")


def is_clip(key: str) -> bool:
    """Se a chave é um vídeo, e não outro objeto qualquer sob o prefixo.

    ``list_keys`` devolve o prefixo inteiro, e nada garante que só haja vídeo
    ali: o manifesto foi parar fora de `assets/backgrounds/` justamente porque
    um `.json` no meio dos clipes entraria no sorteio e o render tentaria montar
    um JSON como movie strip. O filtro é o cinto por cima da suspensória — vale
    para qualquer arquivo solto que apareça ali depois.
    """
    return key.lower().endswith(CLIP_SUFFIXES)
