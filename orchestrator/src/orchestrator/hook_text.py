"""Se a parte já abre com a frase gancho.

O gancho é narrado em arquivo próprio para abrir o vídeo sobre o card. Numa
parte que **já começa** por ele — a parte 1, por construção — esse arquivo
sobra: quem diz a frase é a narração da própria parte, e montar os dois faria o
vídeo repetir a frase logo em seguida.

Puro: entram dois textos, sai um booleano.
"""
import unicodedata


def opens_with_hook(script: str, hook: str) -> bool:
    """``True`` se ``script`` começa pela frase ``hook``.

    Compara ignorando espaço em branco, caixa e forma de acentuação. As duas
    strings são normalizadas para NFC **inteiras**, não caractere a caractere:
    em NFD o til de `manhã` é um caractere separado do `a`, então uma comparação
    por caractere não teria nem o mesmo número de posições dos dois lados.

    O prompt manda copiar a frase literalmente da primeira frase da parte 1, e é
    isso que acontece na prática — a tolerância cobre diferença de serialização,
    não uma reescrita. Um gancho reescrito pelo modelo dá ``False``, e o vídeo
    volta a abrir com a narração separada, que é o comportamento seguro: a frase
    é dita uma vez de um jeito ou de outro.
    """
    if not script or not hook or not hook.strip():
        return False

    script_chars = (c for c in unicodedata.normalize("NFC", script) if not c.isspace())
    hook_chars = [c.casefold() for c in unicodedata.normalize("NFC", hook) if not c.isspace()]

    for expected in hook_chars:
        char = next(script_chars, None)
        if char is None or char.casefold() != expected:
            return False
    return True
