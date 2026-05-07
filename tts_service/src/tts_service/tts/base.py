from abc import ABC, abstractmethod


class BaseTTSClient(ABC):
    @abstractmethod
    async def generate(self, text: str) -> bytes:
        """Returns raw MP3 bytes."""
