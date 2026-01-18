"""
Abstract base class for site handlers.
"""
from abc import ABC, abstractmethod
from typing import Callable, Optional


class BaseSiteHandler(ABC):
    """
    Abstract base class that all site handlers must implement.
    Provides a common interface for processing manga downloads from different sites.
    """
    
    @abstractmethod
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Process a manga URL and download it.
        
        Args:
            url: The manga URL to process
            log_callback: Function to call for logging messages
            check_cancel: Function that returns True if user cancelled
            progress_callback: Optional function to call with (current, total) progress
        """
        pass
    
    @staticmethod
    def get_supported_domains() -> list:
        """
        Returns a list of domain strings this handler supports.
        
        Returns:
            List of domain strings (e.g., ['tmohentai.com'])
        """
        return []
