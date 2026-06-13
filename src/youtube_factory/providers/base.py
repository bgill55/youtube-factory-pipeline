from abc import ABC, abstractmethod

class VideoProvider(ABC):
    def __init__(self, config):
        self.config = config
        self.credits_used = 0
        self.credits_limit = 0

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def generate(self, prompt: str, output_path: str, duration: float = 5.0, aspect_ratio: str = "16:9") -> bool:
        pass

    @property
    def credits_remaining(self) -> int:
        return self.credits_limit - self.credits_used

    @abstractmethod
    def get_priority(self) -> int:
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(available={self.is_available()}, priority={self.get_priority()})"
