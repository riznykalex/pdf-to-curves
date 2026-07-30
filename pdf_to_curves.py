#!/usr/bin/env python3
"""
PDF → Криві (Outline Fonts) — версія з чергою файлів
Перетворює текст у PDF на векторні криві (контури) за допомогою Ghostscript.
Підтримує вибір і обробку декількох файлів одразу, з чергою та статусом
по кожному файлу.

Вимоги:
  - Python 3.8+
  - Ghostscript, встановлений у системі (команда `gs`, у Windows — `gswin64c`)
  - Немає сторонніх Python-пакетів — лише стандартна бібліотека.
"""

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def find_ghostscript() -> str | None:
    for name in ("gs", "gswin64c", "gswin32c"):
        path = shutil.which(name)
        if path:
            return path
    return None


STATUS_QUEUED = "У черзі"
STATUS_RUNNING = "Обробка..."
STATUS_DONE = "Готово"
STATUS_ERROR = "Помилка"


class FileJob:
    def __init__(self, src: str):
        self.src = src
        base, ext = os.path.splitext(src)
        self.dst = f"{base}_curves.pdf"
        self.status = STATUS_QUEUED
        self.error = ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF → Криві — пакетна обробка")
        self.geometry("760x480")
        self.minsize(640, 400)

        self.gs_path = find_ghostscript()
        self.jobs: list[FileJob] = []
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.running = False

        self._build_ui()
        self.after(150, self._poll_ui_queue)

        if not self.gs_path:
            messagebox.showwarning(
                "Ghostscript не знайдено",
                "Не вдалося знайти Ghostscript у системі.\n\n"
                "Встановіть його і перезапустіть застосунок:\n"
                "• Windows: https://ghostscript.com/releases/gsdnld.html\n"
                "• macOS: brew install ghostscript\n"
                "• Linux: sudo apt install ghostscript",
            )

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Button(top, text="Додати файли...", command=self.add_files).pack(
            side="left"
        )
        ttk.Button(top, text="Видалити обрані", command=self.remove_selected).pack(
            side="left", padx=6
        )
        ttk.Button(top, text="Очистити чергу", command=self.clear_queue).pack(
            side="left"
        )

        # Таблиця черги
        columns = ("file", "status")
        self.tree = ttk.Treeview(
            self, columns=columns, show="headings", selectmode="extended"
        )
        self.tree.heading("file", text="Файл")
        self.tree.heading("status", text="Статус")
        self.tree.column("file", width=520, anchor="w")
        self.tree.column("status", width=140, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.tree.tag_configure(STATUS_DONE, foreground="#0a7d1f")
        self.tree.tag_configure(STATUS_ERROR, foreground="#b00020")
        self.tree.tag_configure(STATUS_RUNNING, foreground="#a05a00")

        # Прогрес по поточному файлу
        cur_frame = ttk.Frame(self)
        cur_frame.pack(fill="x", padx=10)
        self.current_label = ttk.Label(cur_frame, text="Немає активної обробки.")
        self.current_label.pack(anchor="w")
        self.current_progress = ttk.Progressbar(cur_frame, mode="indeterminate")
        self.current_progress.pack(fill="x", pady=(2, 8))

        # Загальний прогрес по черзі
        overall_frame = ttk.Frame(self)
        overall_frame.pack(fill="x", padx=10)
        self.overall_label = ttk.Label(overall_frame, text="Файлів у черзі: 0")
        self.overall_label.pack(anchor="w")
        self.overall_progress = ttk.Progressbar(
            overall_frame, mode="determinate", maximum=1, value=0
        )
        self.overall_progress.pack(fill="x", pady=(2, 8))

        # Кнопка запуску
        self.btn_run = ttk.Button(
            self, text="Перетворити всі файли на криві", command=self.start_processing
        )
        self.btn_run.pack(pady=(0, 10))

        ttk.Label(
            self,
            text=(
                "Результат зберігається поруч з оригіналом як «ім'я_curves.pdf».\n"
                "Увага: текст стане векторною графікою — його більше не можна буде "
                "виділити чи скопіювати."
            ),
            foreground="#666",
            justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 8))

    # ---------- Керування чергою ----------

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Оберіть один або декілька PDF файлів",
            filetypes=[("PDF files", "*.pdf")],
        )
        existing = {job.src for job in self.jobs}
        for p in paths:
            if p not in existing:
                job = FileJob(p)
                self.jobs.append(job)
                self.tree.insert(
                    "", "end", iid=p, values=(os.path.basename(p), job.status)
                )
        self._update_overall_label()

    def remove_selected(self):
        if self.running:
            messagebox.showinfo("Зачекайте", "Дочекайтесь завершення поточної обробки.")
            return
        selected = self.tree.selection()
        for iid in selected:
            self.jobs = [j for j in self.jobs if j.src != iid]
            self.tree.delete(iid)
        self._update_overall_label()

    def clear_queue(self):
        if self.running:
            messagebox.showinfo("Зачекайте", "Дочекайтесь завершення поточної обробки.")
            return
        self.jobs.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._update_overall_label()

    def _update_overall_label(self):
        self.overall_label.config(text=f"Файлів у черзі: {len(self.jobs)}")
        self.overall_progress.config(maximum=max(len(self.jobs), 1), value=0)

    # ---------- Обробка ----------

    def start_processing(self):
        if self.running:
            return
        if not self.jobs:
            messagebox.showinfo("Черга порожня", "Спочатку додайте PDF файли.")
            return
        if not self.gs_path:
            messagebox.showerror("Помилка", "Ghostscript не знайдено в системі.")
            return

        for job in self.jobs:
            job.status = STATUS_QUEUED
            job.error = ""
            self.tree.item(job.src, values=(os.path.basename(job.src), job.status))
        self.overall_progress.config(value=0)

        self.running = True
        self.btn_run.config(state="disabled")
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _worker(self):
        total = len(self.jobs)
        for idx, job in enumerate(self.jobs, start=1):
            self.ui_queue.put(("start", job, None, None, idx, total))
            cmd = [
                self.gs_path,
                "-o",
                job.dst,
                "-sDEVICE=pdfwrite",
                "-dNoOutputFonts",
                "-dNOPAUSE",
                "-dBATCH",
                "-dSAFER",
                job.src,
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600
                )
                ok = result.returncode == 0 and os.path.isfile(job.dst)
                err = result.stderr if not ok else ""
            except Exception as e:
                ok = False
                err = str(e)

            self.ui_queue.put(("done", job, ok, err, idx, total))

        self.ui_queue.put(("all_done", None, None, None, None, None))

    def _poll_ui_queue(self):
        try:
            while True:
                kind, job, a, b, idx, total = self.ui_queue.get_nowait()
                if kind == "start":
                    job.status = STATUS_RUNNING
                    self.tree.item(
                        job.src,
                        values=(os.path.basename(job.src), job.status),
                        tags=(STATUS_RUNNING,),
                    )
                    self.current_label.config(
                        text=f"Обробка файлу {idx}/{total}: {os.path.basename(job.src)}"
                    )
                    self.current_progress.start(12)
                elif kind == "done":
                    ok, err = a, b
                    self.current_progress.stop()
                    if ok:
                        job.status = STATUS_DONE
                        self.tree.item(
                            job.src,
                            values=(os.path.basename(job.src), job.status),
                            tags=(STATUS_DONE,),
                        )
                    else:
                        job.status = STATUS_ERROR
                        job.error = err
                        self.tree.item(
                            job.src,
                            values=(os.path.basename(job.src), job.status),
                            tags=(STATUS_ERROR,),
                        )
                    self.overall_progress.config(value=idx)
                elif kind == "all_done":
                    self.running = False
                    self.btn_run.config(state="normal")
                    self.current_label.config(text="Обробку завершено.")
                    errors = [j for j in self.jobs if j.status == STATUS_ERROR]
                    if errors:
                        details = "\n".join(
                            f"• {os.path.basename(j.src)}: {j.error[:200]}"
                            for j in errors
                        )
                        messagebox.showwarning(
                            "Завершено з помилками",
                            f"{len(errors)} з {len(self.jobs)} файлів не вдалося обробити:\n\n{details}",
                        )
                    else:
                        messagebox.showinfo(
                            "Готово", f"Усі {len(self.jobs)} файлів успішно оброблено."
                        )
        except queue.Empty:
            pass
        self.after(150, self._poll_ui_queue)


if __name__ == "__main__":
    App().mainloop()