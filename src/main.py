"""
Main entry point for the Manga Downloader application.
"""
import tkinter as tk
from .gui.downloader_app import DownloaderApp


def main():
    """Initialize and run the application."""
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
