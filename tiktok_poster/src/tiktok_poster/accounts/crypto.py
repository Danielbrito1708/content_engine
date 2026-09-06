from cryptography.fernet import Fernet, InvalidToken

from src.core import settings


class CredentialsKeyMissing(RuntimeError):
    """`ACCOUNT_CREDENTIALS_KEY` não está configurada.

    Só levanta quando alguém tenta cifrar ou decifrar de verdade — não no boot
    — para não quebrar quem opera só a conta default (env vars), que nunca
    passa por aqui.
    """


def _credentials_key() -> str | None:
    """`ACCOUNT_CREDENTIALS_KEY`, ou `None` se não configurada.

    Função e não leitura direta porque `settings.env` é um modelo congelado e
    não aceita `monkeypatch.setattr` — mesma costura de `_youtube_channel_id()`
    em `api/routes/schedule.py`.
    """
    return settings.env.account_credentials_key


def _fernet() -> Fernet:
    key = _credentials_key()
    if not key:
        raise CredentialsKeyMissing(
            "ACCOUNT_CREDENTIALS_KEY não configurada — necessária para cadastrar "
            "ou usar credenciais de conta além da default."
        )
    return Fernet(key.encode())


def encrypt_token(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_token(enc: str) -> str:
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken as exc:
        raise CredentialsKeyMissing(
            "ACCOUNT_CREDENTIALS_KEY não decifra este token — chave errada ou trocada."
        ) from exc
