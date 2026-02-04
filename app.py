import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import asyncio
import core 

class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cancelled = False
        self.root.title("Universal Manga Downloader")
        self.root.geometry("800x600")
        
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
        
        self.placeholder_text = "Pega tu URL aquí..."
        self.url_entry.insert(0, self.placeholder_text)
        self.url_entry.config(foreground='grey')

        self.url_entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.url_entry.bind("<FocusOut>", self._on_entry_focus_out)
        
        self.btn_start = ttk.Button(input_frame, text="Descargar PDF", command=self.start_process)
        self.btn_start.pack(fill=tk.X, pady=(0, 5))
        
        self.btn_cancel = ttk.Button(input_frame, text="Cancelar Detener", command=self.cancel_process, state='disabled')
        self.btn_cancel.pack(fill=tk.X, pady=(0, 5))

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
        
        # File Logging
        try:
            with open("downloader_debug.log", "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except: pass

    def start_process(self) -> None:
        self.cancelled = False
        url = self.url_entry.get().strip()
        if not url or url == self.placeholder_text:
            messagebox.showwarning("Aviso", "Por favor ingrese una URL.")
            return

        supported_domains = ["tmohentai", "m440.in", "mangas.in", "hentai2read", "hitomi.la", "nhentai.net", "zonatmo.com"]
        if not any(domain in url for domain in supported_domains):
             messagebox.showwarning("Aviso", "URL no soportada.\nDominios válidos: tmohentai, m440.in, hentai2read, hitomi.la, nhentai.net, zonatmo")
             return
        
        # Init Log
        try:
            with open("downloader_debug.log", "w", encoding="utf-8") as f:
                f.write("=== LOG START ===\n")
        except Exception as e:
            print(f"Error escribiendo log: {e}")
        
        self.progress['value'] = 0
        self.btn_start.config(state='disabled')
        self.btn_cancel.config(state='normal')
        self.log_area.config(state='normal')
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state='disabled')
        
        # Configurar Core para App
        core.OPEN_RESULT_ON_FINISH = True
        
        # Run in separate thread to prevent GUI freeze
        threading.Thread(target=self.run_async, args=(url,), daemon=True).start()

    def cancel_process(self) -> None:
        self.cancelled = True
        self.log("[AVISO] Solicitando cancelación...")
        self.btn_cancel.config(state='disabled')

    def run_async(self, url: str) -> None:
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
            loop.run_until_complete(core.process_entry(url, safe_log, check_cancel, progress_callback=safe_progress))
        finally:
            loop.close()
            self.root.after(0, lambda: self.reset_buttons())
            
    def reset_buttons(self):
        self.btn_start.config(state='normal')
        self.btn_cancel.config(state='disabled')

if __name__ == "__main__":
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()
