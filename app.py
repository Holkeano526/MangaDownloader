"""
Module: app
Description: A standalone Tkinter Desktop GUI client for the Manga Downloader.
It provides a native Windows interface for users who prefer a local executable 
over the web-based Next.js dashboard. It wraps the core downloading logic in asynchronous 
threads to keep the UI responsive during downloads.
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import asyncio
import core 
import core.config

class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cancelled = False
        self.queue = []
        self.is_processing = False
        self.root.title("Universal Manga Downloader")
        self.root.geometry("800x700")
        
        # Styles
        style = ttk.Style()
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TLabel", font=("Segoe UI", 11))
        
        # Main Layout
        main_frame = ttk.Frame(root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        ttk.Label(main_frame, text="Manga PDF Downloader", font=("Segoe UI", 16, "bold")).pack(pady=(0, 20))
        
        # Input Area
        input_frame = ttk.LabelFrame(main_frame, text="Input", padding="10")
        input_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(input_frame, text="URL (TMO, M440, H2R, Hitomi, nhentai, ZonaTMO):").pack(anchor=tk.W)
        self.url_entry = ttk.Entry(input_frame)
        self.url_entry.pack(fill=tk.X, pady=(5, 10))
        
        self.placeholder_text = "Pegar URL aquí..."
        self.url_entry.insert(0, self.placeholder_text)
        self.url_entry.config(foreground='grey')

        self.url_entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.url_entry.bind("<FocusOut>", self._on_entry_focus_out)
        
        self.btn_start = ttk.Button(input_frame, text="Añadir a la cola", command=self.add_to_queue)
        self.btn_start.pack(fill=tk.X, pady=(0, 5))
        
        self.btn_cancel = ttk.Button(input_frame, text="Cancelar Descarga Actual", command=self.cancel_process, state='disabled')
        self.btn_cancel.pack(fill=tk.X, pady=(0, 5))

        # Queue Area
        queue_frame = ttk.LabelFrame(main_frame, text="Cola de Descargas", padding="10")
        queue_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 15))
        
        self.queue_list = tk.Listbox(queue_frame, height=4, font=("Segoe UI", 10))
        self.queue_list.pack(fill=tk.BOTH, expand=True)

        # Progress Bar
        self.progress = ttk.Progressbar(input_frame, orient="horizontal", length=100, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # Logging Area
        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_area = scrolledtext.ScrolledText(log_frame, state='disabled', font=("Consolas", 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)

    def _on_entry_focus_in(self, event) -> None:
        if self.url_entry.get() == self.placeholder_text:
            self.url_entry.delete(0, tk.END)
            self.url_entry.config(foreground='black')

    def _on_entry_focus_out(self, event) -> None:
        if not self.url_entry.get():
            self.url_entry.insert(0, self.placeholder_text)
            self.url_entry.config(foreground='grey')

    def log(self, message: str) -> None:
        """Appends message to GUI log and File log."""
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')
        
        try:
            with open("downloader_debug.log", "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except: pass

    def update_queue_ui(self):
        self.queue_list.delete(0, tk.END)
        for i, item in enumerate(self.queue):
            status = "Procesando" if (i == 0 and self.is_processing) else "Pendiente"
            self.queue_list.insert(tk.END, f"[{status}] {item}")

    def add_to_queue(self) -> None:
        url = self.url_entry.get().strip()
        if not url or url == self.placeholder_text:
            messagebox.showwarning("Aviso", "Por favor ingrese una URL.")
            return

        supported_domains = ["tmohentai", "m440.in", "mangas.in", "hentai2read", "hitomi.la", "nhentai.net", "zonatmo.com", "fakku.cc", "shademanga.com"]
        if not any(domain in url for domain in supported_domains):
             messagebox.showwarning("Aviso", "URL no soportada.\nDominios válidos: tmohentai, m440.in, hentai2read, hitomi.la, nhentai.net, zonatmo, fakku.cc, shademanga.com")
             return
             
        self.queue.append(url)
        self.update_queue_ui()
        self.url_entry.delete(0, tk.END)
        self.url_entry.insert(0, self.placeholder_text)
        self.url_entry.config(foreground='grey')
        
        if not self.is_processing:
            threading.Thread(target=self.process_queue, daemon=True).start()

    def process_queue(self) -> None:
        self.is_processing = True
        core.config.OPEN_RESULT_ON_FINISH = True
        
        try:
            with open("downloader_debug.log", "w", encoding="utf-8") as f:
                f.write("=== LOG START ===\n")
        except Exception as e:
            print(f"Error writing log: {e}")
            
        while self.queue:
            url = self.queue[0]
            self.cancelled = False
            
            self.root.after(0, self.update_queue_ui)
            self.root.after(0, lambda: self.btn_cancel.config(state='normal'))
            self.root.after(0, lambda: self.progress.config(value=0))
            self.root.after(0, lambda: self.log_area.config(state='normal'))
            self.root.after(0, lambda: self.log_area.delete(1.0, tk.END))
            self.root.after(0, lambda: self.log_area.config(state='disabled'))
            
            result = self.run_async_blocking(url)
            
            if self.queue:
                self.queue.pop(0)
                
            if isinstance(result, list):
                self.root.after(0, lambda: self.log(f"\n[INFO] Serie detectada. Añadiendo {len(result)} capítulos a la cola.\n"))
                self.queue = result + self.queue
            
            self.root.after(0, self.update_queue_ui)
            
        self.is_processing = False
        self.root.after(0, lambda: self.btn_cancel.config(state='disabled'))
        self.root.after(0, lambda: self.progress.config(value=0))

    def cancel_process(self) -> None:
        self.cancelled = True
        self.log("[INFO] Cancelando...")
        self.btn_cancel.config(state='disabled')

    def run_async_blocking(self, url: str):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        def safe_log(msg): self.root.after(0, self.log, msg)
        check_cancel = lambda: self.cancelled

        def safe_progress(current, total):
            def _update():
                self.progress['maximum'] = total
                self.progress['value'] = current
            self.root.after(0, _update)
        
        try:
            return loop.run_until_complete(core.process_entry(url, safe_log, check_cancel, progress_callback=safe_progress))
        finally:
            loop.close()

if __name__ == "__main__":
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()
