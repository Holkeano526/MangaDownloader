
from abc import ABC, abstractmethod
from typing import Callable, Optional

class BaseSiteHandler(ABC):
    """Abstract base class for all site handlers."""
    
    @staticmethod
    @abstractmethod
    def get_supported_domains() -> list:
        """Returns a list of domain strings supported by this handler."""
        pass

    @abstractmethod
    async def process(
        self, 
        url: str, 
        log_callback: Callable[[str], None], 
        check_cancel: Callable[[], bool], 
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Process the given URL to download manga/doujinshi.
        
        Args:
            url: The URL to process
            log_callback: Function to report logs
            check_cancel: Function returning True if cancellation is requested
            progress_callback: Optional function to report progress (current, total)
        """
        pass
