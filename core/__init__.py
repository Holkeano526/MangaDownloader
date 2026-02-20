
"""
Core package for Manga Downloader.
"""
from .handler import process_entry
# We expose process_entry for easier imports
# core.config should be imported directly if needed to modify runtime flags
