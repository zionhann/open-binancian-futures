from abc import ABC, abstractmethod


class Runner(ABC):
    @abstractmethod
    def run(self): ...

    @abstractmethod
    def close(self): ...
