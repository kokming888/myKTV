import subprocess
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from tkinter import messagebox, ttk


import hashlib
import os
import random
import subprocess
import time
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw, ImageFilter, ImageTk


class KtvSelectionMixin:
    """Build the professional KTV song-selection and multimedia playback station."""

    def build_ktv_selection_tab(self, parent):
        colors = {
            "bg": "#0a0015",
            "surface": "#140026",
            "surface_alt": "#220040",
            "input": "#0c0019",
            "line": "#4a0072",
            "text": "#ffffff",
            "muted": "#aa99cc",
            "gold": "#ffd700",
            "mint": "#00ffff",
            "coral": "#ff007f",
            "selected": "#7928ca",
            "missing": "#a370f7",
            "accent": "#00f0ff",
            "success": "#00ff88",
        }
        self.ktv_colors = colors
        self.ktv_parent = parent
        parent.configure(bg=colors["bg"])

        # Cache directories for artwork & video frames
        self.ktv_cache_dir = self.app_dir / "cache" / "ktv_artwork"
        with suppress(Exception):
            self.ktv_cache_dir.mkdir(parents=True, exist_ok=True)

        # Variables
        self.ktv_search_var = tk.StringVar()
        self.ktv_language_filter_var = tk.StringVar(value="全部语言")
        self.ktv_tag_filter_var = tk.StringVar(value="")
        self.ktv_now_playing_var = tk.StringVar(value="等待点歌播放 / Ready to Sing")
        self.ktv_artist_var = tk.StringVar(value="— 歌手 / Artist —")
        self.ktv_playback_status_var = tk.StringVar(value="请从曲库点歌并加入播放伫列。")
        self.ktv_selection_status_var = tk.StringVar(value="双击歌曲即可点播或加入伫列")
        self.ktv_library_count_var = tk.StringVar(value="0 首")
        self.ktv_duration_var = tk.StringVar(value="00:00 / 00:00")
        self.ktv_track_mode_var = tk.StringVar(value="🎤 原唱/伴奏: 声道1")
        self.ktv_popup_video_var = tk.BooleanVar(value=False)
        self.ktv_queue_count_var = tk.StringVar(value="0 首歌曲")

        # Playback states
        self.ktv_queue = []
        self.ktv_queue_playing = False
        self.ktv_queue_index = 0
        self.ktv_preview_process = None
        self.ktv_selected_song_id = None
        self.ktv_current_song = None
        self.ktv_playback_start_time = 0
        self.ktv_estimated_duration = 240
        self.ktv_audio_channel_index = 0
        self.ktv_visualizer_bars = [10] * 24
        self.ktv_artwork_photo = None
        self.ktv_video_bg_photo = None
        self._visualizer_timer_id = None

        languages = ["全部语言", "Mandarin", "Cantonese", "Hokkien", "Malay", "English", "Japanese", "Korean", "Other"]

        # Treeview Styles
        style = ttk.Style(parent)
        style.configure("KTV.Treeview", background=colors["input"], foreground=colors["text"],
                        fieldbackground=colors["input"], rowheight=34, borderwidth=0, font=("Arial", 9))
        style.configure("KTV.Treeview.Heading", background=colors["surface_alt"], foreground=colors["mint"],
                        relief="flat", borderwidth=0, font=("Arial", 9, "bold"), padding=(6, 8))
        style.map("KTV.Treeview", background=[("selected", colors["selected"])],
                  foreground=[("selected", colors["text"])])

        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        # ----------------------------- TOP HEADER -----------------------------
        header = tk.Frame(parent, bg=colors["surface"], padx=16, pady=10,
                          highlightthickness=1, highlightbackground=colors["coral"])
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 6))

        brand_left = tk.Frame(header, bg=colors["surface"])
        brand_left.pack(side=tk.LEFT)
        brand_mark = tk.Label(brand_left, text="♫", bg=colors["coral"], fg=colors["text"],
                              width=3, height=1, font=("Arial", 16, "bold"))
        brand_mark.pack(side=tk.LEFT, padx=(0, 10))
        title_group = tk.Frame(brand_left, bg=colors["surface"])
        title_group.pack(side=tk.LEFT)
        tk.Label(title_group, text="MYKTV STUDIO & PLAYER", bg=colors["surface"], fg=colors["text"],
                 font=("Arial", 15, "bold")).pack(anchor="w")
        tk.Label(title_group, text="KTV 智能点歌台 · 视频视窗与专辑写真", bg=colors["surface"],
                 fg=colors["mint"], font=("Arial", 9, "bold")).pack(anchor="w")

        # Header Search & Library Counter
        header_right = tk.Frame(header, bg=colors["surface"])
        header_right.pack(side=tk.RIGHT)

        search_box = tk.Frame(header_right, bg=colors["input"], padx=8, pady=4,
                              highlightthickness=1, highlightbackground=colors["mint"])
        search_box.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(search_box, text="🔍", bg=colors["input"], fg=colors["gold"], font=("Arial", 11)).pack(side=tk.LEFT, padx=(0, 6))
        search_entry = tk.Entry(search_box, textvariable=self.ktv_search_var, relief="flat", bd=0,
                                bg=colors["input"], fg=colors["text"], insertbackground=colors["mint"],
                                highlightthickness=0, font=("Arial", 10), width=22)
        search_entry.pack(side=tk.LEFT)
        search_entry.bind("<KeyRelease>", lambda _event: self._render_ktv_library())

        tk.Button(header_right, text="↻ 重新整理", command=self._refresh_ktv_library,
                  bg=colors["surface_alt"], fg=colors["text"], activebackground=colors["selected"],
                  activeforeground=colors["text"], relief="flat", padx=10, pady=5, cursor="hand2",
                  font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))

        library_badge = tk.Frame(header_right, bg=colors["selected"], padx=10, pady=4)
        library_badge.pack(side=tk.LEFT)
        tk.Label(library_badge, text="📚 曲库", bg=colors["selected"], fg=colors["text"],
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(library_badge, textvariable=self.ktv_library_count_var, bg=colors["selected"],
                 fg=colors["gold"], font=("Arial", 11, "bold")).pack(side=tk.LEFT)

        # ----------------------------- MAIN SPLIT AREA -----------------------------
        main_split = tk.PanedWindow(parent, orient=tk.HORIZONTAL, sashwidth=6, bg=colors["bg"], bd=0, relief="flat")
        main_split.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 6))

        # LEFT PANEL: Video Player Screen & Artwork & Controls
        left_player_panel = tk.Frame(main_split, bg=colors["surface"], padx=12, pady=10,
                                     highlightthickness=1, highlightbackground=colors["mint"])
        # RIGHT PANEL: Song Selection & Queue
        right_song_panel = tk.Frame(main_split, bg=colors["surface"], padx=12, pady=10,
                                    highlightthickness=1, highlightbackground=colors["gold"])

        main_split.add(left_player_panel, minsize=540, stretch="always")
        main_split.add(right_song_panel, minsize=520, stretch="always")

        # ====================================================================
        # LEFT PANEL CONTENTS: 1. Video Canvas, 2. Artwork & Metadata, 3. Controls
        # ====================================================================

        # 1. Video Canvas Frame (16:9 Widescreen Box)
        video_frame_container = tk.Frame(left_player_panel, bg=colors["input"],
                                         highlightthickness=2, highlightbackground=colors["coral"])
        video_frame_container.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.ktv_video_canvas = tk.Canvas(video_frame_container, bg="#05000c",
                                          highlightthickness=0, height=270)
        self.ktv_video_canvas.pack(fill=tk.BOTH, expand=True)
        self.ktv_video_canvas.bind("<Configure>", lambda _e: self._draw_video_canvas_screen(self.ktv_current_song))

        # 2. Album Artwork + Song Information Card
        media_card = tk.Frame(left_player_panel, bg=colors["surface_alt"], padx=10, pady=8,
                              highlightthickness=1, highlightbackground=colors["line"])
        media_card.pack(fill=tk.X, pady=(0, 10))

        # Artwork Image display
        self.ktv_artwork_label = tk.Label(media_card, bg="#05000c", relief="solid", bd=1,
                                         highlightthickness=1, highlightbackground=colors["mint"])
        self.ktv_artwork_label.pack(side=tk.LEFT, padx=(0, 12))
        self._set_default_artwork()

        # Metadata info column
        meta_info = tk.Frame(media_card, bg=colors["surface_alt"])
        meta_info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(meta_info, textvariable=self.ktv_now_playing_var, bg=colors["surface_alt"],
                 fg=colors["gold"], font=("Arial", 13, "bold"), anchor="w", wraplength=320, justify=tk.LEFT).pack(anchor="w", pady=(0, 2))
        tk.Label(meta_info, textvariable=self.ktv_artist_var, bg=colors["surface_alt"],
                 fg=colors["mint"], font=("Arial", 10, "bold"), anchor="w").pack(anchor="w", pady=(0, 4))

        status_row = tk.Frame(meta_info, bg=colors["surface_alt"])
        status_row.pack(fill=tk.X, pady=(0, 2))
        tk.Label(status_row, textvariable=self.ktv_playback_status_var, bg=colors["surface_alt"],
                 fg=colors["text"], font=("Arial", 9), anchor="w").pack(side=tk.LEFT)

        time_row = tk.Frame(meta_info, bg=colors["surface_alt"])
        time_row.pack(fill=tk.X, pady=(2, 0))
        tk.Label(time_row, text="⏱ 播放时间: ", bg=colors["surface_alt"], fg=colors["muted"],
                 font=("Arial", 8)).pack(side=tk.LEFT)
        tk.Label(time_row, textvariable=self.ktv_duration_var, bg=colors["surface_alt"],
                 fg=colors["gold"], font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 12))

        track_btn = tk.Button(time_row, textvariable=self.ktv_track_mode_var, command=self._switch_ktv_audio_track,
                              bg=colors["input"], fg=colors["mint"], activebackground=colors["selected"],
                              activeforeground=colors["text"], relief="flat", padx=6, pady=2, cursor="hand2",
                              font=("Arial", 8, "bold"))
        track_btn.pack(side=tk.LEFT)

        # 3. Transport Controls Bar
        control_bar = tk.Frame(left_player_panel, bg=colors["surface"], padx=4, pady=4)
        control_bar.pack(fill=tk.X)

        btn_style = {"relief": "flat", "cursor": "hand2", "font": ("Arial", 9, "bold"), "pady": 7}

        tk.Button(control_bar, text="⏮ 上一首", command=self._play_previous_ktv_track,
                  bg=colors["surface_alt"], fg=colors["text"], activebackground=colors["selected"],
                  activeforeground=colors["text"], **btn_style).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        tk.Button(control_bar, text="▶ 播放/切歌", command=self._play_ktv_selected_song,
                  bg=colors["coral"], fg="white", activebackground="#ff3399",
                  activeforeground="white", **btn_style).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        tk.Button(control_bar, text="🔁 重唱", command=self._replay_current_ktv_track,
                  bg=colors["surface_alt"], fg=colors["gold"], activebackground=colors["selected"],
                  activeforeground=colors["gold"], **btn_style).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        tk.Button(control_bar, text="⏭ 下一首", command=self._play_next_ktv_queue_track,
                  bg=colors["surface_alt"], fg=colors["text"], activebackground=colors["selected"],
                  activeforeground=colors["text"], **btn_style).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        tk.Button(control_bar, text="⏹ 停止", command=self._stop_ktv_playback,
                  bg="#3d0014", fg="#ff4d4d", activebackground="#660022",
                  activeforeground="white", **btn_style).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        popup_check = tk.Checkbutton(control_bar, text="📺 弹出独立视窗", variable=self.ktv_popup_video_var,
                                     bg=colors["surface"], fg=colors["mint"], selectcolor=colors["input"],
                                     activebackground=colors["surface"], activeforeground=colors["mint"],
                                     font=("Arial", 8))
        popup_check.pack(side=tk.LEFT, padx=(8, 2))

        # ====================================================================
        # RIGHT PANEL CONTENTS: Song Filter Tabs, Song Treeview, Queue List
        # ====================================================================

        # Top Filter Chips: Languages
        lang_bar = tk.Frame(right_song_panel, bg=colors["surface"])
        lang_bar.pack(fill=tk.X, pady=(0, 6))

        self.ktv_language_buttons = {}
        self.ktv_language_count_vars = {}
        for language in languages:
            count_var = tk.StringVar(value="0")
            self.ktv_language_count_vars[language] = count_var
            label = "全部" if language == "全部语言" else language
            button = tk.Button(lang_bar, text=label, relief="flat", padx=8, pady=4,
                               bg=colors["selected"] if language == "全部语言" else colors["surface_alt"],
                               fg=colors["text"] if language == "全部语言" else colors["muted"],
                               activebackground=colors["selected"], activeforeground=colors["text"],
                               command=lambda value=language: self._set_ktv_language_filter(value),
                               font=("Arial", 8, "bold"), cursor="hand2")
            button.pack(side=tk.LEFT, padx=(0, 4))
            self.ktv_language_buttons[language] = button

        # Tag Quick Filters
        tag_bar = tk.Frame(right_song_panel, bg=colors["surface"])
        tag_bar.pack(fill=tk.X, pady=(0, 8))
        self.ktv_tag_buttons = {}
        for tag in ["全部", "Pop", "Band", "Duet", "Concert", "Ballad"]:
            value = "" if tag == "全部" else tag
            btn = tk.Button(tag_bar, text=tag, relief="flat", padx=8, pady=3,
                            bg=colors["selected"] if not value else colors["input"],
                            fg=colors["text"] if not value else colors["muted"],
                            activebackground=colors["selected"], activeforeground=colors["text"],
                            command=lambda sel=value: self._set_ktv_tag_filter(sel),
                            font=("Arial", 8), cursor="hand2")
            btn.pack(side=tk.LEFT, padx=(0, 4))
            self.ktv_tag_buttons[value] = btn

        # Song List Treeview (Split with Up Next Queue)
        right_split = tk.PanedWindow(right_song_panel, orient=tk.VERTICAL, sashwidth=5, bg=colors["bg"], bd=0)
        right_split.pack(fill=tk.BOTH, expand=True)

        song_list_frame = tk.Frame(right_split, bg=colors["surface"])
        queue_list_frame = tk.Frame(right_split, bg=colors["surface"], padx=2, pady=4)
        right_split.add(song_list_frame, minsize=200, stretch="always")
        right_split.add(queue_list_frame, minsize=140, stretch="never")

        # Treeview in Song List Frame
        tree_container = tk.Frame(song_list_frame, bg=colors["input"])
        tree_container.pack(fill=tk.BOTH, expand=True)

        columns = ("artist", "language", "tags", "availability")
        self.ktv_song_tree = ttk.Treeview(tree_container, columns=columns, show="tree headings",
                                          selectmode="extended", style="KTV.Treeview")
        for column, heading in [("#0", "歌曲 / SONG"), ("artist", "歌手 / ARTIST"),
                                ("language", "语言"), ("tags", "标签"), ("availability", "状态")]:
            self.ktv_song_tree.heading(column, text=heading)
        for column, width, min_w, stretch in [
            ("#0", 220, 130, True), ("artist", 140, 80, True),
            ("language", 80, 60, False), ("tags", 110, 60, True), ("availability", 70, 55, False)
        ]:
            self.ktv_song_tree.column(column, width=width, minwidth=min_w, stretch=stretch)

        yscroll = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.ktv_song_tree.yview)
        self.ktv_song_tree.configure(yscrollcommand=yscroll.set)
        self.ktv_song_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.ktv_song_tree.bind("<<TreeviewSelect>>", self._on_ktv_song_selected)
        self.ktv_song_tree.bind("<Double-1>", lambda _e: self._add_ktv_selection_to_queue(auto_play=True))

        # Queue List Frame
        queue_header = tk.Frame(queue_list_frame, bg=colors["surface"])
        queue_header.pack(fill=tk.X, pady=(0, 4))
        tk.Label(queue_header, text="📋 播放伫列 / UP NEXT", bg=colors["surface"], fg=colors["coral"],
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        tk.Label(queue_header, textvariable=self.ktv_queue_count_var, bg=colors["surface"],
                 fg=colors["mint"], font=("Arial", 8)).pack(side=tk.LEFT, padx=(8, 0))

        # Queue Action buttons
        tk.Button(queue_header, text="🗑️ 清空", command=self._clear_ktv_queue,
                  bg=colors["surface"], fg=colors["muted"], activebackground=colors["surface_alt"],
                  activeforeground=colors["text"], relief="flat", padx=6, pady=1, cursor="hand2",
                  font=("Arial", 8)).pack(side=tk.RIGHT)
        tk.Button(queue_header, text="✕ 移除", command=self._remove_ktv_queue_selection,
                  bg=colors["surface_alt"], fg=colors["coral"], activebackground=colors["selected"],
                  activeforeground=colors["text"], relief="flat", padx=6, pady=1, cursor="hand2",
                  font=("Arial", 8)).pack(side=tk.RIGHT, padx=4)
        tk.Button(queue_header, text="✚ 点歌", command=lambda: self._add_ktv_selection_to_queue(auto_play=False),
                  bg=colors["mint"], fg=colors["bg"], activebackground="#55ffff",
                  activeforeground=colors["bg"], relief="flat", padx=8, pady=1, cursor="hand2",
                  font=("Arial", 8, "bold")).pack(side=tk.RIGHT, padx=4)

        self.ktv_queue_listbox = tk.Listbox(queue_list_frame, height=5, selectmode=tk.EXTENDED,
                                            activestyle="none", relief="flat", bd=0, highlightthickness=0,
                                            bg=colors["input"], fg=colors["text"],
                                            selectbackground=colors["selected"], selectforeground=colors["text"],
                                            font=("Arial", 9))
        self.ktv_queue_listbox.pack(fill=tk.BOTH, expand=True)
        self.ktv_queue_listbox.bind("<Double-1>", lambda _e: self._play_queue_item_direct())

        # ----------------------------- FOOTER STATUS -----------------------------
        footer = tk.Frame(parent, bg=colors["surface"], padx=12, pady=6,
                          highlightthickness=1, highlightbackground=colors["selected"])
        footer.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        tk.Label(footer, text="♫", bg=colors["surface"], fg=colors["gold"], font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(footer, textvariable=self.ktv_selection_status_var, bg=colors["surface"], fg=colors["mint"],
                 font=("Arial", 9)).pack(side=tk.LEFT)

        self._draw_video_canvas_screen()
        self._render_ktv_library()

    # =========================================================================
    # ARTWORK & VIDEO CANVAS DRAWING CAPABILITIES
    # =========================================================================

    def _set_default_artwork(self):
        """Create and display a default stylized neon vinyl artwork."""
        size = (110, 110)
        img = Image.new("RGB", size, color="#120024")
        draw = ImageDraw.Draw(img)

        # Draw concentric vinyl grooves
        draw.ellipse([4, 4, 106, 106], outline="#ff007f", width=2)
        draw.ellipse([16, 16, 94, 94], outline="#2d0059", width=1)
        draw.ellipse([28, 28, 82, 82], outline="#3d0079", width=1)
        draw.ellipse([40, 40, 70, 70], fill="#00ffff", outline="#ffd700", width=2)
        draw.ellipse([50, 50, 60, 60], fill="#0a0015")

        photo = ImageTk.PhotoImage(img)
        self.ktv_artwork_photo = photo
        self.ktv_artwork_label.configure(image=photo)

    def _extract_and_cache_artwork(self, song):
        """Extract album cover or video frame snapshot and cache as image."""
        if not song or "path" not in song:
            return None

        media_path = Path(song["path"])
        if not media_path.is_file():
            return None

        # Check existing sidecar cover image (e.g. song.jpg, cover.jpg)
        for ext in [".jpg", ".png", ".jpeg", ".webp"]:
            sidecar = media_path.with_suffix(ext)
            if sidecar.is_file():
                try:
                    return Image.open(sidecar)
                except Exception:
                    pass
            parent_cover = media_path.parent / f"cover{ext}"
            if parent_cover.is_file():
                try:
                    return Image.open(parent_cover)
                except Exception:
                    pass

        # Hash-based cached frame extraction
        h = hashlib.md5(str(media_path).encode("utf-8")).hexdigest()[:12]
        cached_thumb = self.ktv_cache_dir / f"thumb_{h}.jpg"

        if cached_thumb.is_file() and cached_thumb.stat().st_size > 0:
            try:
                return Image.open(cached_thumb)
            except Exception:
                pass

        # Extract snapshot at 00:00:05 via ffmpeg
        ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
        if ffmpeg_exe.is_file():
            try:
                cmd = [
                    str(ffmpeg_exe), "-y", "-ss", "00:00:04",
                    "-i", str(media_path),
                    "-vframes", "1", "-vf", "scale=480:-1",
                    "-q:v", "3", str(cached_thumb)
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=self.subp_flags, timeout=8)
                if cached_thumb.is_file() and cached_thumb.stat().st_size > 0:
                    return Image.open(cached_thumb)
            except Exception:
                pass

        return None

    def _update_artwork_preview(self, song):
        """Update the artwork picture box and background video screen."""
        if not hasattr(self, "ktv_artwork_label"):
            return

        raw_img = self._extract_and_cache_artwork(song)
        if raw_img:
            try:
                # Resize for thumbnail card (110x110)
                card_img = raw_img.copy()
                card_img.thumbnail((110, 110), Image.Resampling.LANCZOS)
                # Ensure 110x110 square with background
                square = Image.new("RGB", (110, 110), color="#05000c")
                ox = (110 - card_img.width) // 2
                oy = (110 - card_img.height) // 2
                square.paste(card_img, (ox, oy))

                photo = ImageTk.PhotoImage(square)
                self.ktv_artwork_photo = photo
                self.ktv_artwork_label.configure(image=photo)
            except Exception:
                self._set_default_artwork()
        else:
            self._set_default_artwork()

        self._draw_video_canvas_screen(song)

    def _draw_video_canvas_screen(self, song=None):
        """Render the 16:9 Video Playing Screen with artwork background and dynamic visuals."""
        if not hasattr(self, "ktv_video_canvas"):
            return

        canvas = self.ktv_video_canvas
        canvas.update_idletasks()
        w = max(canvas.winfo_width(), 460)
        h = max(canvas.winfo_height(), 240)

        canvas.delete("all")

        # 1. Background gradient or video frame snapshot
        canvas.create_rectangle(0, 0, w, h, fill="#070012", outline="")

        raw_img = self._extract_and_cache_artwork(song) if song else None
        if raw_img:
            try:
                bg_img = raw_img.copy().resize((w, h), Image.Resampling.BILINEAR)
                # Darken slightly for readability of overlay text
                bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=1.5))
                enhancer = ImageDraw.Draw(bg_img, "RGBA")
                enhancer.rectangle([0, 0, w, h], fill=(10, 0, 25, 140))

                self.ktv_video_bg_photo = ImageTk.PhotoImage(bg_img)
                canvas.create_image(w // 2, h // 2, image=self.ktv_video_bg_photo)
            except Exception:
                pass

        # 2. Glowing Neon Border around Video Screen
        canvas.create_rectangle(4, 4, w - 4, h - 4, outline="#ff007f", width=2)
        canvas.create_rectangle(6, 6, w - 6, h - 6, outline="#00ffff", width=1)

        # 3. Top Status Badges
        if self.ktv_queue_playing:
            canvas.create_rectangle(14, 12, 115, 34, fill="#ff0055", outline="#ffd700", width=1)
            canvas.create_text(64, 23, text="● LIVE PLAYING", fill="#ffffff", font=("Arial", 8, "bold"))
        else:
            canvas.create_rectangle(14, 12, 115, 34, fill="#220040", outline="#00ffff", width=1)
            canvas.create_text(64, 23, text="♫ MYKTV READY", fill="#00ffff", font=("Arial", 8, "bold"))

        canvas.create_rectangle(w - 110, 12, w - 14, 34, fill="#120024", outline="#ff007f", width=1)
        canvas.create_text(w - 62, 23, text="1080P KTV", fill="#ffd700", font=("Arial", 8, "bold"))

        # 4. Center Video Information / Karaoke Stage Display
        title = song.get("title", "") if song else ""
        artist = song.get("artist", "") if song else ""

        if not title:
            canvas.create_text(w // 2, h // 2 - 20, text="🎤 MYKTV VIDEO STAGE",
                               fill="#00ffff", font=("Arial", 16, "bold"))
            canvas.create_text(w // 2, h // 2 + 15, text="请在右侧选歌，双击加入播放伫列",
                               fill="#aa99cc", font=("Arial", 11))
        else:
            canvas.create_text(w // 2, h // 2 - 25, text=f"♫ {title}",
                               fill="#ffffff", font=("Arial", 16, "bold"))
            canvas.create_text(w // 2, h // 2 + 8, text=f"🎤 演唱: {artist or 'Unknown Artist'}",
                               fill="#ffd700", font=("Arial", 12, "bold"))

        # 5. Animated Visualizer Bars (Equalizer effect at bottom)
        bar_count = len(self.ktv_visualizer_bars)
        total_bar_width = min(w - 60, 360)
        bar_w = total_bar_width / bar_count
        start_x = (w - total_bar_width) / 2
        base_y = h - 35

        for i, height in enumerate(self.ktv_visualizer_bars):
            bx = start_x + i * bar_w
            canvas.create_rectangle(bx + 1, base_y - height, bx + bar_w - 2, base_y,
                                    fill="#00ffff" if i % 2 == 0 else "#ff007f", outline="")

        # 6. Bottom Progress Bar & Track Info
        canvas.create_line(20, h - 22, w - 20, h - 22, fill="#330066", width=4)
        if self.ktv_queue_playing and self.ktv_playback_start_time > 0:
            elapsed = time.time() - self.ktv_playback_start_time
            progress_ratio = min(1.0, elapsed / max(1, self.ktv_estimated_duration))
            pw = int((w - 40) * progress_ratio)
            if pw > 0:
                canvas.create_line(20, h - 22, 20 + pw, h - 22, fill="#00ffff", width=4)

    def _animate_visualizer(self):
        """Randomly update visualizer bars during playback for a live equalizer effect."""
        if not self.ktv_queue_playing:
            self.ktv_visualizer_bars = [6] * 24
            self._draw_video_canvas_screen(self.ktv_current_song)
            return

        # Generate realistic dance bars
        self.ktv_visualizer_bars = [random.randint(6, 38) for _ in range(24)]

        # Update playback timer
        if self.ktv_playback_start_time > 0:
            elapsed_sec = int(time.time() - self.ktv_playback_start_time)
            total_sec = int(self.ktv_estimated_duration)
            e_min, e_sec = divmod(elapsed_sec, 60)
            t_min, t_sec = divmod(total_sec, 60)
            self.ktv_duration_var.set(f"{e_min:02d}:{e_sec:02d} / {t_min:02d}:{t_sec:02d}")

        self._draw_video_canvas_screen(self.ktv_current_song)
        self._visualizer_timer_id = self.root.after(150, self._animate_visualizer)

    # =========================================================================
    # FILTER & LIBRARY MANAGEMENT
    # =========================================================================

    def _set_ktv_language_filter(self, language):
        self.ktv_language_filter_var.set(language)
        for value, button in self.ktv_language_buttons.items():
            active = (value == language)
            button.configure(
                bg=self.ktv_colors["selected"] if active else self.ktv_colors["surface_alt"],
                fg=self.ktv_colors["text"] if active else self.ktv_colors["muted"]
            )
        self._render_ktv_library()

    def _set_ktv_tag_filter(self, tag):
        self.ktv_tag_filter_var.set(tag)
        for value, button in self.ktv_tag_buttons.items():
            active = (value == tag)
            button.configure(
                bg=self.ktv_colors["selected"] if active else self.ktv_colors["input"],
                fg=self.ktv_colors["text"] if active else self.ktv_colors["muted"]
            )
        self._render_ktv_library()

    def _refresh_ktv_library(self):
        self._load_song_catalog(silent=False)
        self._render_ktv_library()

    def _render_ktv_library(self):
        if not hasattr(self, "ktv_song_tree"):
            return
        query = self.ktv_search_var.get().strip().casefold()
        language = self.ktv_language_filter_var.get()
        tag_filter = self.ktv_tag_filter_var.get().casefold()

        for item in self.ktv_song_tree.get_children():
            self.ktv_song_tree.delete(item)

        language_counts = {lang: 0 for lang in self.ktv_language_count_vars}
        tag_counts = {tag: 0 for tag in self.ktv_tag_buttons}

        for song in self._song_manager_records:
            path = Path(song["path"])
            tags = [t.strip() for t in (song.get("tags") or "").split(",") if t.strip()]
            artist = song.get("artist") or ""
            song_lang = song.get("language") or "Other"

            language_counts["全部语言"] += 1
            language_counts[song_lang] = language_counts.get(song_lang, 0) + 1

            tag_counts[""] += 1
            for song_tag in tags:
                for k in tag_counts:
                    if k and k.casefold() == song_tag.casefold():
                        tag_counts[k] += 1

            if language != "全部语言" and song_lang != language:
                continue
            if tag_filter and tag_filter not in {t.casefold() for t in tags}:
                continue

            search_text = " ".join((song.get("title", ""), artist, song_lang, " ".join(tags))).casefold()
            if query and query not in search_text:
                continue

            available = path.is_file()
            self.ktv_song_tree.insert(
                "", tk.END, iid=str(song["id"]),
                text=song.get("title") or path.stem,
                values=(artist, song_lang, ", ".join(tags) or "—", "可播放" if available else "遗失"),
                tags=("missing",) if not available else ()
            )

        for lang, count in language_counts.items():
            self.ktv_language_count_vars[lang].set(str(count))
            label = "全部" if lang == "全部语言" else lang
            self.ktv_language_buttons[lang].configure(text=f"{label} ({count})")

        for tag, count in tag_counts.items():
            label = "全部" if not tag else tag
            self.ktv_tag_buttons[tag].configure(text=f"{label} ({count})")

        self.ktv_song_tree.tag_configure("missing", foreground=self.ktv_colors["missing"])
        shown = len(self.ktv_song_tree.get_children())
        self.ktv_library_count_var.set(f"{len(self._song_manager_records)} 首")
        self.ktv_selection_status_var.set(f"曲库共 {len(self._song_manager_records)} 首 · 搜寻结果 {shown} 首 · 双击点歌")

    def _on_ktv_song_selected(self, _event=None):
        selection = self.ktv_song_tree.selection()
        if not selection:
            return
        self.ktv_selected_song_id = int(selection[0])
        song = next((r for r in self._song_manager_records if r["id"] == self.ktv_selected_song_id), None)
        if song:
            self._update_artwork_preview(song)
            self.ktv_selection_status_var.set(f"已选择：{song.get('title') or Path(song['path']).stem} · {song.get('artist') or 'Unknown Artist'}")

    # =========================================================================
    # QUEUE MANAGEMENT & PLAYBACK
    # =========================================================================

    def _add_ktv_selection_to_queue(self, auto_play=False):
        selected_ids = [int(item) for item in self.ktv_song_tree.selection()]
        if not selected_ids and self.ktv_selected_song_id is not None:
            selected_ids = [self.ktv_selected_song_id]

        if not selected_ids:
            messagebox.showinfo("KTV 点歌", "请先从曲库列表中选择一首歌曲。")
            return

        added = 0
        queued_ids = {song["id"] for song in self.ktv_queue}
        first_added = None

        for song in self._song_manager_records:
            if song["id"] in selected_ids and song["id"] not in queued_ids:
                if Path(song["path"]).is_file():
                    self.ktv_queue.append(dict(song))
                    queued_ids.add(song["id"])
                    added += 1
                    if first_added is None:
                        first_added = song

        self._render_ktv_queue()

        if auto_play and not self.ktv_queue_playing and first_added:
            self._stop_ktv_playback()
            self.ktv_queue_playing = True
            self.ktv_queue_index = len(self.ktv_queue) - added
            self._launch_ktv_track(first_added)
        else:
            self.ktv_playback_status_var.set(f"已加入 {added} 首歌曲到播放伫列。" if added else "所选歌曲已在伫列中。")

    def _render_ktv_queue(self):
        self.ktv_queue_listbox.delete(0, tk.END)
        for idx, song in enumerate(self.ktv_queue, start=1):
            title = song.get("title") or Path(song["path"]).stem
            artist = song.get("artist") or "Unknown Artist"
            prefix = "▶ " if (self.ktv_queue_playing and self.ktv_current_song and self.ktv_current_song.get("id") == song.get("id")) else f"{idx:02d}. "
            self.ktv_queue_listbox.insert(tk.END, f"{prefix}{title}  -  {artist}")
        self.ktv_queue_count_var.set(f"{len(self.ktv_queue)} 首歌曲")

    def _remove_ktv_queue_selection(self):
        indices = self.ktv_queue_listbox.curselection()
        if not indices:
            return
        for idx in reversed(indices):
            self.ktv_queue.pop(idx)
        self._render_ktv_queue()

    def _clear_ktv_queue(self):
        self._stop_ktv_playback()
        self.ktv_queue.clear()
        self._render_ktv_queue()

    def _play_queue_item_direct(self):
        indices = self.ktv_queue_listbox.curselection()
        if not indices:
            return
        idx = indices[0]
        if idx < len(self.ktv_queue):
            self._stop_ktv_playback()
            self.ktv_queue_playing = True
            self.ktv_queue_index = idx
            song = self.ktv_queue[idx]
            self._launch_ktv_track(song)

    def _play_ktv_selected_song(self):
        # If songs are in queue, play from queue; otherwise play selected song
        if self.ktv_selected_song_id is not None:
            song = next((r for r in self._song_manager_records if r["id"] == self.ktv_selected_song_id), None)
            if song:
                if song not in self.ktv_queue:
                    self.ktv_queue.insert(0, dict(song))
                    self._render_ktv_queue()
                self._stop_ktv_playback()
                self.ktv_queue_playing = True
                self.ktv_queue_index = 0
                self._launch_ktv_track(song)
                return

        if self.ktv_queue:
            self._play_ktv_queue()
        else:
            messagebox.showinfo("KTV 点歌", "请先选择或点播一首歌曲。")

    def _play_ktv_queue(self):
        if not self.ktv_queue:
            messagebox.showinfo("KTV 播放伫列", "播放伫列为空，请先点歌。")
            return
        self._stop_ktv_playback()
        self.ktv_queue_playing = True
        self.ktv_queue_index = 0
        self._play_next_ktv_queue_track()

    def _play_next_ktv_queue_track(self):
        if not self.ktv_queue:
            self._stop_ktv_playback()
            return

        while self.ktv_queue_playing and self.ktv_queue_index < len(self.ktv_queue):
            song = self.ktv_queue[self.ktv_queue_index]
            self.ktv_queue_index += 1
            if Path(song["path"]).is_file():
                self._stop_ktv_playback(keep_queue_flag=True)
                self._launch_ktv_track(song)
                return

        self._stop_ktv_playback()
        self.ktv_now_playing_var.set("播放伫列已结束")
        self.ktv_playback_status_var.set("所有歌曲已播放完成。")

    def _play_previous_ktv_track(self):
        if not self.ktv_queue or self.ktv_queue_index <= 1:
            messagebox.showinfo("KTV 播放", "已经是第一首歌曲了。")
            return
        self.ktv_queue_index = max(0, self.ktv_queue_index - 2)
        self._play_next_ktv_queue_track()

    def _replay_current_ktv_track(self):
        if self.ktv_current_song:
            song = self.ktv_current_song
            self._stop_ktv_playback(keep_queue_flag=True)
            self._launch_ktv_track(song)

    def _switch_ktv_audio_track(self):
        """Toggle audio tracks / vocal channels (Track 1 Original / Track 2 Acc)."""
        self.ktv_audio_channel_index = (self.ktv_audio_channel_index + 1) % 2
        track_name = "🎤 原唱/伴奏: 声道2 (伴奏)" if self.ktv_audio_channel_index == 1 else "🎤 原唱/伴奏: 声道1 (原唱)"
        self.ktv_track_mode_var.set(track_name)
        if self.ktv_current_song and self.ktv_preview_process:
            self.ktv_playback_status_var.set(f"音轨切换为：{track_name}")

    def _launch_ktv_track(self, song):
        ffplay_exe = self.bin_dir / "ffplay.exe"
        media_path = Path(song["path"])

        if not ffplay_exe.is_file():
            self.ktv_queue_playing = False
            messagebox.showwarning("找不到播放器", "找不到 engine_ffmpeg/ffplay.exe，请确认 FFmpeg 环境。")
            return

        if not media_path.is_file():
            self.ktv_playback_status_var.set(f"找不到档案：{media_path.name}")
            if self.ktv_queue_playing:
                self.root.after(100, self._play_next_ktv_queue_track)
            return

        self.ktv_current_song = song
        title = song.get("title") or media_path.stem
        artist = song.get("artist") or "Unknown Artist"

        self.ktv_now_playing_var.set(title)
        self.ktv_artist_var.set(f"🎤 {artist}")
        self.ktv_playback_status_var.set("正在播放中...")
        self.ktv_playback_start_time = time.time()
        self.ktv_queue_playing = True

        self._update_artwork_preview(song)
        self._render_ktv_queue()

        # Build ffplay arguments
        # If popup video is checked, show full video window; otherwise run in background with visualizer
        ffplay_cmd = [str(ffplay_exe), "-autoexit", "-loglevel", "error", "-window_title", f"MyKTV Player - {title}"]
        if not self.ktv_popup_video_var.get():
            ffplay_cmd.append("-nodisp")

        ffplay_cmd.append(str(media_path))

        try:
            self.ktv_preview_process = subprocess.Popen(
                ffplay_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=self.subp_flags if not self.ktv_popup_video_var.get() else 0
            )
            self._animate_visualizer()
            self.root.after(500, self._poll_ktv_playback)
        except Exception as e:
            self.ktv_preview_process = None
            self.ktv_playback_status_var.set(f"无法启动播放：{e}")
            if self.ktv_queue_playing:
                self.root.after(500, self._play_next_ktv_queue_track)

    def _poll_ktv_playback(self):
        proc = self.ktv_preview_process
        if proc is None:
            return
        if proc.poll() is None:
            self.root.after(500, self._poll_ktv_playback)
            return

        self.ktv_preview_process = None
        if self.ktv_queue_playing:
            self._play_next_ktv_queue_track()
        else:
            self.ktv_playback_status_var.set("播放完成。")
            self._stop_ktv_playback()

    def _stop_ktv_playback(self, keep_queue_flag=False):
        if not keep_queue_flag:
            self.ktv_queue_playing = False
        if self._visualizer_timer_id:
            with suppress(Exception):
                self.root.after_cancel(self._visualizer_timer_id)
            self._visualizer_timer_id = None

        proc = self.ktv_preview_process
        self.ktv_preview_process = None
        if proc and proc.poll() is None:
            with suppress(Exception):
                proc.terminate()

        if hasattr(self, "ktv_playback_status_var") and not keep_queue_flag:
            self.ktv_playback_status_var.set("播放已停止。")
            self.ktv_visualizer_bars = [6] * 24
            self._draw_video_canvas_screen(self.ktv_current_song)
            self._render_ktv_queue()

