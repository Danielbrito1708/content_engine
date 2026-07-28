"""Corte da frase gancho do início da parte 1.

O gancho é narrado sozinho na abertura, sobre o card, e é **literalmente** a
primeira frase da parte 1 — narrar a parte inteira faria o vídeo dizer a mesma
frase duas vezes seguidas, justamente nos segundos em que a retenção se decide.

Puro: entra texto, sai texto. Quem chama decide o que fazer quando não dá para
cortar.
"""
import unicodedata


def strip_hook(script: str, hook: str) -> str | None:
    """O roteiro sem a frase gancho na frente, ou ``None`` se ela não estiver lá.

    Compara ignorando espaços em branco, maiúsculas e forma de acentuação. As
    duas strings são normalizadas para NFC **inteiras**, não caractere a
    caractere: em NFD o til de `manhã` é um caractere separado do `a`, então uma
    comparação por caractere não teria nem o mesmo número de posições dos dois
    lados. O texto devolvido é o roteiro em NFC, do ponto em que o gancho
    termina — fora o prefixo cortado, nada é reescrito.

    Devolve ``None`` — e não o roteiro intacto — quando:

    - não há gancho, ou o roteiro não começa por ele (modelo não copiou a frase
      literalmente, como o prompt pede);
    - sobra só espaço em branco depois do corte (a parte inteira era o gancho).

    Nos dois casos quem chama tem de saber que o corte não aconteceu, para
    registrar o motivo: o vídeo sai com a frase repetida, que é o defeito que
    esta função existe para evitar, e isso não pode acontecer em silêncio.
    """
    if not hook or not hook.strip() or not script:
        return None

    script = unicodedata.normalize("NFC", script)
    hook_chars = [c.casefold() for c in unicodedata.normalize("NFC", hook) if not c.isspace()]
    if not hook_chars:
        return None

    matched = 0
    cut = None
    for index, char in enumerate(script):
        if char.isspace():
            continue
        if char.casefold() != hook_chars[matched]:
            return None
        matched += 1
        if matched == len(hook_chars):
            cut = index + 1
            break

    if cut is None:  # roteiro acaba no meio do gancho
        return None

    remainder = script[cut:].lstrip()
    return remainder or None
