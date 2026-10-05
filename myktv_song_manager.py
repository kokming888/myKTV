import os
import re
import shutil
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


APP_VERSION = "v1.0"


class SongManagerViewMixin:
    """Build the local catalog organizer and manage its SQLite index."""

    def build_song_manager_tab(self, parent):
        colors = self.ui_colors
        output_dir = Path(self.config.get("output_dir", self.app_dir / "output"))
        self.song_inbox_var = tk.StringVar(value=self.config.get("song_inbox_dir", str(output_dir)))
        self.song_library_var = tk.StringVar(value=self.config.get("song_library_dir", str(output_dir / "KTV_Library")))
        self.song_search_var = tk.StringVar()
        self.song_language_filter_var = tk.StringVar(value="全部语言")
        self.song_tag_filter_var = tk.StringVar(value="")
        self.song_title_var = tk.StringVar()
        self.song_artist_var = tk.StringVar()
        self.song_language_var = tk.StringVar(value="Other")
        self.song_tags_var = tk.StringVar()
        self.song_path_preview_var = tk.StringVar(value="请先选择歌曲")
        self.song_manager_status_var = tk.StringVar(value="选择下载收件匣与曲库资料夹，然后扫描歌曲。")
        self.song_manager_count_var = tk.StringVar(value="尚未建立歌曲索引")
        self._song_manager_records = []
        self._song_manager_scanning = False
        self._song_manager_selected_id = None

        parent.grid_rowconfigure(3, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        header = tk.Frame(parent, bg=self._bg)
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        tk.Label(header, text="歌曲管理 / Song Library", bg=self._bg, fg=colors["primary"],
            font=("Arial", 14, "bold")).pack(side=tk.LEFT)
        tk.Label(header, textvariable=self.song_manager_status_var, bg=self._bg, fg="gray",
            font=("Arial", 9)).pack(side=tk.RIGHT, padx=5)

        paths = tk.LabelFrame(parent, text="资料夹 / Folders", bg=self._bg, fg=self._fg, padx=7, pady=5)
        paths.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        paths.grid_columnconfigure(1, weight=1)
        for row, label, variable, callback in [
            (0, "下载收件匣", self.song_inbox_var, self._choose_song_inbox),
            (1, "整理后曲库", self.song_library_var, self._choose_song_library),
        ]:
            tk.Label(paths, text=label, width=11, anchor="w", bg=self._bg, fg=self._fg).grid(row=row, column=0, sticky="w", pady=2)
            tk.Entry(paths, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=4, pady=2)
            tk.Button(paths, text="选择...", command=callback, bg="#f0f0f0").grid(row=row, column=2, padx=3)

        toolbar = tk.Frame(parent, bg=self._bg)
        toolbar.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        tk.Label(toolbar, text="搜寻", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(0, 4))
        search_entry = tk.Entry(toolbar, textvariable=self.song_search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 7))
        search_entry.bind("<KeyRelease>", lambda _event: self._render_song_catalog())
        ttk.Combobox(toolbar, textvariable=self.song_language_filter_var,
            values=["全部语言", "Mandarin", "Cantonese", "Hokkien", "Malay", "English", "Japanese", "Korean", "Other"],
            state="readonly", width=13).pack(side=tk.LEFT, padx=3)
        language_box = toolbar.winfo_children()[-1]
        language_box.bind("<<ComboboxSelected>>", lambda _event: self._render_song_catalog())
        tk.Button(toolbar, text="扫描资料夹", command=self.scan_song_folders,
            bg=colors["primary"], fg="white", relief="flat", padx=10, pady=5).pack(side=tk.LEFT, padx=(6, 0))

        content = tk.PanedWindow(parent, orient=tk.HORIZONTAL, sashwidth=5, bg=colors["border"], bd=0, relief="flat")
        content.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        list_panel = tk.Frame(content, bg=self._bg, padx=5, pady=5)
        detail_panel = tk.Frame(content, bg=self._bg, padx=10, pady=7)
        content.add(list_panel, minsize=430, stretch="always")
        content.add(detail_panel, minsize=230, stretch="always")

        tags_row = tk.Frame(list_panel, bg=self._bg)
        tags_row.pack(fill=tk.X, pady=(0, 5))
        tk.Label(tags_row, text="标签", bg=self._bg, fg="gray").pack(side=tk.LEFT, padx=(2, 4))
        for tag in ["全部", "Pop", "Band", "Duet", "Concert", "Ballad"]:
            value = "" if tag == "全部" else tag
            tk.Button(tags_row, text=tag, relief="flat", padx=7, pady=3,
                bg=colors["hover"] if not value else "white", fg=self._fg,
                command=lambda selected=value: self._set_song_tag_filter(selected)).pack(side=tk.LEFT, padx=2)
        tk.Button(tags_row, text="＋ 加标签", command=self._add_tags_to_selected,
            bg="#f0f0f0", relief="flat", padx=7, pady=3).pack(side=tk.RIGHT, padx=2)

        table = tk.Frame(list_panel, bg=self._bg)
        table.pack(fill=tk.BOTH, expand=True)
        columns = ("artist", "language", "tags", "location")
        self.song_tree = ttk.Treeview(table, columns=columns, show="tree headings", selectmode="extended")
        for column, heading in [("#0", "歌曲 / Song"), ("artist", "歌手 / Artist"),
                                ("language", "语言"), ("tags", "标签"), ("location", "位置")]:
            self.song_tree.heading(column, text=heading)
        for column, width, minimum, stretch in [
            ("#0", 210, 130, True), ("artist", 145, 90, True),
            ("language", 85, 70, False), ("tags", 135, 75, True), ("location", 80, 65, False)
        ]:
            self.song_tree.column(column, width=width, minwidth=minimum, stretch=stretch)
        yscroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.song_tree.yview)
        xscroll = ttk.Scrollbar(table, orient=tk.HORIZONTAL, command=self.song_tree.xview)
        self.song_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.song_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)
        self.song_tree.bind("<<TreeviewSelect>>", self._on_song_selected)

        tk.Label(detail_panel, text="歌曲资料 / SONG DETAILS", bg=self._bg, fg=colors["primary"],
            font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self._song_manager_field(detail_panel, "歌曲名称", self.song_title_var)
        self._song_manager_field(detail_panel, "歌手 / Artist", self.song_artist_var)
        language_row = tk.Frame(detail_panel, bg=self._bg)
        language_row.pack(fill=tk.X, pady=4)
        tk.Label(language_row, text="语言", width=11, anchor="w", bg=self._bg, fg=self._fg).pack(side=tk.LEFT)
        ttk.Combobox(language_row, textvariable=self.song_language_var,
            values=["Mandarin", "Cantonese", "Hokkien", "Malay", "English", "Japanese", "Korean", "Other"],
            state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._song_manager_field(detail_panel, "标签 (逗号分隔)", self.song_tags_var)
        tk.Label(detail_panel, text="整理目的地预览", bg=self._bg, fg="gray", font=("Arial", 8)).pack(anchor="w", pady=(9, 3))
        tk.Label(detail_panel, textvariable=self.song_path_preview_var, bg="white", fg=self._fg,
            anchor="w", justify=tk.LEFT, wraplength=260, padx=7, pady=7, relief="solid", bd=1).pack(fill=tk.X)
        actions = tk.Frame(detail_panel, bg=self._bg)
        actions.pack(fill=tk.X, pady=(10, 4))
        tk.Button(actions, text="储存资料", command=self._save_song_details,
            bg=colors["info"], fg="white", relief="flat", padx=8, pady=5).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        tk.Button(actions, text="整理所选歌曲", command=self.move_selected_songs,
            bg=colors["success"], fg="white", relief="flat", padx=8, pady=5).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))
        tk.Label(detail_panel, text="资料夹依语言 / 歌手首字分类。标签保存在本机曲库索引，不会改写影片。",
            bg=self._bg, fg="gray", justify=tk.LEFT, wraplength=260, font=("Arial", 8)).pack(anchor="w", pady=(7, 0))

        footer = tk.Frame(parent, bg=self._bg)
        footer.grid(row=4, column=0, sticky="ew", padx=8, pady=(3, 8))
        tk.Label(footer, textvariable=self.song_manager_count_var, bg=self._bg, fg="gray").pack(side=tk.LEFT)
        tk.Label(footer, text="MP4 / MKV / AVI / MOV / WMV / WEBM / MP3 / M4A / WAV / FLAC",
            bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.RIGHT)
        for variable in (self.song_title_var, self.song_artist_var, self.song_language_var):
            variable.trace_add("write", lambda *_args: self._update_song_destination_preview())
        self._load_song_catalog(silent=True)

    def _song_manager_field(self, parent, label, variable):
        row = tk.Frame(parent, bg=self._bg)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=label, width=11, anchor="w", bg=self._bg, fg=self._fg).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _song_catalog_path(self, library=None):
        library = Path(library or self.song_library_var.get()).expanduser()
        catalog_path = library / ".myktv_catalog.sqlite3"
        legacy_path = library / ".yttomkv_catalog.sqlite3"
        if legacy_path.exists() and not catalog_path.exists():
            try:
                legacy_path.replace(catalog_path)
            except OSError:
                return legacy_path
        return catalog_path

    def _choose_song_inbox(self):
        folder = filedialog.askdirectory(title="选择下载收件匣", initialdir=self.song_inbox_var.get() or str(self.app_dir))
        if folder:
            self.song_inbox_var.set(folder)
            self.config["song_inbox_dir"] = folder
            self.save_config()

    def _choose_song_library(self):
        folder = filedialog.askdirectory(title="选择整理后曲库", initialdir=self.song_library_var.get() or str(self.app_dir))
        if folder:
            self.song_library_var.set(folder)
            self.config["song_library_dir"] = folder
            self.save_config()
            self._load_song_catalog(silent=True)
            self._update_song_destination_preview()

    def _set_song_tag_filter(self, tag):
        self.song_tag_filter_var.set(tag)
        self._render_song_catalog()

    def _load_song_catalog(self, silent=False):
        db_path = self._song_catalog_path()
        if not db_path.exists():
            self._song_manager_records = []
            self._render_song_catalog()
            return
        try:
            with closing(sqlite3.connect(str(db_path), timeout=10)) as connection, connection:
                connection.row_factory = sqlite3.Row
                self._song_manager_records = [dict(row) for row in connection.execute(
                    "SELECT id, path, title, artist, language, tags, updated_at FROM songs ORDER BY title COLLATE NOCASE")]
            self._render_song_catalog()
        except Exception as e:
            if not silent:
                messagebox.showerror("曲库索引错误", f"无法读取歌曲索引：\n{e}")

    def _render_song_catalog(self):
        if not hasattr(self, "song_tree"):
            return
        query = self.song_search_var.get().strip().casefold()
        language_filter = self.song_language_filter_var.get()
        tag_filter = self.song_tag_filter_var.get().casefold()
        for item in self.song_tree.get_children():
            self.song_tree.delete(item)
        inbox_count = 0
        untagged_count = 0
        library_root = Path(self.song_library_var.get()).expanduser()
        for song in self._song_manager_records:
            path = Path(song["path"])
            tags = [tag.strip() for tag in (song.get("tags") or "").split(",") if tag.strip()]
            try:
                path.relative_to(library_root)
                location = "曲库"
            except (ValueError, OSError):
                location = "收件匣"
            inbox_count += location == "收件匣"
            untagged_count += not tags
            search_text = " ".join((song.get("title", ""), song.get("artist", ""), song.get("language", ""), " ".join(tags), path.name)).casefold()
            if query and query not in search_text:
                continue
            if language_filter != "全部语言" and song.get("language") != language_filter:
                continue
            if tag_filter and tag_filter not in {tag.casefold() for tag in tags}:
                continue
            self.song_tree.insert("", tk.END, iid=str(song["id"]), text=song.get("title") or path.stem,
                values=(song.get("artist") or "", song.get("language") or "Other", ", ".join(tags) or "未标记", location))
        self.song_manager_count_var.set(
            f"索引 {len(self._song_manager_records)} 首歌曲  ·  收件匣 {inbox_count}  ·  未标记 {untagged_count}  ·  显示 {len(self.song_tree.get_children())} 首")

    def _on_song_selected(self, _event=None):
        selection = self.song_tree.selection()
        if not selection:
            return
        self._song_manager_selected_id = int(selection[0])
        song = next((item for item in self._song_manager_records if item["id"] == self._song_manager_selected_id), None)
        if not song:
            return
        self.song_title_var.set(song.get("title") or "")
        self.song_artist_var.set(song.get("artist") or "")
        self.song_language_var.set(song.get("language") or "Other")
        self.song_tags_var.set(song.get("tags") or "")
        self._update_song_destination_preview()

    def _song_manager_target_path(self, song, library=None):
        library = Path(library or self.song_library_var.get()).expanduser()
        supported = {"Mandarin", "Cantonese", "Hokkien", "Malay", "English", "Japanese", "Korean", "Other"}
        language = song.get("language") or "Other"
        if language not in supported:
            language = "Other"
        artist = re.sub(r'[<>:"/\\|?*]', "_", (song.get("artist") or "Unknown Artist")).strip(" .") or "Unknown Artist"
        initial = artist[0].upper() if artist[0].isascii() and artist[0].isalnum() else "#"
        return library / language / initial / artist / Path(song["path"]).name

    def _update_song_destination_preview(self):
        if not hasattr(self, "song_path_preview_var"):
            return
        song = next((item for item in self._song_manager_records
            if item["id"] == self._song_manager_selected_id), None)
        if not song:
            self.song_path_preview_var.set("选择歌曲后显示整理目的地")
            return
        proposed = dict(song, artist=self.song_artist_var.get().strip(), language=self.song_language_var.get())
        self.song_path_preview_var.set(str(self._song_manager_target_path(proposed)))

    def _save_song_details(self):
        song = next((item for item in self._song_manager_records
            if item["id"] == self._song_manager_selected_id), None)
        if not song:
            messagebox.showinfo("歌曲管理", "请先选择一首歌曲。")
            return
        try:
            with closing(sqlite3.connect(str(self._song_catalog_path()), timeout=10)) as connection, connection:
                connection.execute("UPDATE songs SET title=?, artist=?, language=?, tags=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (self.song_title_var.get().strip() or Path(song["path"]).stem,
                     self.song_artist_var.get().strip(), self.song_language_var.get(),
                     self.song_tags_var.get().strip(), self._song_manager_selected_id))
            song.update(title=self.song_title_var.get().strip() or Path(song["path"]).stem,
                artist=self.song_artist_var.get().strip(), language=self.song_language_var.get(), tags=self.song_tags_var.get().strip())
            self._render_song_catalog()
            self.song_manager_status_var.set("歌曲资料已储存。")
        except Exception as e:
            messagebox.showerror("储存失败", f"无法储存歌曲资料：\n{e}")

    def _add_tags_to_selected(self):
        ids = {int(item) for item in self.song_tree.selection()}
        if not ids and self._song_manager_selected_id is not None:
            ids.add(self._song_manager_selected_id)
        if not ids:
            messagebox.showinfo("歌曲管理", "请先选择歌曲。")
            return
        value = simpledialog.askstring("新增标签", "输入标签，以逗号分隔：\n例如：Pop, Duet, Concert", parent=self.root)
        if not value:
            return
        additions = [tag.strip() for tag in value.split(",") if tag.strip()]
        try:
            with closing(sqlite3.connect(str(self._song_catalog_path()), timeout=10)) as connection, connection:
                for song in self._song_manager_records:
                    if song["id"] not in ids:
                        continue
                    tags = [tag.strip() for tag in (song.get("tags") or "").split(",") if tag.strip()]
                    seen = {tag.casefold() for tag in tags}
                    tags.extend(tag for tag in additions if tag.casefold() not in seen)
                    song["tags"] = ", ".join(tags)
                    connection.execute("UPDATE songs SET tags=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (song["tags"], song["id"]))
            self._render_song_catalog()
            self.song_manager_status_var.set(f"已为 {len(ids)} 首歌曲新增标签。")
        except Exception as e:
            messagebox.showerror("标签更新失败", f"无法储存标签：\n{e}")

    def scan_song_folders(self):
        inbox = Path(self.song_inbox_var.get()).expanduser()
        library = Path(self.song_library_var.get()).expanduser()
        if not inbox.is_dir():
            messagebox.showwarning("选择下载收件匣", "请先选择有效的下载收件匣资料夹。")
            return
        try:
            library.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("曲库资料夹错误", f"无法建立曲库资料夹：\n{e}")
            return
        if self._song_manager_scanning:
            return
        self._song_manager_scanning = True
        self.song_manager_status_var.set("正在背景扫描资料夹...")
        threading.Thread(target=self._scan_song_folders_worker, args=(inbox, library), daemon=True).start()

    def _scan_song_folders_worker(self, inbox, library):
        extensions = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".mp3", ".m4a", ".wav", ".flac"}
        scanned = set()
        count = 0
        try:
            db_path = self._song_catalog_path(library)
            with closing(sqlite3.connect(str(db_path), timeout=30)) as connection, connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'Other', tags TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_songs_title ON songs(title COLLATE NOCASE)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist COLLATE NOCASE)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_songs_language ON songs(language)")
                for folder in (inbox, library):
                    for path in folder.rglob("*"):
                        if path.suffix.lower() not in extensions or path.is_symlink():
                            continue
                        try:
                            if not path.is_file():
                                continue
                            resolved = str(path.resolve())
                            key = os.path.normcase(resolved)
                            if key in scanned:
                                continue
                            scanned.add(key)
                            parts = re.split(r"\s[-–—]\s", path.stem, maxsplit=1)
                            artist, title = (parts[0], parts[1]) if len(parts) == 2 else ("", path.stem)
                            connection.execute("INSERT OR IGNORE INTO songs(path, title, artist) VALUES (?, ?, ?)",
                                (resolved, title.strip(), artist.strip()))
                            count += 1
                        except (OSError, sqlite3.Error):
                            continue
            self.root.after(0, self._song_scan_completed, count, None)
        except Exception as e:
            self.root.after(0, self._song_scan_completed, count, str(e))

    def _song_scan_completed(self, count, error):
        self._song_manager_scanning = False
        if error:
            self.song_manager_status_var.set("扫描失败。")
            messagebox.showerror("扫描失败", f"扫描歌曲资料夹时发生错误：\n{error}")
            return
        self._load_song_catalog()
        self.song_manager_status_var.set(f"扫描完成，检查 {count} 个媒体档案。")

    def move_selected_songs(self):
        if self._song_manager_selected_id is None:
            messagebox.showinfo("歌曲管理", "请先选择要整理的歌曲。")
            return
        self._save_song_details()
        ids = {int(item) for item in self.song_tree.selection()} or {self._song_manager_selected_id}
        selected = [dict(song) for song in self._song_manager_records if song["id"] in ids]
        library = Path(self.song_library_var.get()).expanduser()
        if not messagebox.askyesno("确认整理", f"将 {len(selected)} 个档案移至曲库资料夹？\n\n{library}\n\n不会覆写同名档案。"):
            return
        self.song_manager_status_var.set("正在整理档案...")
        threading.Thread(target=self._move_song_files_worker, args=(selected, library), daemon=True).start()

    def _move_song_files_worker(self, selected, library):
        moved = 0
        errors = []
        db_path = self._song_catalog_path(library)
        for song in selected:
            source = Path(song["path"])
            target = self._song_manager_target_path(song, library=library)
            try:
                if not source.is_file():
                    errors.append(f"找不到档案：{source}")
                    continue
                if source.resolve() == target.resolve():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                candidate = target
                suffix = 2
                while candidate.exists():
                    candidate = target.with_name(f"{target.stem} ({suffix}){target.suffix}")
                    suffix += 1
                shutil.move(str(source), str(candidate))
                with closing(sqlite3.connect(str(db_path), timeout=30)) as connection, connection:
                    connection.execute("UPDATE songs SET path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(candidate.resolve()), song["id"]))
                moved += 1
            except Exception as e:
                errors.append(f"{source.name}: {e}")
        self.root.after(0, self._song_move_completed, moved, errors)

    def _song_move_completed(self, moved, errors):
        self._load_song_catalog()
        if errors:
            self.song_manager_status_var.set(f"整理完成：移动 {moved} 个，{len(errors)} 个失败。")
            messagebox.showwarning("部分整理失败", "\n".join(errors[:8]))
        else:
            self.song_manager_status_var.set(f"整理完成：已移动 {moved} 个档案。")
