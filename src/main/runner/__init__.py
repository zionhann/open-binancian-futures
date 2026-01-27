from abc import ABC, abstractmethod
from typing import Self


class Runner(ABC):
    """
    Abstract base class for trading runners with context manager support.

    Runners can be used as context managers to ensure proper cleanup:
        with LiveTrading() as runner:
            runner.run()
    """

    @abstractmethod
    def run(self): ...

    @abstractmethod
    def close(self): ...

    def __enter__(self) -> Self:
        """Enter the runtime context (context manager protocol)."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Exit the runtime context and ensure cleanup (context manager protocol).

        Args:
            exc_type: Exception type if an exception was raised
            exc_val: Exception value if an exception was raised
            exc_tb: Exception traceback if an exception was raised
        """
        self.close()
