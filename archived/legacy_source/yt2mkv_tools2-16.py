import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk, colorchooser
import subprocess
import threading
from pathlib import Path
import urllib.request
import zipfile
import shutil
import multiprocessing
import re
import webbrowser
import ssl
import json
import traceback
import time
import tempfile
from contextlib import suppress
from myktv_song_manager import APP_VERSION, SongManagerViewMixin
from myktv_ktv_selection import KtvSelectionMixin

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and not any(arg.startswith('--multiprocessing') for arg in sys.argv):
        sys.exit(0)

class MyKTVApp(SongManagerViewMixin, KtvSelectionMixin):
    _DEFAULT_WHISPER_ZH = {
        "no_speech_threshold": 0.6, "compression_ratio_threshold": 1.8,
        "temperature": [0.0, 0.2, 0.4], "beam_size": 5, "nsp_skip": 0.85, "logprob_skip": -1.5,
    }
    _DEFAULT_WHISPER_EN = {
        "no_speech_threshold": 0.55, "compression_ratio_threshold": 1.35,
        "temperature": [0.0, 0.2], "beam_size": 5, "nsp_skip": 0.35, "logprob_skip": -0.7,
    }

    def __init__(self, root):
        self.root = root
        self.version = APP_VERSION
        self.root.title(f"MyKTV {self.version} | YouTube KTV 歌曲与字幕制作")
        self.root.geometry("1100x650")
        self.root.minsize(900, 560)  # 設定最小尺寸

        if getattr(sys, 'frozen', False):
            exe_path = Path(sys.executable)
            alt = (os.environ.get('_MEIPASS2', '') or os.environ.get('PYINSTALLER_ORIG_EXEC', '')) if 'temp' in str(exe_path).lower() else ''
            self.app_dir = Path(alt).parent if alt else exe_path.parent
        else:
            self.app_dir = Path(__file__).parent

        for old_name, new_name in {  # 自動遷移舊資料夾名稱
            "bin": "engine_ffmpeg", "python_env": "runtime_python",
            "packages": "ai_libraries_cpu", "packages_gpu": "ai_libraries_gpu",
            "models": "ai_models"
        }.items():
            old_p, new_p = self.app_dir / old_name, self.app_dir / new_name
            if old_p.exists() and not new_p.exists():
                with suppress(Exception):
                    old_p.rename(new_p)

        self.bin_dir          = self.app_dir / "engine_ffmpeg"
        self.py_dir           = self.app_dir / "runtime_python"
        self.ytdlp_dir        = self.app_dir / "yt-dlp"
        self.common_lib_dir   = self.app_dir / "ai_libraries_common"
        self.lib_dir          = self.app_dir / "ai_libraries_cpu"
        self.gpu_lib_dir      = self.app_dir / "ai_libraries_gpu"
        self.directml_lib_dir = self.app_dir / "ai_libraries_directml"
        self.models_dir       = self.app_dir / "ai_models"
        self.whisper_models_dir = self.app_dir / "ai_models_whisper"

        for d in [self.bin_dir, self.py_dir, self.ytdlp_dir, self.common_lib_dir, self.lib_dir, self.gpu_lib_dir, self.directml_lib_dir, self.models_dir, self.whisper_models_dir]:
            with suppress(Exception):
                d.mkdir(parents=True, exist_ok=True)

        self.local_python = self.py_dir / "python.exe"

        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{self.py_dir}{os.pathsep}{os.environ['PATH']}"
        if hasattr(os, 'add_dll_directory'):
            with suppress(Exception):
                os.add_dll_directory(str(self.bin_dir))

        os.environ["PYTHONPATH"] = os.pathsep.join([str(self.common_lib_dir), str(self.lib_dir)])

        if str(self.common_lib_dir) not in sys.path:
            sys.path.insert(0, str(self.common_lib_dir))

        self.subp_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.file_list =[]
        self.is_processing = False
        self.cancel_event = threading.Event()   # 用於中止正在執行的任務
        self._current_process = None            # 追蹤當前子程序，供取消使用
        self._ort_fix_prompt_after_id = None
        self._ort_fix_prompt_pending = False
        self._ort_fix_prompt_active = False
        self._ort_fix_prompt_shown_keys = set()
        self._ort_fix_prompt_suppressed_keys = set()
        self._startup_component_prompt_shown = False
        self._startup_ort_check_running = False
        self._vc_runtime_prompt_shown = False
        self._last_downloaded_subtitle = None
        self._yt_js_runtime_cache = None
        self._yt_js_runtime_notice_shown = False
        self.yt_url_var = tk.StringVar()
        self.force_1080p_var = tk.BooleanVar(value=False)

        self.config_file = self.app_dir / "config.json"
        self.load_config()

        self.setup_ui_style()
        self.setup_ui()

        def _startup_check():
            self.update_status("正在检查环境...", "gray")
            self.check_components(prompt=False, show_list=False)
            # 背景靜默更新 yt-dlp，確保格式支援是最新的
            threading.Thread(target=self._startup_update_ytdlp, daemon=True).start()
            self.update_status("准备就绪", "green")
        self.root.after(500, _startup_check)

    def _get_system_fonts(self):
        """读取电脑上已安装的字体清单，优先显示本地化（中文）名称"""
        fallback = ["Arial", "微软正黑体", "新细明体", "标楷体", "DFKai-SB", "Microsoft JhengHei", "Microsoft YaHei"]

        def _get_localized_name_from_file(font_path):
            """从字体档案内读取本地化名称（支援 TTF / OTF / TTC）
            nameID=4 Full name，优先取繁中 → 简中 → 英文"""
            try:
                with open(font_path, 'rb') as f:
                    data = f.read()
                if len(data) < 12:
                    return None

                def _read_name_table(data, sfnt_offset):
                    """从指定的 sfnt offset 读取 name table，回传 (zh_tw, zh_cn, en_us)"""
                    if sfnt_offset + 12 > len(data):
                        return None, None, None
                    num_tables = int.from_bytes(data[sfnt_offset+4:sfnt_offset+6], 'big')
                    tbl_offset = sfnt_offset + 12
                    name_table_off = None
                    for _ in range(num_tables):
                        if tbl_offset + 16 > len(data):
                            break
                        tag = data[tbl_offset:tbl_offset+4]
                        tbl_off = int.from_bytes(data[tbl_offset+8:tbl_offset+12], 'big')
                        if tag == b'name':
                            name_table_off = tbl_off
                            break
                        tbl_offset += 16
                    if name_table_off is None:
                        return None, None, None
                    n = name_table_off
                    if n + 6 > len(data):
                        return None, None, None
                    count = int.from_bytes(data[n+2:n+4], 'big')
                    storage_off = name_table_off + int.from_bytes(data[n+4:n+6], 'big')
                    zh_tw = zh_cn = en_us = None
                    rec_off = n + 6
                    for _ in range(count):
                        if rec_off + 12 > len(data):
                            break
                        plat = int.from_bytes(data[rec_off:rec_off+2], 'big')
                        enc  = int.from_bytes(data[rec_off+2:rec_off+4], 'big')
                        lang = int.from_bytes(data[rec_off+4:rec_off+6], 'big')
                        nid  = int.from_bytes(data[rec_off+6:rec_off+8], 'big')
                        slen = int.from_bytes(data[rec_off+8:rec_off+10], 'big')
                        soff = int.from_bytes(data[rec_off+10:rec_off+12], 'big')
                        rec_off += 12
                        if nid != 4:
                            continue
                        raw = data[storage_off + soff: storage_off + soff + slen]
                        try:
                            if plat == 3 and enc == 1:
                                decoded = raw.decode('utf-16-be')
                                if lang == 0x0404 and zh_tw is None:
                                    zh_tw = decoded
                                elif lang == 0x0804 and zh_cn is None:
                                    zh_cn = decoded
                                elif lang == 0x0409 and en_us is None:
                                    en_us = decoded
                            elif plat == 1:
                                decoded = raw.decode('mac_roman', errors='replace')
                                if en_us is None:
                                    en_us = decoded
                        except Exception:
                            pass
                    return zh_tw, zh_cn, en_us

                magic = data[0:4]

                if magic == b'ttcf':
                    # TTC (TrueType Collection)：包含多個 sfnt，取第一個有中文名稱的
                    # TTC header: magic(4) + version(4) + numFonts(4) + offsetTable[numFonts](4 each)
                    if len(data) < 12:
                        return None
                    num_fonts = int.from_bytes(data[8:12], 'big')
                    best_zh_tw = best_zh_cn = best_en = None
                    for fi in range(num_fonts):
                        off_pos = 12 + fi * 4
                        if off_pos + 4 > len(data):
                            break
                        sfnt_off = int.from_bytes(data[off_pos:off_pos+4], 'big')
                        zh_tw, zh_cn, en_us = _read_name_table(data, sfnt_off)
                        best_zh_tw = best_zh_tw or zh_tw
                        best_zh_cn = best_zh_cn or zh_cn
                        best_en    = best_en    or en_us
                        if best_zh_tw:
                            break  # 找到繁中就夠了
                    return best_zh_tw or best_zh_cn or best_en
                else:
                    # TTF / OTF：單一 sfnt，從 offset 0 開始
                    zh_tw, zh_cn, en_us = _read_name_table(data, 0)
                    return zh_tw or zh_cn or en_us

            except Exception:
                return None

        def _collect_windows_fonts():
            import winreg
            # 登錄檔：name=顯示名稱, value=檔案名稱
            font_dirs = [r"C:\Windows\Fonts"]
            try:
                user_font_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts")
                if os.path.isdir(user_font_dir):
                    font_dirs.append(user_font_dir)
            except Exception:
                pass

            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
                (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            ]
            # reg_name → file_path
            name_to_file = {}
            for hive, rpath in reg_paths:
                try:
                    with winreg.OpenKey(hive, rpath) as key:
                        i = 0
                        while True:
                            try:
                                reg_name, file_val, _ = winreg.EnumValue(key, i)
                                i += 1
                                # file_val 可能是絕對路徑或只是檔名
                                if os.path.isabs(file_val) and os.path.exists(file_val):
                                    fpath = file_val
                                else:
                                    fpath = None
                                    for d in font_dirs:
                                        candidate = os.path.join(d, file_val)
                                        if os.path.exists(candidate):
                                            fpath = candidate
                                            break
                                clean = re.sub(r"\s*\(.*?\)\s*$", "", reg_name).strip()
                                if clean:
                                    name_to_file[clean] = fpath
                            except OSError:
                                break
                except Exception:
                    pass
            # 嘗試用本地化名稱替換
            return {(_get_localized_name_from_file(fpath) or reg_name) if fpath else reg_name
                    for reg_name, fpath in name_to_file.items()}

        try:
            if os.name == "nt":
                fonts = _collect_windows_fonts()
            else:
                import tkinter.font as tkfont
                fonts = sorted(set(tkfont.families()), key=lambda x: x.lower())
            if fonts:
                return sorted(fonts, key=lambda x: x.lower()) if os.name == "nt" else fonts
        except Exception:
            pass
        return fallback

    def _log_ffmpeg_stderr(self, e, tail=20):
        """印出 CalledProcessError 的 stderr 尾端（共用）"""
        if getattr(e, "stderr", None):
            for line in e.stderr.splitlines()[-tail:]:
                if line.strip():
                    self.log(f"    [ffmpeg] {line}")

    def _get_1080p_scale_pad_filter(self) -> str:
        """回传「等比缩放到 1920x1080 + 不足补黑边」的 FFmpeg filter 字串"""
        return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"

    def _pick_color(self, title, var, preview_attr=None):
        """通用颜色选择器"""
        if (color := colorchooser.askcolor(title=title, initialcolor=var.get()))[1]:
            var.set(color[1]); self.update_ktv_color_previews()

    def _make_color_row(self, parent, label_text, var, label_attr=None, preview_attr=None, padx_left=20):
        """通用颜色选择器列：Label + Canvas预览 + Entry + 选色按钮"""
        row = tk.Frame(parent, bg=self._bg)
        row.pack(side=tk.LEFT, padx=(padx_left, 0) if padx_left else 0)
        lbl = tk.Label(row, text=label_text, bg=self._bg, fg=self._fg, width=10, anchor=tk.W)
        lbl.pack(side=tk.LEFT, padx=5)
        if label_attr:
            setattr(self, label_attr, lbl)
        canvas = tk.Canvas(row, width=30, height=20, bg=var.get(), relief="solid", bd=1)
        canvas.pack(side=tk.LEFT, padx=5)
        if preview_attr:
            setattr(self, preview_attr, canvas)
        tk.Entry(row, textvariable=var, width=10).pack(side=tk.LEFT, padx=5)
        btn = tk.Button(row, text="选色", bg="#f0f0f0", command=lambda: self._pick_color(label_text, var, None))
        btn.pack(side=tk.LEFT, padx=5)
        return row, canvas, btn

    def _make_whisper_lang_model_row(self, parent, lang_var, model_var, lang_width=18):
        """建立语言+模型 Combobox 行（Whisper 共用）"""
        tk.Label(parent, text="语言:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        ttk.Combobox(parent, textvariable=lang_var,
            values=["auto (自动侦测)", "zh (中文)", "en (英文)", "ja (日文)", "ko (韩文)"],
            state="readonly", width=lang_width).pack(side=tk.LEFT, padx=5)
        tk.Label(parent, text="模型大小:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(20, 5))
        ttk.Combobox(parent, textvariable=model_var,
            values=["tiny", "base", "small", "medium", "large"],
            state="readonly", width=8).pack(side=tk.LEFT, padx=5)
        tk.Label(parent, text="（medium 准确度高，large 最准但较慢）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

    def _make_stable_ts_row(self, parent, var, command):
        """建立 stable-ts Checkbutton+说明 Label 行（共用）"""
        tk.Checkbutton(parent, text="启用 stable-ts 精准时间轴对齐（推荐，需先安装 stable-ts 套件）",
            variable=var, command=command,
            bg=self._bg, fg=self._fg, selectcolor=self._bg, font=("Arial", 9)).pack(side=tk.LEFT, padx=5)
        tk.Label(parent, text="可大幅提升逐字时间轴精度", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 5))

    def _info_btn(self, parent, text, command, width=10, side=tk.LEFT):
        """建立 info 色系按钮（常用于选择档案/浏览）"""
        b = tk.Button(parent, text=text, command=command, width=width,
                      bg=self.ui_colors['info'], fg='white', relief='flat',
                      padx=5, pady=5, cursor='hand2')
        b.pack(side=side)
        return b

    def _run_pip(self, packages, target_dir, log_all=False):
        """通用 pip 安装，回传是否成功"""
        cmd = [str(self.local_python), "-m", "pip", "install",
               "--target", str(target_dir),
               "--retries", "10", "--timeout", "100",
               "--no-warn-script-location"] + (packages if isinstance(packages, list) else [packages])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, creationflags=self.subp_flags,
                                encoding='utf-8', errors='replace')
        _pip_keywords = ["Downloading", "Installing", "Collecting", "ERROR", "Exception", "Traceback", "Requirement already satisfied"]
        for line in proc.stdout:
            clean = line.strip()
            if clean and (log_all or any(x in clean for x in _pip_keywords)):
                self.log(f"  > {clean[:100]}{'...' if len(clean) > 100 else ''}")
        proc.wait()
        return proc.returncode == 0

    @property
    def ffmpeg_exe(self): return self.bin_dir / "ffmpeg.exe"

    def setup_ui_style(self):
        """设定 UI 风格，让介面更现代美观"""
        style = ttk.Style()

        available_themes = style.theme_names()
        style.theme_use(next((t for t in ['clam', 'alt', 'default', 'classic'] if t in available_themes), 'default'))

        colors = {
            'primary': '#2563EB', 'success': '#10B981', 'warning': '#F59E0B',
            'danger': '#EF4444',  'info': '#6366F1',   'bg': '#F8FAFC',
            'fg': '#1E293B',      'border': '#E2E8F0',  'hover': '#DBEAFE',
        }
        self.root.configure(bg=colors['bg'])
        style.configure('TFrame', background=colors['bg'])
        style.configure('TLabel', background=colors['bg'], foreground=colors['fg'], font=('Arial', 9))
        style.configure('TButton', font=('Arial', 9, 'bold'), padding=8, borderwidth=1, relief='flat')
        style.configure('TEntry', fieldbackground='white', bordercolor=colors['border'], lightcolor=colors['border'], darkcolor=colors['border'])
        style.configure('TLabelframe', background=colors['bg'], bordercolor=colors['border'], lightcolor=colors['border'], darkcolor=colors['border'])
        style.configure('TLabelframe.Label', background=colors['bg'], foreground=colors['primary'], font=('Arial', 10, 'bold'))

        style.configure('TNotebook', background=colors['bg'], bordercolor=colors['border'])
        style.configure('TNotebook.Tab', background=colors['bg'], foreground=colors['fg'], padding=[10, 5], font=('Arial', 9))
        style.map('TNotebook.Tab', background=[('selected', colors['primary']), ('active', colors['hover'])], foreground=[('selected', 'white')])

        style.configure('Horizontal.TProgressbar',
                       background=colors['primary'], troughcolor=colors['border'],
                       bordercolor=colors['border'], lightcolor=colors['primary'], darkcolor=colors['primary'])

        for w in ('TCheckbutton', 'TRadiobutton'):
            style.configure(w, background=colors['bg'], foreground=colors['fg'])

        self.ui_colors = colors
        self._bg = colors['bg']; self._fg = colors['fg']
        self._rbkw = {'bg': colors['bg'], 'fg': colors['fg'], 'selectcolor': colors['bg']}

    def _make_listbox_panel(self, parent, btn_specs):
        """通用：建立 Listbox+Scrollbar（左）+ 按钮栏（右）"""
        lf = tk.Frame(parent, bg=self._bg)
        lf.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=5, pady=5)
        lb = tk.Listbox(lf, height=6, selectmode=tk.EXTENDED)
        lb.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        sb = tk.Scrollbar(lf, command=lb.yview)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)
        bf = tk.Frame(parent, bg=self._bg)
        bf.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        C = self.ui_colors
        for text, cmd, color in btn_specs:
            tk.Button(bf, text=text, command=cmd, width=10, bg=C.get(color, color), fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        return lb, bf

    def setup_ui(self):
        tk.Label(self.root, text=f"MP3 人声分离 & YouTube 下载/KTV 制作工具 {self.version}", font=("Arial", 13, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(pady=(3, 2))

        main_split = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=self._bg)
        main_split.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        left_panel = tk.Frame(main_split, bg=self._bg, width=200)
        left_panel.pack_propagate(False)
        main_split.add(left_panel, minsize=180)

        tk.Label(left_panel, text="功能选单", font=("Arial", 12, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(pady=(3, 5))

        self.tab_buttons = []
        self.current_tab_index = 0

        tab_info = [
            ("📺", "YouTube 一键转 KTV"),
            ("📥", "YouTube 下载 (MP3/MP4)"),
            ("🎬", "本地影片转 KTV"),
            ("🎵", "本地音档批量分离"),
            ("🎤", "本地影片辨识歌词"),
            ("📝", "合并字幕与影片"),
            ("🔧", "环境修复"),
            ("📋", "执行日志"),
            ("⚙️", "Whisper 辨识设定"),
            ("📧", "联络作者"),
            ("🎼", "歌曲管理 / Song Library"),
            ("🎤", "KTV 选歌 / Song Selection")
        ]

        self.right_scrollable_frame = tk.Frame(main_split, bg=self._bg)
        main_split.add(self.right_scrollable_frame, minsize=600)

        self.right_main_container = tk.Frame(self.right_scrollable_frame, bg=self._bg)
        self.right_main_container.pack(fill=tk.X, padx=5, pady=3)

        self.tab_container = tk.Frame(self.right_main_container, bg=self._bg)
        self.tab_container.pack(fill=tk.X, pady=(0, 3))

        self.tab_container.columnconfigure(0, weight=1)

        self.content_frames = []

        for i, (icon, name) in enumerate(tab_info):
            if i == 2:
                tk.Frame(left_panel, bg=self.ui_colors['border'], height=2).pack(fill=tk.X, padx=10, pady=3)
                tk.Label(left_panel, text="【 本地功能 】", bg=self._bg, fg=self._fg, font=("Arial", 9, "bold")).pack(anchor=tk.W, padx=15, pady=(3, 1))

            btn = tk.Button(left_panel,
                          text=f"{icon} {name}",
                          font=("Arial", 10),
                          bg=self._bg,
                          fg=self._fg,
                          relief='flat',
                          padx=15,
                          pady=6,
                          anchor='w',
                          cursor='hand2',
                          command=lambda idx=i: self.switch_tab(idx))
            btn.pack(fill=tk.X, padx=5, pady=1)
            self.tab_buttons.append(btn)

            content_frame = tk.Frame(self.tab_container, bg=self._bg)
            self.content_frames.append(content_frame)

        for i, frame in enumerate(self.content_frames):
            sticky = "nsew" if i in (7, 10, 11) else "new"
            frame.grid(row=0, column=0, sticky=sticky, padx=3, pady=2)
            if i != 0:
                frame.grid_remove()

        self.update_tab_buttons(0)

        yt_tab = self.content_frames[0]

        yt_url_frame = tk.Frame(yt_tab, bg=self._bg)
        yt_url_frame.pack(fill=tk.X, pady=2)

        tk.Label(yt_url_frame, text="YouTube 网址:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_entry = tk.Entry(yt_url_frame, textvariable=self.yt_url_var)
        self.yt_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_entry.bind("<Button-1>", self.quick_paste_url)

        tk.Label(yt_tab, text="(点击输入框自动贴上剪贴簿网址)", fg="gray", font=("Arial", 8), bg=self._bg).pack(anchor=tk.W, padx=(85, 0), pady=(1, 0))

        yt_quality_row = tk.Frame(yt_tab, bg=self._bg)
        yt_quality_row.pack(fill=tk.X, pady=2)
        tk.Label(yt_quality_row, text="MP4 画质:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_quality_var = tk.StringVar(value="1080")
        for _t, _v in [("最佳", "best"), ("1080p", "1080"), ("720p", "720"), ("480p", "480")]:
            tk.Radiobutton(yt_quality_row, text=_t, variable=self.yt_quality_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=4)

        yt_dl_tab = self.content_frames[1]

        dl_url_row = tk.Frame(yt_dl_tab, bg=self._bg)
        dl_url_row.pack(fill=tk.X, pady=2)
        tk.Label(dl_url_row, text="YouTube 网址:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_dl_url_var = tk.StringVar()
        self.yt_dl_entry = tk.Entry(dl_url_row, textvariable=self.yt_dl_url_var)
        self.yt_dl_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_dl_entry.bind("<Button-1>", self.quick_paste_dl_url)
        tk.Label(dl_url_row, text="(点击自动贴上)", fg="gray", font=("Arial", 8), bg=self._bg).pack(side=tk.LEFT)

        dl_opt_row = tk.Frame(yt_dl_tab, bg=self._bg)
        dl_opt_row.pack(fill=tk.X, pady=2)

        tk.Label(dl_opt_row, text="下载格式:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.dl_type_var = tk.StringVar(value="both")
        for _t, _v in [("MP3 + MP4","both"),("仅 MP3","mp3"),("仅 MP4","mp4")]:
            tk.Radiobutton(dl_opt_row, text=_t, variable=self.dl_type_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=4)

        tk.Label(dl_opt_row, text="  |  MP4 画质:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(10, 0))
        self.dl_quality_var = tk.StringVar(value="1080")
        self._dl_quality_rbs = {}
        for _t, _v in [("最佳","best"),("1080p","1080"),("720p","720"),("480p","480")]:
            _rb = tk.Radiobutton(dl_opt_row, text=_t, variable=self.dl_quality_var, value=_v, **self._rbkw)
            _rb.pack(side=tk.LEFT, padx=4)
            self._dl_quality_rbs[_v] = _rb

        # ── 共用 BooleanVar：強制等比輸出 1080p（第一頁/第二頁同步）──
        # 第一頁（YouTube 轉 MKV / KTV）會在合成時套用 scale+pad
        # 第二頁（純下載）會在下載完成後再用 ffmpeg 輸出一份 1080p（_1080p.mp4）
        # 第二頁：FFmpeg 強制輸出 1080p（等比放大 + 不足補黑邊）
        dl_force_row = tk.Frame(yt_dl_tab, bg=self._bg)
        dl_force_row.pack(fill=tk.X, pady=(2, 0))

        tk.Checkbutton(dl_force_row, text="强制等比输出 1080p（不足自动补黑边）",
                       variable=self.force_1080p_var, bg=self._bg, fg=self._fg,
                       selectcolor=self._bg, font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(10, 0))

        local_v_tab = self.content_frames[2]

        self.v_list = []
        self.v_listbox, _ = self._make_listbox_panel(local_v_tab, [
            ('加入影片',   self.browse_local_video,   'info'),
            ('加入资料夹', self.browse_local_v_folder,'info'),
            ('移除选取',   self.remove_selected_v,   'warning'),
            ('清除清单',   self.clear_v_list,         'danger'),
        ])

        file_tab = self.content_frames[3]

        self.file_listbox, _ = self._make_listbox_panel(file_tab, [
            ('加入档案', self.browse_file,        'info'),
            ('移除选取', self.remove_selected_file,'warning'),
            ('清除清单', self.clear_files,         'danger'),
        ])

        # ── stable-ts 共用 BooleanVar（lyrics_options_row 與 Tab4 同步）──
        self.use_stable_ts_var = tk.BooleanVar(value=self.config.get("use_stable_ts", True))

        def _on_stable_ts_toggle():
            """任一勾选框改变时，写入 config 并储存"""
            self.config["use_stable_ts"] = self.use_stable_ts_var.get()
            self.save_config()

        recognize_tab = self.content_frames[4]

        rec_video_frame = tk.LabelFrame(recognize_tab, text="影片档案", padx=10, pady=10, bg=self._bg)
        rec_video_frame.pack(fill=tk.X, pady=5)

        self.rec_video_path_var = tk.StringVar()
        tk.Entry(rec_video_frame, textvariable=self.rec_video_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self._info_btn(rec_video_frame, "选择影片", self.browse_rec_video)

        rec_options_frame = tk.LabelFrame(recognize_tab, text="辨识选项", padx=10, pady=10, bg=self._bg)
        rec_options_frame.pack(fill=tk.X, pady=5)

        rec_lang_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_lang_row.pack(fill=tk.X, pady=(0, 6))
        # 預設不強制指定語言：讓 Whisper 自動偵測，避免混合語言歌曲被「硬轉」成單一語言（例如韓文被轉成中文）
        self.rec_language_var = tk.StringVar(value="auto")
        self.whisper_model_var = tk.StringVar(value="medium")
        self._make_whisper_lang_model_row(rec_lang_row, self.rec_language_var, self.whisper_model_var)

        rec_sep_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_sep_row.pack(fill=tk.X)
        self.rec_separate_first_var = tk.BooleanVar(value=True)
        tk.Checkbutton(rec_sep_row, text="先分离人声再辨识（准确度较高，需要较长时间）",
                       variable=self.rec_separate_first_var, bg=self._bg, fg=self._fg,
                       selectcolor=self._bg, font=("Arial", 9)).pack(side=tk.LEFT, padx=5)

        rec_zh_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_zh_row.pack(fill=tk.X, pady=(4, 0))
        tk.Label(rec_zh_row, text="输出文字:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.rec_lyrics_language_var = tk.StringVar(value="traditional")
        for _t, _v in [("繁体中文（预设）","traditional"),("简体中文","simplified"),("原文字（不转换）","original")]:
            tk.Radiobutton(rec_zh_row, text=_t, variable=self.rec_lyrics_language_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=4)

        rec_stable_ts_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_stable_ts_row.pack(fill=tk.X, pady=(4, 0))
        self._make_stable_ts_row(rec_stable_ts_row, self.use_stable_ts_var, _on_stable_ts_toggle)

        tk.Label(recognize_tab, text="💡 提示：此功能需要使用语音辨识模型", bg=self._bg, fg=self._fg, font=("Arial", 9)).pack(anchor=tk.W, padx=10, pady=5)

        merge_tab = self.content_frames[5]

        video_frame = tk.LabelFrame(merge_tab, text="影片或图片", padx=10, pady=10, bg=self._bg)
        video_frame.pack(fill=tk.X, pady=5)

        self.merge_video_path_var = tk.StringVar()
        tk.Entry(video_frame, textvariable=self.merge_video_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self._info_btn(video_frame, "选择档案", self.browse_merge_video)
        with suppress(Exception):
            self.merge_video_path_var.trace_add('write', lambda *_: self._refresh_merge_audio_ui())

        # ── 影片/圖片 外掛音訊（圖片必須選音訊；影片可選擇是否用外掛音訊）──
        self.merge_audio_path_var = tk.StringVar()
        self.merge_use_external_audio_var = tk.BooleanVar(value=False)

        # 為了讓「聲音檔案」區塊永遠緊貼在「影片或圖片」下方，
        # 使用專用容器，避免 pack/pack_forget 重新 pack 時跑到其他區塊（例如字幕檔案）下面。
        self.merge_audio_container = tk.Frame(merge_tab, bg=self._bg)
        self.merge_audio_container.pack(fill=tk.X, pady=(0, 0))

        self.merge_audio_option_row = tk.Frame(self.merge_audio_container, bg=self._bg)
        self.merge_audio_option_row.pack(fill=tk.X, pady=(0, 2))
        self.merge_audio_option_row.pack_forget()

        self.merge_audio_file_row = tk.LabelFrame(self.merge_audio_container, text="声音档案", padx=10, pady=10, bg=self._bg)
        self.merge_audio_file_row.pack(fill=tk.X, pady=5)
        self.merge_audio_file_row.pack_forget()

        tk.Entry(self.merge_audio_file_row, textvariable=self.merge_audio_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self._info_btn(self.merge_audio_file_row, "选择声音", self.browse_merge_audio)

        self.merge_external_audio_chk = tk.Checkbutton(
            self.merge_audio_option_row, text="声音档案另外",
            variable=self.merge_use_external_audio_var, command=self._refresh_merge_audio_ui,
            bg=self._bg, fg=self._fg, selectcolor=self._bg, font=("Arial", 9))
        self.merge_external_audio_chk.pack(side=tk.LEFT, padx=10)

        subtitle_frame = tk.LabelFrame(merge_tab, text="字幕档案", padx=10, pady=10, bg=self._bg)
        subtitle_frame.pack(fill=tk.X, pady=5)

        self.merge_subtitle_path_var = tk.StringVar()
        tk.Entry(subtitle_frame, textvariable=self.merge_subtitle_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self._info_btn(subtitle_frame, "选择字幕", self.browse_merge_subtitle)

        tk.Label(merge_tab, text="⚠️ 提醒：AI 辨识的歌词可能有错字，建议您先手动修正，或使用其他 AI 工具修复错字后再进行字幕合并。",
            font=("Arial", 9), fg="#E65100", bg=self._bg, wraplength=780, justify=tk.LEFT
        ).pack(fill=tk.X, padx=10, pady=(5, 5))

        self.ktv_color_frame = tk.LabelFrame(merge_tab, text="字幕样式设定", padx=10, pady=10, bg=self._bg)
        self.ktv_color_frame.pack(fill=tk.X, pady=5)
        self.ktv_color_frame.pack_forget()

        # 四個顏色選擇器全部同一行
        colors_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        colors_row.pack(fill=tk.X, pady=4)

        def _mk_color_row(parent_frame, label_text, color_var, default_color):
            color_var.set(default_color)
            fr = tk.Frame(parent_frame, bg=self._bg)
            fr.pack(side=tk.LEFT, padx=(0, 8))
            lbl = tk.Label(fr, text=label_text, bg=self._bg, fg=self._fg)
            lbl.pack(side=tk.LEFT)
            preview = tk.Canvas(fr, width=22, height=18, bg=color_var.get(), relief="solid", bd=1)
            preview.pack(side=tk.LEFT, padx=3)
            entry = tk.Entry(fr, textvariable=color_var, width=8)
            entry.pack(side=tk.LEFT, padx=2)
            btn = tk.Button(fr, text="选色", bg="#f0f0f0", padx=3,
                            command=lambda: self._pick_color(label_text, color_var, None))
            btn.pack(side=tk.LEFT, padx=2)
            return fr, lbl, preview, entry, btn

        # --- 未唱顏色 ---
        self.ktv_unplayed_color_var = tk.StringVar()
        _uc_row, self.unplayed_label, self.unplayed_color_preview, _uc_entry, _uc_btn = _mk_color_row(
            colors_row, "未唱颜色:", self.ktv_unplayed_color_var, "#FFFFFF")

        # --- 已唱顏色 ---
        self.ktv_played_color_var = tk.StringVar()
        self.played_row, _, self.played_color_preview, _pc_entry, _pc_btn = _mk_color_row(
            colors_row, "已唱颜色:", self.ktv_played_color_var, "#0000FF")

        # --- 邊框顏色（SRT 模式可勾選啟用；JSON 模式固定啟用）---
        self.ktv_border_color_var = tk.StringVar()
        _bc_row, _border_lbl, self.border_color_preview, self._border_entry, self._border_btn = _mk_color_row(
            colors_row, "未唱边框:", self.ktv_border_color_var, "#000000")
        # 將「邊框顏色」做成可勾選（預設打勾）；SRT 才會用到這個勾選
        self.srt_use_border_var = tk.BooleanVar(value=True)
        with suppress(Exception): _border_lbl.destroy()
        self.border_label = tk.Checkbutton(_bc_row, text="边框颜色:",
            variable=self.srt_use_border_var, bg=self._bg, fg=self._fg, selectcolor=self._bg)
        # 重新排列：把「邊框顏色」放在最前面（原本 _mk_color_row 會先 pack 預覽/輸入框/按鈕）
        with suppress(Exception):
            self.border_label.pack_forget(); self.border_color_preview.pack_forget()
            self._border_entry.pack_forget(); self._border_btn.pack_forget()
        self.border_label.pack(side=tk.LEFT)
        self.border_color_preview.pack(side=tk.LEFT, padx=3)
        self._border_entry.pack(side=tk.LEFT, padx=2)
        self._border_btn.pack(side=tk.LEFT, padx=2)

        def _toggle_border(bool_var, color_var, entry_widget, btn_widget, preview_widget):
            """通用边框勾选开关：取消时停用颜色选择器"""
            enabled = bool_var.get()
            state = tk.NORMAL if enabled else tk.DISABLED
            with suppress(Exception):
                entry_widget.config(state=state)
                btn_widget.config(state=state)
                preview_widget.config(bg=color_var.get() if enabled else "#cccccc")

        def _toggle_srt_border():
            _toggle_border(self.srt_use_border_var, self.ktv_border_color_var,
                           self._border_entry, self._border_btn, self.border_color_preview)

        # 讓勾選生效
        self.border_label.config(command=_toggle_srt_border)
        # 方便其他地方（例如選到字幕檔後）強制刷新狀態
        self._toggle_srt_border = _toggle_srt_border

        # --- 已唱邊框（含勾選）---
        self.ktv_use_played_border_var = tk.BooleanVar(value=True)
        self.ktv_played_border_color_var = tk.StringVar(value="#ffffff")

        _pb_row = tk.Frame(colors_row, bg=self._bg)
        _pb_row.pack(side=tk.LEFT)
        self.played_border_row = _pb_row
        _toggle_played_border = lambda: _toggle_border(
            self.ktv_use_played_border_var, self.ktv_played_border_color_var,
            self._played_border_entry, self._played_border_btn, self.played_border_color_preview)
        self._played_border_chk = tk.Checkbutton(_pb_row, text="启用已唱边框",
            variable=self.ktv_use_played_border_var, bg=self._bg, fg=self._fg,
            selectcolor=self._bg, command=_toggle_played_border)
        self._played_border_chk.pack(side=tk.LEFT)
        self.played_border_color_preview = tk.Canvas(_pb_row, width=22, height=18, bg=self.ktv_played_border_color_var.get(), relief="solid", bd=1)
        self.played_border_color_preview.pack(side=tk.LEFT, padx=3)
        self._played_border_entry = tk.Entry(_pb_row, textvariable=self.ktv_played_border_color_var, width=8)
        self._played_border_entry.pack(side=tk.LEFT, padx=2)
        self._played_border_btn = tk.Button(_pb_row, text="选色", bg="#f0f0f0", padx=3,
                  command=lambda: self._pick_color("已唱边框", self.ktv_played_border_color_var, None))
        self._played_border_btn.pack(side=tk.LEFT, padx=2)

        # 邊框粗細（px）：同時影響 JSON→ASS 及 SRT→ASS 的 Outline 粗細
        map_expand_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        map_expand_row.pack(fill=tk.X, pady=5)
        tk.Label(map_expand_row, text="边框粗细:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)

        self.ktv_border_map_expand_factor_var = tk.DoubleVar(value=4)
        self._map_expand_scale = tk.Scale(map_expand_row, from_=0, to=20, resolution=1,
            orient=tk.HORIZONTAL, variable=self.ktv_border_map_expand_factor_var,
            length=220, showvalue=False, bg=self._bg, fg=self._fg, highlightthickness=0)
        self._map_expand_scale.pack(side=tk.LEFT, padx=(0, 8))
        self._map_expand_entry = tk.Entry(map_expand_row, textvariable=self.ktv_border_map_expand_factor_var, width=5, justify='center')
        self._map_expand_entry.pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(map_expand_row, text="（像素；预设 4）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

        # 初始化一次，確保控制項狀態與預覽正確
        _toggle_played_border()
        _toggle_srt_border()

        font_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        font_row.pack(fill=tk.X, pady=5)
        tk.Label(font_row, text="字幕字体:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)

        available_fonts = self._get_system_fonts()
        default_font = "微软正黑体" if "微软正黑体" in available_fonts else (available_fonts[0] if available_fonts else "Arial")
        self.ktv_font_var = tk.StringVar(value=default_font)
        font_menu = ttk.Combobox(font_row, textvariable=self.ktv_font_var, values=available_fonts, state="normal", width=20)
        font_menu.pack(side=tk.LEFT, padx=5)
        font_menu.bind("<<ComboboxSelected>>", lambda e: self.update_ktv_color_previews())

        tk.Label(font_row, text="字体大小:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(20, 5))
        self.ktv_font_size_var = tk.StringVar(value="60")
        font_size_spin = tk.Spinbox(font_row, from_=10, to=200, textvariable=self.ktv_font_size_var, width=5, justify='center')
        font_size_spin.pack(side=tk.LEFT, padx=2)
        tk.Label(font_row, text="pt　（预设 60）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=2)

        margin_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        margin_row.pack(fill=tk.X, pady=5)
        tk.Label(margin_row, text="字幕高度:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.subtitle_margin_var = tk.StringVar(value="0")
        tk.Entry(margin_row, textvariable=self.subtitle_margin_var, width=6, justify='center').pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(margin_row, text="（0 = 预设位置，正数往上，负数往下）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

        self.two_line_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        self.two_line_row.pack(fill=tk.X, pady=5)
        self.ktv_two_line_var = tk.BooleanVar(value=False)
        self.ktv_two_line_advance_var = tk.StringVar(value="1.5")
        self.ktv_two_line_gap_var = tk.StringVar(value="")
        self.ktv_two_line_top_x_var = tk.StringVar(value="0")
        self.ktv_two_line_bottom_x_var = tk.StringVar(value="0")

        def _toggle_two_line_advance_entry():
            try:
                st = "normal" if self.ktv_two_line_var.get() else "disabled"
                for w in (self.ktv_two_line_advance_entry, self.ktv_two_line_gap_entry,
                          getattr(self, 'ktv_two_line_top_x_entry', None),
                          getattr(self, 'ktv_two_line_bottom_x_entry', None)):
                    if w: w.config(state=st)
            except Exception:
                pass

        tk.Checkbutton(self.two_line_row, text="双行字幕（下一句提前显示）",
                       variable=self.ktv_two_line_var, bg=self._bg, fg=self._fg,
                       selectcolor=self._bg, command=_toggle_two_line_advance_entry).pack(side=tk.LEFT, padx=5)
        tk.Label(self.two_line_row, text="提前:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(10, 3))
        self.ktv_two_line_advance_entry = tk.Entry(self.two_line_row, textvariable=self.ktv_two_line_advance_var, width=5, justify='center')
        self.ktv_two_line_advance_entry.pack(side=tk.LEFT, padx=(0, 3))
        tk.Label(self.two_line_row, text="秒　", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)
        tk.Label(self.two_line_row, text="行距:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(10, 3))
        self.ktv_two_line_gap_entry = tk.Entry(self.two_line_row, textvariable=self.ktv_two_line_gap_var, width=5, justify='center')
        self.ktv_two_line_gap_entry.pack(side=tk.LEFT, padx=(0, 3))
        tk.Label(self.two_line_row, text="px（留空 = 自动）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)
        _toggle_two_line_advance_entry()

        self.two_line_x_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        self.two_line_x_row.pack(fill=tk.X, pady=5)
        tk.Label(self.two_line_x_row, text="水平位置:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        tk.Label(self.two_line_x_row, text="上行 X:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(0, 3))
        self.ktv_two_line_top_x_entry = tk.Entry(self.two_line_x_row, textvariable=self.ktv_two_line_top_x_var, width=6, justify='center')
        self.ktv_two_line_top_x_entry.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(self.two_line_x_row, text="下行 X:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(0, 3))
        self.ktv_two_line_bottom_x_entry = tk.Entry(self.two_line_x_row, textvariable=self.ktv_two_line_bottom_x_var, width=6, justify='center')
        self.ktv_two_line_bottom_x_entry.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(self.two_line_x_row, text="（正数往右、负数往左；0 = 置中）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

        self.ktv_color_mode_var = tk.StringVar(value="slide")
        self.mode_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        self.mode_hint_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        self.ktv_mode_hint_label = tk.Label(self.mode_hint_row,
            text="  💡 滑动渐变：使用 \\kf tag，颜色由左至右平滑扫过",
            font=("Arial", 8), fg="gray", bg=self._bg, anchor=tk.W)

        # 漸變微調固定值（UI 已移除，以固定預設值運行）
        self.ktv_pre_show_var = tk.StringVar(value="0.0")
        self.ktv_hold_sec_var = tk.StringVar(value="0")
        self.ktv_speed_factor_var = tk.StringVar(value="1.0")
        self.ktv_singing_end_ratio_var = tk.StringVar(value="1.0")

        output_frame = tk.LabelFrame(merge_tab, text="输出设定", padx=10, pady=10, bg=self._bg)
        output_frame.pack(fill=tk.X, pady=5)

        fmt_row = tk.Frame(output_frame, bg=self._bg)
        fmt_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(fmt_row, text="输出格式:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT)
        self.merge_video_format_var = tk.StringVar(value="mp4")
        for _t, _v in [("MP4","mp4"),("MKV","mkv")]:
            tk.Radiobutton(fmt_row, text=_t, variable=self.merge_video_format_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=10)

        # 合併字幕與影片：輸出 1080p（等比＋補黑邊）
        merge_force_row = tk.Frame(output_frame, bg=self._bg)
        merge_force_row.pack(fill=tk.X, pady=(0, 4))
        tk.Checkbutton(merge_force_row, text="强制等比输出 1080p（不足自动补黑边）",
                       variable=self.force_1080p_var, bg=self._bg, fg=self._fg,
                       selectcolor=self._bg, font=("Arial", 9)).pack(side=tk.LEFT, padx=0)

        # 合併按鈕區（同一行，json_to_ass_btn 選到 JSON/SRT 才顯示）
        merge_btn_frame = tk.Frame(merge_tab, bg=self._bg)
        merge_btn_frame.pack(fill=tk.X, pady=8, padx=5)
        # 三欄等寬：col 0=開始合併, col 1=JSON轉ASS(隱藏時佔位), col 2=取消任務
        for _c in range(3): merge_btn_frame.columnconfigure(_c, weight=1)

        self.merge_start_btn = tk.Button(merge_btn_frame, text="\u25b6 开始字幕合并",
            command=self.start_merge_subtitle_video, bg=self.ui_colors['warning'], fg='white',
            font=("Arial", 10, "bold"), relief='flat', padx=10, pady=8, cursor='hand2')
        self.merge_start_btn.grid(row=0, column=0, sticky='ew', padx=(0, 4))

        self.json_to_ass_btn = tk.Button(merge_btn_frame, text="\U0001f4dd 将 JSON 转 ASS 字幕",
            command=self.start_json_to_ass_only, bg='#5D4037', fg='white',
            font=("Arial", 10, "bold"), relief='flat', padx=10, pady=8, cursor='hand2')
        # 預設隱藏，選到 JSON / SRT 才顯示（文字/功能由 browse_merge_subtitle 動態切換）

        # 取消任務按鈕，固定在最右欄
        self.merge_cancel_btn = tk.Button(merge_btn_frame, text="🛑 取消任务",
            command=self.cancel_processing, bg=self.ui_colors['danger'], fg="white",
            font=("Arial", 10, "bold"), state=tk.DISABLED, relief='flat',
            padx=8, pady=8, cursor='hand2')
        self.merge_cancel_btn.grid(row=0, column=2, sticky='ew', padx=(4, 0))

        repair_tab = self.content_frames[6]

        self.repair_components_frame = tk.Frame(repair_tab, bg=self._bg)
        self.repair_components_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        repair_btn_frame = tk.Frame(repair_tab, bg=self._bg)
        repair_btn_frame.pack(fill=tk.X, padx=10, pady=10)

        for _attr, _txt in [('repair_select_all_btn', '全选'), ('repair_select_none_btn', '全不选')]:
            b = tk.Button(repair_btn_frame, text=_txt, width=10, bg="#f0f0f0", cursor='hand2')
            b.pack(side=tk.LEFT, padx=5)
            setattr(self, _attr, b)

        self.repair_start_btn = tk.Button(repair_btn_frame, text="🔧 开始修复",
            bg=self.ui_colors['warning'], fg="white", font=("Arial", 10, "bold"),
            padx=20, pady=8, cursor='hand2')
        self.repair_start_btn.pack(side=tk.RIGHT, padx=5)

        log_tab = self.content_frames[7]
        log_tab.rowconfigure(1, weight=1)  # 讓 log_area 列可以垂直擴展

        log_header_frame = tk.Frame(log_tab, bg=self._bg)
        log_header_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(log_header_frame, text="执行日志", fg=self._fg, bg=self._bg, font=("Arial", 12, "bold")).pack(side=tk.LEFT)

        def copy_log_to_clipboard():
            log_content = self.log_area.get("1.0", tk.END).strip()
            copy_log_btn.config(text="✅ 已复制！" if log_content else "（日志为空）")
            if log_content:
                self.root.clipboard_clear(); self.root.clipboard_append(log_content)
            self.root.after(2000, lambda: copy_log_btn.config(text="📋 复制到剪贴簿"))

        copy_log_btn = tk.Button(log_header_frame, text="📋 复制到剪贴簿", command=copy_log_to_clipboard,
                                 bg=self.ui_colors['info'], fg='white', font=("Arial", 9),
                                 relief='flat', padx=8, pady=4, cursor='hand2')
        copy_log_btn.pack(side=tk.RIGHT)

        self.log_area = scrolledtext.ScrolledText(log_tab, height=20, font=("Consolas", 9), bg='white', fg=self._fg, relief='flat', bd=1, padx=8, pady=8)
        self.log_area.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

        self.setup_whisper_settings_tab(self.content_frames[8])

        contact_tab = self.content_frames[9]
        def _lbl(parent, text, font=("Arial", 11), fg=None, **kw):
            tk.Label(parent, text=text, font=font, bg=self._bg, fg=fg or self._fg, **kw).pack(anchor=tk.W, padx=40)
        tk.Label(contact_tab, text=f"【MyKTV {self.version} 开发者信息】", font=("Arial", 14, "bold"), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=20, pady=(20, 10))
        _lbl(contact_tab, "作者：KM")
        _lbl(contact_tab, "WhatsApp ID：@kokmingx")

        fb_frame = tk.Frame(contact_tab, bg=self._bg)
        fb_frame.pack(anchor=tk.W, padx=40, pady=5)
        tk.Label(fb_frame, text="Facebook：", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(side=tk.LEFT)
        fb_link = tk.Label(fb_frame, text="https://www.facebook.com/theamkokming/", fg="blue", cursor="hand2", font=("Arial", 11, "underline"), bg=self._bg)
        fb_link.pack(side=tk.LEFT)
        fb_link.bind("<Button-1>", lambda e: webbrowser.open("https://www.facebook.com/theamkokming/"))

        tk.Label(contact_tab, text="", bg=self._bg).pack(pady=15)
        tk.Label(contact_tab, text="【捐款赞助】", font=("Arial", 14, "bold"), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=20, pady=(0, 10))
        _lbl(contact_tab, "若您觉得此工具好用，欢迎赞助支持开发者！", wraplength=600, justify=tk.LEFT)

        bank_frame = tk.Frame(contact_tab, bg=self._bg)
        bank_frame.pack(anchor=tk.W, padx=40, pady=15)
        tk.Label(bank_frame, text="银行代码：", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W)
        tk.Label(bank_frame, text="帐号：", font=("Arial", 11, "bold"), fg="#D32F2F", bg=self._bg).pack(anchor=tk.W)
        tk.Label(bank_frame, text="户名：", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W)

        _lbl(contact_tab, "本工具基于 David Chang 开发的 YT2MKV。", wraplength=700, justify=tk.LEFT)
        _lbl(contact_tab, "如需查看原始版本或向他捐款，请访问此页面：", wraplength=700, justify=tk.LEFT)
        original_link = tk.Label(contact_tab, text="访问 David Chang 的 Facebook 页面",
            fg="blue", cursor="hand2", font=("Arial", 11, "underline"), bg=self._bg)
        original_link.pack(anchor=tk.W, padx=40, pady=(2, 6))
        original_link.bind("<Button-1>", lambda _event: webbrowser.open("https://www.facebook.com/share/p/1BHjRR3yfB/"))



        self.settings_frame = tk.LabelFrame(self.right_main_container, text="核心设定", padx=10, pady=5, bg=self._bg)
        self.settings_frame.pack(fill=tk.X, pady=(0, 5))

        self.btn_frame = tk.Frame(self.right_main_container, bg=self._bg)
        self.btn_frame.pack(pady=(0, 3))

        self.left_btn_frame = tk.Frame(self.btn_frame, bg=self._bg)
        self.left_btn_frame.pack(side=tk.LEFT)

        self.start_btn = tk.Button(self.left_btn_frame, text="开始分离任务", command=self.on_start_click,
                                  bg=self.ui_colors['success'], fg="white", font=("Arial", 11, "bold"),
                                  width=20, relief='flat', padx=12, pady=5, cursor='hand2')
        self.start_btn.pack(side=tk.LEFT, padx=8)
        self.cancel_btn = tk.Button(self.left_btn_frame, text="取消任务", command=self.cancel_processing,
                                   bg=self.ui_colors['danger'], fg="white", font=("Arial", 10, "bold"),
                                   width=12, state=tk.DISABLED, relief='flat', padx=8, pady=4, cursor='hand2')
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        out_row = tk.Frame(self.settings_frame)
        out_row.pack(fill=tk.X, pady=2)
        self.output_dir_var = tk.StringVar(value=self.config.get("output_dir", str(self.app_dir / "output")))
        tk.Label(out_row, text="输出目录:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        tk.Entry(out_row, textvariable=self.output_dir_var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self._info_btn(out_row, "浏览", self.browse_output_dir, width=6, side=tk.RIGHT)

        self.opt_row = tk.Frame(self.settings_frame)
        self.opt_row.pack(fill=tk.X, pady=2)

        tk.Label(self.opt_row, text="运算装置:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.device_var = tk.StringVar(value="cpu")
        for _t, _v in [("CPU","cpu"), ("GPU (NVIDIA)","gpu"), ("GPU (DirectML)","directml")]:
            tk.Radiobutton(self.opt_row, text=_t, variable=self.device_var, value=_v).pack(side=tk.LEFT, padx=5)

        tk.Button(self.opt_row, text="🔍 检测 GPU 环境", command=self.check_gpu_env, font=("Arial", 9), bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=10)

        self.denoise_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.opt_row, text="启用 AI 去噪 (推荐)", variable=self.denoise_var).pack(side=tk.RIGHT, padx=10)

        self.overlap_var = tk.DoubleVar(value=0.5)
        self.vocal_volume_var = tk.DoubleVar(value=100)
        self.instrumental_volume_var = tk.DoubleVar(value=100)
        self.vocal_mix_label_var = tk.StringVar(value="")

        self.video_format_var = tk.StringVar(value="mkv")
        self.audio_track_mode_var = tk.StringVar(value="dual")

        self.model_row = tk.Frame(self.settings_frame)
        self.model_row.pack(fill=tk.X, pady=2)

        tk.Label(self.model_row, text="AI 模型:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        model_options =[
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏优化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品质综合)",
            "Kim_Vocal_2.onnx (MDX - 极致人声提取)",
            "htdemucs.yaml (Demucs - 4音轨高品质分离)",
            "htdemucs_ft.yaml (Demucs - 流行乐优化)",
            "htdemucs_6s.yaml (Demucs - 6音轨扩充版)"
        ]
        self.model_menu = ttk.Combobox(self.model_row, textvariable=self.model_var, values=model_options, state="readonly", width=45)
        self.model_menu.pack(side=tk.LEFT, padx=5)
        self.model_menu.current(0)

        self.output_format_row = tk.Frame(self.settings_frame)
        self.output_format_row.pack(fill=tk.X, pady=2)

        tk.Label(self.output_format_row, text="输出格式:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.output_format_var = tk.StringVar(value="mp3")
        for fmt in ["mp3", "wav", "flac"]:
            tk.Radiobutton(self.output_format_row, text=fmt.upper(), variable=self.output_format_var, value=fmt).pack(side=tk.LEFT, padx=10)

        self.ktv_row = tk.Frame(self.settings_frame)
        self.ktv_row.pack(fill=tk.X, pady=2)

        tk.Label(self.ktv_row, text="KTV 影片格式:").pack(side=tk.LEFT)
        for _t, _v in [("MKV（预设，相容性最佳）","mkv"),("MP4","mp4")]:
            tk.Radiobutton(self.ktv_row, text=_t, variable=self.video_format_var, value=_v).pack(side=tk.LEFT, padx=5)

        self.track_row = tk.Frame(self.settings_frame)
        self.track_row.pack(fill=tk.X, pady=2)

        tk.Label(self.track_row, text="伴唱带音轨:").pack(side=tk.LEFT)
        for _t, _v in [("双音轨（伴唱＋人声，预设）","dual"),
                        ("左伴唱／右人声+伴奏（单音轨立体声）","lr"),
                        ("纯伴唱（仅伴奏，无人声音轨）","inst")]:
            tk.Radiobutton(self.track_row, text=_t, variable=self.audio_track_mode_var, value=_v).pack(side=tk.LEFT, padx=5)

        self.mix_row = tk.Frame(self.settings_frame)
        self.mix_row.pack(fill=tk.X, pady=2)
        tk.Label(self.mix_row, text="导唱混合比例:").pack(side=tk.LEFT)
        
        # 人聲音量滑块
        tk.Label(self.mix_row, text="人声").pack(side=tk.LEFT, padx=(10, 0))
        tk.Scale(self.mix_row, from_=0, to=100, orient=tk.HORIZONTAL, showvalue=False,
                 resolution=5, length=150, variable=self.vocal_volume_var,
                 command=lambda _value: self.update_vocal_mix_label()).pack(side=tk.LEFT, padx=5)
        
        # 伴奏音量滑块
        tk.Label(self.mix_row, text="伴奏").pack(side=tk.LEFT, padx=(10, 0))
        tk.Scale(self.mix_row, from_=0, to=100, orient=tk.HORIZONTAL, showvalue=False,
                 resolution=5, length=150, variable=self.instrumental_volume_var,
                 command=lambda _value: self.update_vocal_mix_label()).pack(side=tk.LEFT, padx=5)
        
        tk.Label(self.mix_row, textvariable=self.vocal_mix_label_var, width=35, anchor="w").pack(side=tk.LEFT, padx=5)
        self.update_vocal_mix_label()

        self.lyrics_row = tk.Frame(self.settings_frame)
        self.lyrics_row.pack(fill=tk.X, pady=2)

        self.enable_lyrics_recognition_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.lyrics_row, text="启用 Whisper AI 歌词识别（产生 SRT 字幕档）", variable=self.enable_lyrics_recognition_var, font=("Arial", 9, "bold")).pack(side=tk.LEFT)

        tk.Label(self.lyrics_row, text="输出文字:").pack(side=tk.LEFT, padx=(20, 5))
        self.lyrics_language_var = tk.StringVar(value="traditional")
        for _t, _v in [("繁体中文","traditional"),("简体中文","simplified"),("原文字","original")]:
            tk.Radiobutton(self.lyrics_row, text=_t, variable=self.lyrics_language_var, value=_v).pack(side=tk.LEFT, padx=4)

        self.lyrics_options_row = tk.Frame(self.settings_frame)
        self.lyrics_options_row.pack(fill=tk.X, pady=2)
        # 預設不強制指定語言：讓 Whisper 自動偵測，避免混合語言歌曲被「硬轉」成單一語言（例如韓文被轉成中文）
        self.yt_whisper_language_var = tk.StringVar(value="auto")
        self.yt_whisper_model_var = tk.StringVar(value="medium")
        self._make_whisper_lang_model_row(self.lyrics_options_row, self.yt_whisper_language_var, self.yt_whisper_model_var, lang_width=12)

        self.lyrics_stable_ts_row = tk.Frame(self.settings_frame)
        self.lyrics_stable_ts_row.pack(fill=tk.X, pady=2)
        self._make_stable_ts_row(self.lyrics_stable_ts_row, self.use_stable_ts_var, _on_stable_ts_toggle)
        tk.Label(self.lyrics_stable_ts_row, text="KTV 歌词对齐必备",
                 bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=(6, 0))

        self.extra_video_row = tk.Frame(self.settings_frame)
        self.extra_video_row.pack(fill=tk.X, pady=2)

        self.force_1080p_chk = tk.Checkbutton(self.extra_video_row, text="强制等比输出 1080p（不足自动补黑边）", variable=self.force_1080p_var)
        self.force_1080p_chk.pack(side=tk.LEFT)

        self.yt_cc_var = tk.BooleanVar(value=False)
        self.yt_cc_chk = tk.Checkbutton(self.extra_video_row, text="启用 YouTube CC 字幕处理", variable=self.yt_cc_var, command=self.refresh_yt_subtitle_mode_ui)
        self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))

        self.yt_subtitle_mode_var = tk.StringVar(value="mux")
        self.yt_subtitle_mode_row = tk.Frame(self.settings_frame)
        tk.Label(self.yt_subtitle_mode_row, text="字幕模式:").pack(side=tk.LEFT)
        for _t, _v in [("下载SRT字幕","srt_only"),("下载srt字幕并合成","mux")]:
            tk.Radiobutton(self.yt_subtitle_mode_row, text=_t, variable=self.yt_subtitle_mode_var, value=_v, command=self.refresh_yt_subtitle_mode_ui).pack(side=tk.LEFT, padx=5)

        self.status_frame = tk.Frame(self.right_scrollable_frame, bg=self._bg)
        self.status_frame.pack(fill=tk.X, padx=20, pady=(4, 0))
        self.status_var = tk.StringVar(value="状态: 就绪")
        self.status_label = tk.Label(self.status_frame, textvariable=self.status_var, fg=self.ui_colors['primary'], bg=self._bg, font=("Arial", 10))
        self.status_label.pack(side=tk.LEFT)

        self.progress_text = tk.Label(self.status_frame, text="0%", font=("Arial", 9, "bold"), fg=self._fg, bg=self._bg)
        self.progress_text.pack(side=tk.RIGHT)

        self.progress_header = tk.Frame(self.right_scrollable_frame, bg=self._bg)
        self.progress_header.pack(fill=tk.X, padx=20, pady=(3, 0))
        tk.Label(self.progress_header, text="进度:", fg=self._fg, bg=self._bg, font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.step_label = tk.Label(self.progress_header, text="", fg=self.ui_colors['info'], bg=self._bg, font=("Arial", 9))
        self.step_label.pack(side=tk.LEFT, padx=(8, 0))
        self.item_progress_bar = ttk.Progressbar(self.right_scrollable_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.item_progress_bar.pack(fill=tk.X, padx=20, pady=(2, 5))

        self.refresh_yt_subtitle_mode_ui()
        self.root.update_idletasks()
        self.show_welcome_message()
        self.build_song_manager_tab(self.content_frames[10])
        self.build_ktv_selection_tab(self.content_frames[11])
        self.root.protocol("WM_DELETE_WINDOW", self._close_app_window)

    def _close_app_window(self):
        self._stop_ktv_playback()
        self.root.destroy()

    def convert_lyrics_language(self, lyrics_text, target_language):
        """转换歌词语言（简繁转换）target_language: "original", "traditional", "simplified" """
        if target_language == "original": return lyrics_text
        if str(self.common_lib_dir) not in sys.path: sys.path.insert(0, str(self.common_lib_dir))
        zhconv_available = False
        try:
            import zhconv; zhconv_available = True
        except ImportError:
            try:
                self.log("  📦 正在安装 zhconv 中文简繁转换套件...")
                self.log("     （这可能需要几秒钟，请稍候...）")
                self.update_status("正在安装 zhconv 套件...", "orange")
                result = subprocess.run([str(self.local_python), '-m', 'pip', 'install',
                     '--target', str(self.common_lib_dir), 'zhconv'], capture_output=True, text=True)
                if result.returncode == 0:
                    self.log("  ✅ zhconv 安装成功！")
                    try:
                        sys.modules.pop('zhconv', None)
                        if str(self.common_lib_dir) not in sys.path: sys.path.insert(0, str(self.common_lib_dir))
                        import zhconv; zhconv_available = True
                    except Exception: self.log("  ⚠️ 安装后仍无法汇入 zhconv")
                else:
                    self.log(f"  ⚠️ zhconv 安装失败: {result.stderr}")
            except Exception as e:
                self.log(f"  ⚠️ 安装 zhconv 时出错: {str(e)}")
        if zhconv_available:
            try:
                return zhconv.convert(lyrics_text, 'zh-tw' if target_language == "traditional" else 'zh-cn')
            except Exception as e: self.log(f"  ⚠️ zhconv 转换失败: {str(e)}")
        return lyrics_text

    def _get_cookie_opts(self, force_no_cookie=False):
        """根据使用者选择的浏览器，回传 yt-dlp 的 cookie 参数列表。"""
        if force_no_cookie or not hasattr(self, 'cookie_browser_var'):
            return []
        browser = self.cookie_browser_var.get()
        return [] if browser == "none" else ["--cookies-from-browser", browser]

    def _build_ytdlp_common_opts(self, js_runtime_opts=None, force_no_cookie=False):
        """建立 yt-dlp 通用参数（重试、FFmpeg、Cookie 等）"""
        opts = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
            "--retries", "10",
            "--fragment-retries", "10",
            "--extractor-retries", "5",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "0.5",
            "--sleep-interval", "3",
            # ── 2026 破解 YouTube 下載限制的關鍵參數 ──
            # web 優先以取得高畫質（1080p+），失敗再 fallback 到 mweb / android
            "--extractor-args", "youtube:player_client=web,mweb,android",
            "--compat-options", "no-youtube-unavailable-videos",
            "--user-agent", "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
            "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
            "--add-header", "Referer:https://www.youtube.com/",
            "--add-header", "Origin:https://www.youtube.com",
        ]
        if js_runtime_opts:
            opts.extend(js_runtime_opts)
        opts.extend(self._get_cookie_opts(force_no_cookie=force_no_cookie))
        # 若目錄下有 cookies.txt 也自動套用
        cookies_txt = self.app_dir / "cookies.txt"
        if not force_no_cookie and cookies_txt.exists():
            opts.extend(["--cookies", str(cookies_txt)])
        return opts

    def _log_ytdlp_error(self, line):
        """解析 yt-dlp 错误讯息并附上中文说明"""
        self.log(f"  ❌ {line}")
        el = line.lower()
        cookie_msg = "无法读取浏览器 Cookie，正在自动重试而不用 Cookie..."
        ratelimit_msg = "YouTube 封锁太多请求！\n     已自动开启重试和延迟机制，请稍候...\n     如果还是失败，请稍后再试或选择浏览器 Cookie！"
        hints = [
            ("this video is not available", "此影片无法存取！可能原因：\n     1. 影片是私人影片\n     2. 影片已被删除\n     3. 影片有地区锁定（Geo-block）\n     4. 请确认您使用的是「单一影片」连结，不是播放列表或电台连结！"),
            ("video unavailable",           "影片无法使用！"),
            ("age restricted",              "此影片有年龄限制，建议使用 Cookie 选项！"),
            ("sign in to confirm",          "需要登入确认年龄，请在设定中选择浏览器 Cookie！"),
            ("cookie database",             cookie_msg), ("could not copy chrome", cookie_msg),
            ("429",                         ratelimit_msg), ("too many requests", ratelimit_msg),
        ]
        for kw, hint in hints:
            if kw in el:
                self.log(f"  💡 中文说明：{hint}"); break

    def _get_ytdlp_js_runtime_opts(self):
        """自动侦测 yt-dlp 可用的 JS runtime，提升 YouTube 资讯与字幕撷取成功率。
        优先使用本地 yt-dlp/deno.exe（随环境修复自动安装），其次搜寻系统 PATH。"""
        if self._yt_js_runtime_cache is not None:
            return list(self._yt_js_runtime_cache)

        # 優先使用本地免安裝版 deno（與 yt-dlp 同目錄，由環境修復自動下載）
        local_deno = self.ytdlp_dir / ("deno.exe" if sys.platform == "win32" else "deno")

        quickjs_path = shutil.which("quickjs") or shutil.which("qjs")
        runtime_candidates = [
            ("deno", str(local_deno) if local_deno.exists() else shutil.which("deno")),
            ("node", shutil.which("node")),
            ("bun", shutil.which("bun")),
            *([("quickjs", quickjs_path)] if quickjs_path else []),
        ]

        for runtime_name, runtime_path in runtime_candidates:
            if runtime_path:
                self._yt_js_runtime_cache = ["--js-runtimes", f"{runtime_name}:{runtime_path}"]
                if not self._yt_js_runtime_notice_shown:
                    self.log(f"  ℹ️ 已启用 yt-dlp JavaScript runtime：{runtime_name}")
                    self._yt_js_runtime_notice_shown = True
                return list(self._yt_js_runtime_cache)

        self._yt_js_runtime_cache = []
        if not self._yt_js_runtime_notice_shown:
            self.log("  ⚠️ 未侦测到 deno/node/bun/quickjs；YouTube 字幕或格式清单可能不完整。")
            self._yt_js_runtime_notice_shown = True
        return []
    def _get_ytdlp_command_base(self):
        """回传可实际启动 yt-dlp 的命令前缀"""
        for p in [self.ytdlp_dir/"yt-dlp.exe", self.py_dir/"Scripts"/"yt-dlp.exe", self.lib_dir/"bin"/"yt-dlp.exe"]:
            if p.exists(): return [str(p)]
        for p in [self.ytdlp_dir/"yt_dlp"/"__main__.py", self.lib_dir/"yt_dlp"/"__main__.py"]:
            if p.exists(): return [str(self.local_python), str(p)]
        return [str(self.local_python), "-m", "yt_dlp"]

    def refresh_start_button_text(self):
        """依当前分页与字幕模式更新主按钮文字。"""
        vfmt = self.video_format_var.get().upper() if hasattr(self, 'video_format_var') else "MKV"
        if self.current_tab_index in (5, 6, 7, 8):
            self.btn_frame.pack_forget()
        else:
            self.btn_frame.pack(pady=(0, 3))
            _btn_cfg = {
                0: (f"一键制作 {vfmt} 伴唱带", 'primary'),
                1: ("立即下载 YouTube 档案",    'info'),
                2: (f"制作本地影片 KTV ({vfmt})", 'primary'),
                3: ("开始批量分离音档",          'success'),
                4: ("开始辨识歌词",             'info'),
            }
            if self.current_tab_index in _btn_cfg:
                _t, _c = _btn_cfg[self.current_tab_index]
                self.start_btn.config(text=_t, bg=self.ui_colors[_c])

    def refresh_yt_subtitle_mode_ui(self):
        """依分页与勾选状态显示字幕模式列。"""
        current_tab = self.current_tab_index
        if current_tab == 0 and self.yt_cc_var.get():
            self.yt_subtitle_mode_row.pack(fill=tk.X, pady=2)
        else:
            self.yt_subtitle_mode_row.pack_forget()
        if hasattr(self, "start_btn"):
            self.refresh_start_button_text()

    @staticmethod
    def extract_youtube_video_id(url):
        """从常见 YouTube 网址格式中撷取影片 ID。"""
        match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([0-9A-Za-z_-]{11})", url)
        return match.group(1) if match else None

    @staticmethod
    def clean_youtube_url(url):
        """
        清洗 YouTube 网址：保留纯影片连结，移除播放清单/电台等多余参数。
        例如：
          https://www.youtube.com/watch?v=FvTmSB4tDZg&list=RD...&start_radio=1
          → https://www.youtube.com/watch?v=FvTmSB4tDZg
        对 youtu.be 短网址、/shorts/、/embed/ 也同样处理。
        """
        import urllib.parse
        url = url.strip()
        try:
            parsed = urllib.parse.urlparse(url)
            # youtu.be 短網址：直接取路徑最後一段當 video_id
            if parsed.netloc in ("youtu.be", "www.youtu.be"):
                video_id = parsed.path.lstrip("/").split("/")[0]
                if video_id:
                    return f"https://www.youtube.com/watch?v={video_id}"
                return url
            # /shorts/ 或 /embed/ 路徑：保留 video_id，捨棄多餘參數
            path_lower = parsed.path.lower()
            if "/shorts/" in path_lower or "/embed/" in path_lower:
                match = re.search(r"/(?:shorts|embed)/([0-9A-Za-z_-]{11})", parsed.path, re.IGNORECASE)
                if match:
                    return f"https://www.youtube.com/watch?v={match.group(1)}"
                return url
            # 標準 watch?v= 網址：只保留 v= 參數
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
            if "v" in qs:
                clean_query = urllib.parse.urlencode({"v": qs["v"][0]})
                clean = parsed._replace(query=clean_query, fragment="")
                return urllib.parse.urlunparse(clean)
        except Exception:
            pass
        return url

    def _rename_subtitle_to(self, subtitle_file, desired_path):
        """将字幕档移动到 desired_path（共用核心），回传最终路径字串。"""
        try:
            subtitle_path = Path(subtitle_file)
            if not subtitle_path.exists(): return subtitle_file
            if subtitle_path.resolve() == desired_path.resolve(): return str(subtitle_path)
            with suppress(Exception): desired_path.unlink()
            shutil.move(str(subtitle_path), str(desired_path))
            self.log(f"  📝 字幕档已对齐命名：{desired_path.name}")
            return str(desired_path)
        except Exception as e:
            self.log(f"  ⚠️ 字幕档重新命名失败，保留原档名：{str(e)}")
            return subtitle_file

    def align_subtitle_filename(self, subtitle_file, target_media_file):
        """将字幕档改名成与目标媒体档完全同主档名，副档名固定为 .srt。"""
        if not Path(target_media_file).exists(): return subtitle_file
        return self._rename_subtitle_to(subtitle_file, Path(target_media_file).parent / f"{Path(target_media_file).stem}.srt")

    def switch_tab(self, index):
        """切换到指定的分页"""
        if self.current_tab_index == index: return
        self.content_frames[self.current_tab_index].grid_remove()
        self.current_tab_index = index
        fill_content = index in (7, 10, 11)
        self.right_main_container.pack_configure(fill=tk.BOTH if fill_content else tk.X, expand=fill_content)
        self.tab_container.pack_configure(fill=tk.BOTH if fill_content else tk.X, expand=fill_content)
        self.content_frames[index].grid(row=0, column=0, sticky="nsew" if fill_content else "new", padx=3, pady=2)
        if index in (10, 11):
            self.btn_frame.pack_forget()
            self.status_frame.pack_forget()
            self.progress_header.pack_forget()
            self.item_progress_bar.pack_forget()
        else:
            self.btn_frame.pack(pady=(0, 3))
            self.status_frame.pack(fill=tk.X, padx=20, pady=(4, 0))
            self.progress_header.pack(fill=tk.X, padx=20, pady=(3, 0))
            self.item_progress_bar.pack(fill=tk.X, padx=20, pady=(2, 5))
        if index == 6: self.check_components(prompt=False)
        self.root.update_idletasks()
        self.update_tab_buttons(index)
        self._on_tab_changed_logic(index)

    def update_tab_buttons(self, active_index):
        """更新按钮的视觉样式"""
        for i, btn in enumerate(self.tab_buttons):
            is_active = (i == active_index)
            btn.configure(bg=self.ui_colors['primary'] if is_active else self._bg,
                          fg='white' if is_active else self._fg,
                          relief='sunken' if is_active else 'flat')

    def _on_tab_changed_logic(self, current_tab):
        """原来的 on_tab_changed 逻辑，用于处理核心设定显示/隐藏等"""
        if current_tab in (5, 6, 7, 8, 9, 10, 11):
            self.settings_frame.pack_forget()
        else:
            self.settings_frame.pack(fill=tk.X, pady=(0, 3))

            hide_ai = current_tab in (1, 4)
            for row in [self.output_format_row, self.ktv_row, self.track_row, self.mix_row,
                        self.lyrics_row, self.lyrics_options_row, self.lyrics_stable_ts_row]:
                if hide_ai: row.pack_forget()
                else: row.pack(fill=tk.X, pady=2)
            for row in [self.opt_row, self.model_row]:
                if current_tab == 1: row.pack_forget()
                else: row.pack(fill=tk.X, pady=2)

            if current_tab in (0, 2):
                self.extra_video_row.pack(fill=tk.X, pady=2)
                self.force_1080p_chk.pack(side=tk.LEFT)
                self.yt_cc_chk.pack_forget()
                if current_tab == 0:
                    self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))
                else:
                    self.yt_cc_var.set(False)
            else:
                self.extra_video_row.pack_forget()
                self.yt_cc_var.set(False)
            self.refresh_yt_subtitle_mode_ui()

        self.refresh_start_button_text()

    def update_vocal_mix_label(self):
        """更新导唱混合比例显示文字。"""
        v = round(self.vocal_volume_var.get())
        i = round(self.instrumental_volume_var.get())
        self.vocal_mix_label_var.set(f"人声 {v}% / 伴奏 {i}%")

    def on_start_click(self):
        """智能启动按钮：根据当前分页决定执行对应功能"""
        _actions = {0: self.start_yt_process, 1: self.start_pure_download,
                    2: self.start_local_v_process, 3: self.start_separation,
                    4: self.start_recognize_lyrics, 5: self.start_merge_subtitle_video}
        if self.current_tab_index in _actions:
            _actions[self.current_tab_index]()

    def browse_local_video(self):
        file_paths = filedialog.askopenfilenames(title="选择影片档案", filetypes=[("影片档案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有档案", "*.*")])
        if file_paths:
            self._add_files_to_listbox(file_paths, self.v_list, self.v_listbox)

    def _add_files_to_listbox(self, file_paths, file_list, listbox):
        """通用：将档案路径加入 file_list 与 listbox（去重）"""
        for fp in file_paths:
            fp_abs = str(Path(fp).absolute())
            if fp_abs not in file_list:
                file_list.append(fp_abs)
                listbox.insert(tk.END, os.path.basename(fp_abs))

    def browse_local_v_folder(self):
        folder_path = filedialog.askdirectory(title="选择影片资料夹")
        if folder_path:
            for ext in ["mp4", "mkv", "avi", "mov", "wmv", "webm"]:
                self._add_files_to_listbox(Path(folder_path).glob(f"*.{ext}"), self.v_list, self.v_listbox)

    def _update_listbox(self, listbox, file_list, *, clear=False):
        """通用：清空或移除选取项目"""
        if clear:
            file_list.clear()
            listbox.delete(0, tk.END)
            return
        for idx in reversed(listbox.curselection()):
            file_list.pop(idx)
            listbox.delete(idx)

    def remove_selected_v(self): self._update_listbox(self.v_listbox, self.v_list)
    def clear_v_list(self): self._update_listbox(self.v_listbox, self.v_list, clear=True)

    def _rename_output_file(self, src_path_str, dst_path, label="档案"):
        """通用：安全重新命名输出档案，回传最终路径（字串）。"""
        try:
            src = Path(src_path_str)
            self.log(f"  📝 处理 {label}: {src.name} → {dst_path.name}")
            if src.exists():
                if dst_path.exists(): self.log("  📝 删除旧的目标档案"); dst_path.unlink()
                shutil.move(str(src), str(dst_path))
                self.log(f"  ✅ {label} 已重新命名为: {dst_path.name}")
                return str(dst_path)
        except Exception as e:
            self._log_exception(f"  ❌ {label} 重新命名失败: ", e)
        return src_path_str

    def start_local_v_process(self):
        if not self.v_list:
            messagebox.showwarning("警告", "请先加入影片档案！")
            return
        self._begin_processing("正在进行批次影片处理...", self.local_v_batch_process)

    def local_v_batch_process(self):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            enable_lyrics = self.enable_lyrics_recognition_var.get()

            total = len(self.v_list)
            for i, video_path in enumerate(self.v_list):
                if not self.is_processing or self.cancel_event.is_set():
                    self.log("🛑 批次处理已中止。")
                    break

                video_stem = Path(video_path).stem
                self.log(f"\n--- 正在处理 ({i+1}/{total}): {os.path.basename(video_path)} ---")

                self.v_listbox.selection_clear(0, tk.END)
                self.v_listbox.selection_set(i)
                self.v_listbox.see(i)

                temp_audio = Path(output_dir) / f"{video_stem}_temp_audio.mp3"

                progress_base = int((i / total) * 100)
                progress_step = int(100 / total)

                self.update_progress(progress_base + int(progress_step * 0.1), f"正在撷取音讯 ({i+1}/{total})")
                self.log("  > 正在从影片撷取音讯...")
                extract_cmd = [str(self.ffmpeg_exe), "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-ab", "320k", str(temp_audio)]
                subprocess.run(extract_cmd, check=True, creationflags=self.subp_flags)

                self.update_progress(progress_base + int(progress_step * 0.3), f"正在 AI 分离 ({i+1}/{total})")
                success = self.run_audio_separator(str(temp_audio), output_dir)

                if success:
                    srt_subtitle = json_subtitle = None
                    if enable_lyrics:
                        srt_subtitle, json_subtitle = self._recognize_and_rename_lyrics(temp_audio, output_dir, video_stem)

                    self.log("  > 正在整理产出档案...")
                    voc_file, inst_file = self.consolidate_stems(str(temp_audio), video_path, output_dir)

                    if voc_file and inst_file:
                        vfmt = self.video_format_var.get()
                        self.update_progress(progress_base + int(progress_step * 0.8), f"正在合成 {vfmt.upper()} ({i+1}/{total})")
                        output_file = Path(output_dir) / f"{video_stem}_KTV.{vfmt}"
                        mkv_success = self.synthesize_mkv(video_path, voc_file, inst_file, str(output_file), subtitle_file=srt_subtitle)

                        if mkv_success:
                            self.log(f"✅ 成功生成 {vfmt.upper()}: {output_file.name}")
                        else:
                            self.log(f"❌ {video_stem} MKV 合成失败。")
                    else:
                        self.log(f"❌ {video_stem} 找不到分离后的必要档案。")
                else:
                    self.log(f"❌ {video_stem} 音讯分离失败。")

            self.update_progress(100, "批次处理完成")
            self.log("\n✨ 所有影片批次处理任务已结束！")
            self._show_done_and_open("完成", f"已完成 {total} 个影片的处理！\n档案已储存至: {output_dir}", output_dir)

        except Exception as e:
            self.log(f"❌ 批次处理中出错: {str(e)}")
        finally:
            self.finish_processing()

    def start_pure_download(self):
        """纯下载逻辑：不进行 AI 分离与合成"""
        url = self.yt_dl_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入 YouTube 网址！")
            return
        self._begin_processing("正在下载 YouTube 档案...", self.pure_download_process, url)

    def pure_download_process(self, url):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            dl_type     = self.dl_type_var.get()
            quality     = self.dl_quality_var.get()
            force_upscale_output_1080p = self.force_1080p_var.get()

            self.log(f"🚀 开始下载任务 (格式: {str(dl_type).upper()}，画质: {quality})...")
            result = self.download_youtube(url, output_dir, mode=dl_type, download_subtitles=False, quality=quality)

            # 解析回傳值（保持原本行為：both→(mp4,mp3)、mp4→mp4、mp3→mp3；失敗→None）
            video_file = audio_file = None
            if result:
                if dl_type == "both":
                    video_file, audio_file = result if isinstance(result, tuple) else (None, None)
                elif dl_type == "mp4":
                    video_file = result
                elif dl_type == "mp3":
                    audio_file = result

            # 「強制等比輸出 1080p」：純下載分頁也套用（把下載到的 MP4 另外輸出一份 1080p）
            if force_upscale_output_1080p:
                if dl_type == "mp3":
                    self.log("⚠️ 你目前选的是「仅 MP3」，没有下载影片，因此无法执行 1080p 放大。请改选「仅 MP4」或「MP3 + MP4」。")
                elif video_file and os.path.exists(str(video_file)):
                    self.log("🖼️ 已勾选「强制等比输出 1080p」：将对下载的 MP4 进行等比缩放 + 补黑边，并覆盖原档。")
                    video_file = self.upscale_video_to_1080p(str(video_file), replace_original=True)

            if dl_type in ["both", "mp4"] and not video_file:
                self.log("  ❌ MP4 下载失败，请查看上方日志。")
            if dl_type in ["both", "mp3"] and not audio_file:
                self.log("  ❌ MP3 下载失败，请查看上方日志。")

            self.log("\n✅ 所有任务已全部完成！")
            self._show_done_and_open("完成", "YouTube 下载成功！", output_dir if os.path.exists(output_dir) else None)
        except Exception as e:
            self.log(f"❌ 下载过程中出错: {e}")
        finally:
            self.finish_processing()

    def _quick_paste_yt_url(self, url_var, log_prefix=""):
        """通用：点击输入框时自动贴上剪贴簿中的 YouTube 网址"""
        with suppress(Exception):
            clipboard = self.root.clipboard_get().strip()
            if not clipboard or clipboard == url_var.get().strip():
                return
            if "youtube.com/" not in clipboard and "youtu.be/" not in clipboard:
                return
            url_var.set(clipboard)
            self.log(f"📋 {log_prefix}已从剪贴簿贴上网址: {clipboard}")
            if "list=" in clipboard and "watch?v=" not in clipboard and "/shorts/" not in clipboard:
                self.log("⚠️ 侦测到播放清单连结，本工具仅会下载第一支影片（已加入 --no-playlist）。")

    def quick_paste_url(self, event):  self._quick_paste_yt_url(self.yt_url_var)

    def quick_paste_dl_url(self, event):  self._quick_paste_yt_url(self.yt_dl_url_var, "[下载分页] ")

    def show_welcome_message(self):
        welcome_text = (
            "==================================================\n"
            " 🎵 欢迎使用 MP3 人声分离 & YouTube 下载/KTV 制作工具\n"
            "==================================================\n"
            "【快速入门】\n"
            "1. YouTube 转 MKV：贴上网址，点击「一键制作」即可自动完成。\n"
            "2. 本地分离：切换至分页，加入 MP3 档案，点击「开始分离」。\n"
            "--------------------------------------------------\n"
            "💡 提示：点击 YouTube 网址框可自动贴上剪贴簿内容。\n"
            "💡 建议：初次使用请确保环境已「初始化/修复」完成。\n"
            "==================================================\n"
            "🚀 系统就绪，请选择功能分页开始使用。\n"
        )
        self.log_area.insert(tk.END, welcome_text + "\n")
        self.log_area.see(tk.END)

    def log(self, message):  self.root.after(0, lambda: self._safe_log(message))

    def _safe_log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        if self.is_processing: self.status_var.set(f"状态: {message.strip()}")

    def _log_exception(self, prefix, e):
        """记录错误讯息和 traceback（常用的 2 行合 1 行）"""
        sep = "" if prefix.endswith(": ") or prefix.endswith(":") else ": "
        self.log(f"{prefix}{sep}{str(e)}")
        self.log(f"   {traceback.format_exc()}")

    def _require_file(self, path, empty_msg, missing_msg="找不到档案！"):
        """验证路径非空且档案存在，否则显示警告/错误并回传 False"""
        if not path:
            messagebox.showwarning("警告", empty_msg); return False
        if not os.path.exists(path):
            messagebox.showerror("错误", missing_msg); return False
        return True

    def update_progress(self, item_percent=None, text=None, step_text=None):
        def _do():
            if item_percent is not None:
                try:
                    pct = float(item_percent)
                    self.item_progress_bar["value"] = pct
                    self.progress_text.config(text=f"{pct:.0f}%")
                except (ValueError, TypeError): pass
            if text: self.status_var.set(f"状态: {text}")
            if step_text is not None: self.step_label.config(text=step_text)
            elif item_percent is not None and float(item_percent) >= 100: self.step_label.config(text="")
        self.root.after(0, _do)

    def browse_file(self):
        filenames = filedialog.askopenfilenames(filetypes=[("Audio files", "*.mp3 *.wav *.flac *.m4a"), ("All files", "*.*")])
        if filenames:
            self._add_files_to_listbox(filenames, self.file_list, self.file_listbox)

    def remove_selected_file(self): self._update_listbox(self.file_listbox, self.file_list)

    def clear_files(self):  self._update_listbox(self.file_listbox, self.file_list, clear=True)

    def load_config(self):
        """载入设定档，若不存在则建立预设设定"""
        default_config = {
            "output_dir": str(self.app_dir / "output"),
            "use_stable_ts": True,
            "whisper_zh": dict(self._DEFAULT_WHISPER_ZH),
            "whisper_en": dict(self._DEFAULT_WHISPER_EN)
        }

        def _deep_merge(base, override):
            """将 override 合并进 base；dict 类型递回补齐缺少的 key，其余直接用 override 的值"""
            result = dict(base)
            for key, value in override.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = _deep_merge(result[key], value)
                else:
                    result[key] = value
            return result

        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # 深層合併：以 default 為底，把存檔的值蓋上去，dict 類型遞迴補齊缺少的 key
                self.config = _deep_merge(default_config, saved)
                # 若合併後與存檔不同（例如版本更新新增了新設定欄位），立即寫回硬碟。
                # 確保異常關機時新欄位也已持久化，避免下次啟動再重新補齊的循環。
                if self.config != saved:
                    self.save_config()
            except Exception as e:
                self.log(f"⚠️ 读取设定档失败，使用预设设定: {str(e)}")
                self.config = default_config
                self.save_config()
        else:
            self.config = default_config
            self.save_config()

    def update_ktv_color_previews(self):
        """更新 KTV 字幕颜色预览"""
        try:
            if hasattr(self, 'ktv_mode_hint_label') and hasattr(self, 'ktv_color_mode_var'):
                mode = self.ktv_color_mode_var.get()
                self.ktv_mode_hint_label.config(text=(
                    "  💡 滑动渐变：使用 \\kf tag，颜色由左至右平滑扫过，视觉效果更流畅"
                    if mode == "slide" else
                    "  💡 逐字变色：使用 \\k tag，每个字唱完后瞬间切换颜色"
                ))

            for _attr, _var in [
                ('unplayed_color_preview',      'ktv_unplayed_color_var'),
                ('played_color_preview',        'ktv_played_color_var'),
                ('border_color_preview',        'ktv_border_color_var'),
                ('played_border_color_preview', 'ktv_played_border_color_var'),
            ]:
                if hasattr(self, _attr) and hasattr(self, _var):
                    getattr(self, _attr).config(bg=getattr(self, _var).get())

            if hasattr(self, 'ktv_preview_canvas'):
                canvas = self.ktv_preview_canvas
                canvas.delete("all")

                canvas.create_rectangle(0, 0, 760, 80, fill="#333333", outline="")

                lyrics_text = "哥哥爸爸真伟大"

                selected_font = getattr(self, 'ktv_font_var', tk.StringVar(value="微软正黑体")).get()
                font = (selected_font, 28, "bold")

                full_unsung_id = canvas.create_text(380, 40, text=lyrics_text, font=font, fill=self.ktv_unplayed_color_var.get(), anchor=tk.CENTER)
                full_bbox = canvas.bbox(full_unsung_id)

                if full_bbox:
                    x0, y0, x1, y1 = full_bbox
                    canvas.create_rectangle(x0-3, y0-3, x1+3, y1+3, outline=self.ktv_border_color_var.get(), width=3, fill="")
                    mid_x = x0 + (x1 - x0) // 2
                    canvas.create_rectangle(x0, y0, mid_x, y1, outline="", tags="clip")
                    canvas.create_text(380, 40, text=lyrics_text, font=font, fill=self.ktv_played_color_var.get(), anchor=tk.CENTER, tags=("sung",))
                    canvas.create_rectangle(mid_x, y0, x1, y1, fill="#333333", outline="")
                    canvas.create_text(380, 40, text=lyrics_text, font=font, fill=self.ktv_unplayed_color_var.get(), anchor=tk.CENTER)
                    canvas.create_rectangle(x0, y0, mid_x, y1, fill="#333333", outline="")

                    unplayed_border = self.ktv_border_color_var.get()
                    played_border = self.ktv_played_border_color_var.get() if hasattr(self, 'ktv_played_border_color_var') else unplayed_border

                    x1, y1 = full_bbox[0]-3, full_bbox[1]-3
                    x2, y2 = full_bbox[2]+3, full_bbox[3]+3
                    mid = x1 + (x2 - x1) / 2

                    canvas.create_rectangle(x1, y1, x2, y2, outline=unplayed_border, width=3, fill="")

                    if played_border and played_border != unplayed_border:
                        canvas.create_line(x1, y1, mid, y1, fill=played_border, width=3)
                        canvas.create_line(x1, y2, mid, y2, fill=played_border, width=3)
                        canvas.create_line(x1, y1, x1, y2, fill=played_border, width=3)

        except Exception:
            pass

    def format_time(self, seconds):
        try: s = float(seconds); return f"{int(s//60):02d}:{int(s%60):02d}"
        except: return "00:00"

    def _run_ffmpeg_info(self, video_path):
        """执行 ffmpeg -i 并回传 stderr 字串，失败回传 ''"""
        if not self.ffmpeg_exe.exists(): return ""
        try:
            return subprocess.run([str(self.ffmpeg_exe), "-i", str(video_path)],
                                  capture_output=True, creationflags=self.subp_flags).stderr.decode('utf-8', errors='replace')
        except Exception: return ""

    def get_video_duration(self, video_path):
        """使用 FFmpeg 取得影片长度"""
        try:
            m = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})", self._run_ffmpeg_info(video_path))
            if m: return int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/100
        except Exception: pass
        return 0

    def _get_video_size(self, video_path):
        """使用 FFmpeg 取得影片解析度，回传 (width, height)；失败回传 (None, None)"""
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", self._run_ffmpeg_info(video_path))
        return (int(m.group(1)), int(m.group(2))) if m else (None, None)

    def parse_lyrics(self, subtitle_path):
        """解析歌词档案（支援 SRT 和 JSON）"""
        lyrics = []
        try:
            if not subtitle_path or not os.path.exists(subtitle_path):
                return lyrics

            if subtitle_path.lower().endswith('.json'):
                with open(subtitle_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                lyrics = data if isinstance(data, list) else (data.get('lyrics') or data.get('segments') or [])
            elif subtitle_path.lower().endswith('.srt'):
                with open(subtitle_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                srt_pattern = re.compile(r'(\d+)\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\n(.*?)(?=\n\n|\Z)', re.DOTALL)
                for match in srt_pattern.finditer(content):
                    g = match.group
                    start_time = int(g(2))*3600 + int(g(3))*60 + int(g(4)) + int(g(5))/1000
                    end_time   = int(g(6))*3600 + int(g(7))*60 + int(g(8)) + int(g(9))/1000
                    text = g(10).strip()
                    if text:
                        lyrics.append({'start': start_time, 'end': end_time, 'text': text})
        except Exception:
            pass
        return lyrics

    def reload_player(self):
        """重新载入影片和歌词"""
        try:
            if self.is_playing: self.toggle_play()
            self._kill_ffplay()
            self.current_time = 0
            self.lyrics_data  = []
            video_path    = self.merge_video_path_var.get().strip()
            subtitle_path = self.merge_subtitle_path_var.get().strip()

            self.video_canvas.delete("all")
            self.video_canvas.create_text(320, 140, text="请选择影片或图片", fill="#666666", font=("Arial", 14))
            self.video_canvas.create_text(320, 170, text="影片将在独立视窗播放", fill="#888888", font=("Arial", 10))

            if video_path and os.path.exists(video_path):
                is_img = self._is_image_file(video_path)
                self.player_video_path = None if is_img else video_path
                self.video_duration = 20 if is_img else self.get_video_duration(video_path)
                if is_img or self.video_duration > 0:
                    self.timeline_scale.config(to=self.video_duration)
                    c = self.video_canvas; c.delete("all")
                    c.create_rectangle(0, 0, 640, 320, fill="#111111" if is_img else "#000000", outline="")
                    c.create_text(320, 130, text=os.path.basename(video_path), fill="#FFFFFF", font=("Arial", 11))
                    if is_img:
                        c.create_text(320, 160, text="(图片模式：不支援播放预览)", fill="#AAAAAA", font=("Arial", 9))
                        c.create_text(320, 190, text="可直接按「开始合并」生成影片", fill="#777777", font=("Arial", 9))
                    else:
                        c.create_text(320, 160, text="(点击播放开始预览)", fill="#888888", font=("Arial", 9))
                        c.create_text(320, 190, text="影片将在独立视窗播放", fill="#666666", font=("Arial", 9))

            if subtitle_path and os.path.exists(subtitle_path):
                self.player_subtitle_path = subtitle_path
                self.lyrics_data = self.parse_lyrics(subtitle_path)
                if self.lyrics_data:
                    self.log(f"✅ 已载入 {len(self.lyrics_data)} 句歌词")
            else:
                self.lyrics_data = [
                    {'start': 0, 'end': 5, 'text': '哥哥爸爸真伟大'},
                    {'start': 5, 'end': 10, 'text': '荣誉都属于他'},
                    {'start': 10, 'end': 15, 'text': '为国家去打仗'},
                    {'start': 15, 'end': 20, 'text': '我们都爱他'}
                ]
                if self.video_duration <= 0:
                    self.video_duration = 20
                    self.timeline_scale.config(to=self.video_duration)

            self.update_time_display()
            self.display_lyrics_at_time(0)

        except Exception:
            pass

    def toggle_play(self):
        """切换播放/暂停"""
        if not self.player_video_path or not os.path.exists(self.player_video_path):
            # 圖片模式/未選檔：不播放
            messagebox.showwarning("提示", "目前是图片模式或尚未选择可播放的影片。")
            return

        if self.is_playing:
            self.is_playing = False
            self.play_btn.config(text="▶ 播放", bg=self.ui_colors['success'])
            self.stop_playback()
        else:
            self.is_playing = True
            self.play_btn.config(text="⏸ 暂停", bg=self.ui_colors['warning'])
            self.start_playback()

    def start_playback(self):
        """开始播放 - 使用 ffplay"""
        ffplay_exe = self.bin_dir / "ffplay.exe"
        if self.player_after_id:
            self.root.after_cancel(self.player_after_id)
        if not ffplay_exe.exists():
            messagebox.showwarning("提示", "找不到 ffplay.exe，请确认 FFmpeg 已正确安装！\n(我们会继续显示歌词预览)")
        else:
            try:
                self._kill_ffplay()
                self.ffplay_process = subprocess.Popen(
                    [str(ffplay_exe), "-ss", str(self.current_time), "-autoexit", "-window_title", "KTV 影片预览", str(self.player_video_path)],
                    creationflags=self.subp_flags)
            except Exception as e:
                messagebox.showerror("错误", f"无法启动播放器：{str(e)}\n(我们会继续显示歌词预览)")
        self.update_playback()

    def _kill_ffplay(self):
        if self.ffplay_process:
            with suppress(Exception): self.ffplay_process.terminate()
            self.ffplay_process = None

    def stop_playback(self):
        self._kill_ffplay()
        if self.player_after_id:
            self.root.after_cancel(self.player_after_id); self.player_after_id = None

    def update_playback(self):
        if not self.is_playing: return
        self.current_time += 0.1
        if self.current_time >= self.video_duration:
            self.current_time = 0
            self.toggle_play()
            return
        self.update_time_display()
        self.timeline_scale.set(self.current_time)
        self.display_lyrics_at_time(self.current_time)
        self.player_after_id = self.root.after(100, self.update_playback)

    def update_time_display(self):
        self.time_label_var.set(f"{self.format_time(self.current_time)} / {self.format_time(self.video_duration)}")

    def _timeline_update(self):
        self.current_time = self.timeline_scale.get()
        self.update_time_display(); self.display_lyrics_at_time(self.current_time)

    def on_timeline_seek(self, event):
        self._timeline_update()
        if self.is_playing: self.restart_player_at_time(self.current_time)

    def on_timeline_drag(self, event): self._timeline_update()

    def restart_player_at_time(self, new_time):
        with suppress(Exception):
            ffplay_exe = self.bin_dir / "ffplay.exe"
            if not ffplay_exe.exists(): return
            self._kill_ffplay()
            self.ffplay_process = subprocess.Popen(
                [str(ffplay_exe), "-ss", str(new_time), "-autoexit", "-window_title", "KTV 影片预览", str(self.player_video_path)],
                creationflags=self.subp_flags)

    def display_lyrics_at_time(self, current_time):
        """根据时间显示对应歌词"""
        try:
            canvas = self.lyrics_display_canvas
            canvas.delete("all")

            canvas.create_rectangle(0, 0, 640, 70, fill="#333333", outline="")

            current_lyric = None
            for lyric in self.lyrics_data:
                s = float(lyric.get('start', 0))
                e = float(lyric.get('end', 0))
                if s <= current_time <= e:
                    current_lyric = lyric; break
                if s <= current_time:
                    current_lyric = lyric  # 持續更新到最後一個 start ≤ current_time

            if current_lyric:
                text = current_lyric.get('text', '')

                def _gv(attr, default):
                    v = getattr(self, attr, None)
                    return v.get() if v else default
                selected_font  = _gv('ktv_font_var',          '微软正黑体')
                unplayed_color = _gv('ktv_unplayed_color_var', '#FFFFFF')
                played_color   = _gv('ktv_played_color_var',   '#0000FF')

                font = (selected_font, 24, "bold")

                start_time = float(current_lyric.get('start', 0))
                end_time = float(current_lyric.get('end', 0))
                duration = end_time - start_time if end_time > start_time else 1
                progress = min(max((current_time - start_time) / duration, 0), 1)

                display_color = played_color if progress >= 0.5 else unplayed_color

                canvas.create_text(320, 35, text=text, font=font, fill=display_color, anchor=tk.CENTER)
            else:
                canvas.create_text(320, 35, text="请选择歌词档案", fill="#666666", font=("Arial", 12))

        except Exception as e:
            self.log(f"⚠️ 显示歌词时错误: {e}")

    def save_config(self):
        """储存设定到 JSON 档案"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.log(f"⚠️ 储存设定档失败: {e}")

    def setup_whisper_settings_tab(self, parent):
        """建立 Whisper 辨识设定签页"""

        C = self.ui_colors

        DEFAULTS_ZH = self._DEFAULT_WHISPER_ZH
        DEFAULTS_EN = self._DEFAULT_WHISPER_EN

        cur_zh = self.config.get("whisper_zh", {})
        cur_en = self.config.get("whisper_en", {})

        _COMMON_PARAMS = [
            ("no_speech_threshold",        "静音略过灵敏度",       "↑ 调高：轻声演唱常被跳过\n↓ 调低：静音段落有杂音残留",  0.1, 0.95, 0.05, True),
            ("compression_ratio_threshold","重复幻觉过滤强度",      "↑ 调高：副歌歌词常被漏掉\n↓ 调低：出现大量重复幻觉歌词",  1.0, 3.0,  0.1,  True),
            ("beam_size",                  "辨识精准度（候选数量）", "↑ 调高：辨识准确但速度慢\n↓ 调低：电脑慢或辨识很久",       1,   10,   1,    False),
            ("nsp_skip",                   "静音略过门槛",          "↑ 调高：轻声段落常被跳过\n↓ 调低：静音段落有残留",        0.1, 0.9,  0.05, True),
        ]
        ZH_PARAMS = _COMMON_PARAMS + [("logprob_skip", "辨识信心下限", "↓ 调低（如 -2.0）：正常歌词常被误删\n↑ 调高（如 -1.0）：很多不确定歌词残留", -2.0, -0.1, 0.1, True)]
        EN_PARAMS = _COMMON_PARAMS + [("logprob_skip", "辨识信心下限", "↓ 调低（如 -1.0）：正常歌词常被误删\n↑ 调高（如 -0.5）：很多不确定歌词残留", -2.0, -0.1, 0.1, True)]

        self._wsp_vars_zh = {}
        self._wsp_vars_en = {}

        canvas = tk.Canvas(parent, bg=C['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=C['bg'])
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        PAD = {"padx": 16, "pady": 3}

        tk.Label(scroll_frame, text="⚙️  Whisper 辨识参数设定", font=("Arial", 13, "bold"), fg=C['primary'], bg=C['bg']).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(scroll_frame, text="调整后点「储存设定」即生效，下次辨识自动套用。", font=("Arial", 9), fg="gray", bg=C['bg']).pack(anchor="w", padx=16, pady=(0, 6))

        # ── 小白提示框 ──────────────────────────────────────────
        tip_box = tk.Frame(scroll_frame, bg="#EFF6FF", relief="solid", bd=1)
        tip_box.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(tip_box, text="💡  一般使用者：直接点右下角「还原预设值」即可，不需要修改任何设定。",
                 font=("Arial", 9, "bold"), fg=C['primary'], bg="#EFF6FF", anchor="w").pack(anchor="w", padx=10, pady=(6, 2))
        tk.Label(tip_box, text="如果辨识结果有问题，才需要参考下方的「症状对照表」微调。",
                 font=("Arial", 8), fg="#555", bg="#EFF6FF", anchor="w").pack(anchor="w", padx=10, pady=(0, 6))

        # ── 症狀對照表 ───────────────────────────────────────────
        sym_box = tk.Frame(scroll_frame, bg="#F8FAFC", relief="solid", bd=1)
        sym_box.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(sym_box, text="🔍  遇到问题时才看这里", font=("Arial", 9, "bold"), fg=C['fg'], bg="#F8FAFC").pack(anchor="w", padx=10, pady=(7, 4))

        SYMPTOMS = [
            ("副歌重复的歌词常被漏掉",   "重复幻觉过滤强度",  "调高，例如 1.8 → 2.2（中）/ 1.35 → 1.8（英）"),
            ("轻声演唱的段落常被跳过",   "静音略过灵敏度",    "调高，例如 0.6 → 0.75"),
            ("电脑很慢、辨识很久",       "辨识精准度",        "调低，例如 5 → 3"),
            ("正常歌词一直被误删",       "辨识信心下限",      "调低，例如 -0.7 → -1.0"),
        ]

        def _cfg3(w): w.columnconfigure(0, minsize=180); w.columnconfigure(1, minsize=140); w.columnconfigure(2, weight=1)
        hdr = tk.Frame(sym_box, bg="#E2E8F0")
        hdr.pack(fill="x", padx=8, pady=(0, 2))
        _cfg3(hdr)
        tk.Label(hdr, text="症状",        font=("Arial", 8, "bold"), fg=C['fg'], bg="#E2E8F0", anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        tk.Label(hdr, text="调整哪个设定", font=("Arial", 8, "bold"), fg=C['fg'], bg="#E2E8F0", anchor="w").grid(row=0, column=1, sticky="w", padx=6, pady=3)
        tk.Label(hdr, text="怎么调",      font=("Arial", 8, "bold"), fg=C['fg'], bg="#E2E8F0", anchor="w").grid(row=0, column=2, sticky="w", padx=6, pady=3)

        # 表格列
        for i, (symptom, setting, action) in enumerate(SYMPTOMS):
            row_bg = "#F8FAFC" if i % 2 == 0 else "#F1F5F9"
            r = tk.Frame(sym_box, bg=row_bg)
            r.pack(fill="x", padx=8)
            _cfg3(r)
            tk.Label(r, text=symptom, font=("Arial", 8), fg=C['fg'], bg=row_bg, anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=2)
            tk.Label(r, text=setting, font=("Arial", 8), fg=C['fg'], bg=row_bg, anchor="w").grid(row=0, column=1, sticky="w", padx=6, pady=2)
            tk.Label(r, text=action,  font=("Arial", 8), fg=C['fg'], bg=row_bg, anchor="w").grid(row=0, column=2, sticky="w", padx=6, pady=2)

        def _make_section(title, params, cfg_dict, var_dict, defaults):
            def _cfg4(w): w.columnconfigure(0, minsize=180); w.columnconfigure(1, minsize=90); w.columnconfigure(2, minsize=110); w.columnconfigure(3, weight=1)
            tk.Frame(scroll_frame, bg=C['border'], height=1).pack(fill="x", padx=16, pady=(8, 0))
            tk.Label(scroll_frame, text=title, font=("Arial", 11, "bold"), fg=C['primary'], bg=C['bg']).pack(anchor="w", padx=16, pady=(6, 4))

            # 表頭（仿 Treeview 樣式）
            hdr = tk.Frame(scroll_frame, bg="#e0e0e0")
            hdr.pack(fill="x", padx=16, pady=(0, 1))
            _cfg4(hdr)
            tk.Label(hdr, text="参数名称", font=("Arial", 9, "bold"), bg="#e0e0e0", anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=3)
            tk.Label(hdr, text="数值",     font=("Arial", 9, "bold"), bg="#e0e0e0", anchor="w").grid(row=0, column=1, sticky="w", padx=6, pady=3)
            tk.Label(hdr, text="建议值",   font=("Arial", 9, "bold"), bg="#e0e0e0", anchor="w").grid(row=0, column=2, sticky="w", padx=6, pady=3)
            tk.Label(hdr, text="说明",     font=("Arial", 9, "bold"), bg="#e0e0e0", anchor="w").grid(row=0, column=3, sticky="w", padx=6, pady=3)

            for i, (key, label, desc, vmin, vmax, step, is_float) in enumerate(params):
                cur_val = cfg_dict.get(key, defaults[key])
                row_bg = C['bg'] if i % 2 == 0 else "#f5f5f5"
                row = tk.Frame(scroll_frame, bg=row_bg)
                row.pack(fill="x", padx=16)
                _cfg4(row)

                tk.Label(row, text=label, font=("Arial", 10, "bold"), fg=C['fg'], bg=row_bg, anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=4)

                var = tk.StringVar(value=str(cur_val))
                var_dict[key] = var
                tk.Entry(row, textvariable=var, width=8, font=("Arial", 10), relief="solid", bd=1).grid(row=0, column=1, sticky="w", padx=6, pady=4)

                suggest = defaults[key]
                tk.Label(row, text=str(suggest), font=("Arial", 9), fg="gray", bg=row_bg, anchor="w").grid(row=0, column=2, sticky="w", padx=6, pady=4)
                tk.Label(row, text=desc, font=("Arial", 8), fg="#555", bg=row_bg, justify="left", anchor="nw", wraplength=260).grid(row=0, column=3, sticky="w", padx=6, pady=4)

        _make_section("🈳  中文辨识参数", ZH_PARAMS, cur_zh, self._wsp_vars_zh, DEFAULTS_ZH)
        _make_section("🔤  英文辨识参数", EN_PARAMS, cur_en, self._wsp_vars_en, DEFAULTS_EN)

        tk.Frame(scroll_frame, bg=C['border'], height=1).pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(scroll_frame, text="🌡️  重试创意程度（温度序列）", font=("Arial", 11, "bold"), fg=C['primary'], bg=C['bg']).pack(anchor="w", padx=16, pady=(6, 2))
        tk.Label(scroll_frame, text=("辨识失败时自动重试的「创意程度」序列，用逗号分隔。\n"
                                     "↑ 加大数值：辨识结果太保守、常辨识失败时\n"
                                     "↓ 减少数值：辨识结果乱跳、出现奇怪歌词时"),
                 font=("Arial", 8), fg="#555", bg=C['bg'], justify="left").pack(anchor="w", padx=16, pady=(0, 4))

        temp_row = tk.Frame(scroll_frame, bg=C['bg'])
        temp_row.pack(fill="x", padx=16, pady=3)

        def _make_temp_entry(parent, label, cfg, defaults, attr, width, padx_right):
            tk.Label(parent, text=label, font=("Arial", 10), fg=C['fg'], bg=C['bg']).pack(side="left")
            init = ", ".join(str(v) for v in cfg.get("temperature", defaults["temperature"]))
            var = tk.StringVar(value=init)
            setattr(self, attr, var)
            tk.Entry(parent, textvariable=var, width=width, font=("Arial", 10), relief="solid", bd=1).pack(side="left", padx=(4, padx_right))

        _make_temp_entry(temp_row, "中文温度：", cur_zh, DEFAULTS_ZH, "_wsp_temp_zh", 18, 20)
        _make_temp_entry(temp_row, "英文温度：", cur_en, DEFAULTS_EN, "_wsp_temp_en", 14, 4)

        tk.Frame(scroll_frame, bg=C['border'], height=1).pack(fill="x", padx=16, pady=(12, 0))

        btn_row = tk.Frame(scroll_frame, bg=C['bg'])
        btn_row.pack(anchor="e", padx=16, pady=10)

        def _parse_temp(s):
            return [float(x.strip()) for x in s.split(",") if x.strip()]

        def _save_whisper_settings():
            try:
                def _read_vars(var_dict, defaults):
                    return {k: (float(v.get()) if isinstance(defaults[k], float) else int(v.get()))
                            for k, v in var_dict.items()}

                zh = _read_vars(self._wsp_vars_zh, DEFAULTS_ZH)
                zh["temperature"] = _parse_temp(self._wsp_temp_zh.get())
                en = _read_vars(self._wsp_vars_en, DEFAULTS_EN)
                en["temperature"] = _parse_temp(self._wsp_temp_en.get())

                self.config["whisper_zh"] = zh
                self.config["whisper_en"] = en
                self.config["use_stable_ts"] = self.use_stable_ts_var.get()
                self.save_config()
                save_btn.config(text="✅ 已储存！")
                self.root.after(2000, lambda: save_btn.config(text="💾  储存设定"))
            except ValueError as e:
                messagebox.showerror("格式错误", f"请确认所有栏位为数字。\n{e}")

        def _reset_defaults():
            if not messagebox.askyesno("还原预设", "确定要还原所有 Whisper 参数为预设值吗？"):
                return
            def _apply(var_dict, defaults, temp_var):
                for key, var in var_dict.items():
                    var.set(str(defaults[key]))
                temp_var.set(", ".join(str(v) for v in defaults["temperature"]))
            _apply(self._wsp_vars_zh, DEFAULTS_ZH, self._wsp_temp_zh)
            _apply(self._wsp_vars_en, DEFAULTS_EN, self._wsp_temp_en)
            self.config["whisper_zh"] = dict(DEFAULTS_ZH)
            self.config["whisper_en"] = dict(DEFAULTS_EN)
            self.config["use_stable_ts"] = True
            self.use_stable_ts_var.set(True)
            self.save_config()
            save_btn.config(text="✅ 已还原并储存！")
            self.root.after(2000, lambda: save_btn.config(text="💾  储存设定"))

        tk.Button(btn_row, text="🔄  还原预设值", command=_reset_defaults, bg=C['warning'], fg='white', font=("Arial", 10, "bold"), relief='flat', padx=14, pady=6, cursor='hand2').pack(side="left", padx=(0, 8))

        save_btn = tk.Button(btn_row, text="💾  储存设定", command=_save_whisper_settings,
                             bg=C['success'], fg='white', font=("Arial", 10, "bold"), relief='flat', padx=14, pady=6, cursor='hand2')
        save_btn.pack(side="left")

    def find_separated_files(self, input_file, output_dir, fmt):
        """寻找分离后的人声和伴奏档案"""
        input_stem = Path(input_file).stem
        out_path = Path(output_dir)
        vocal_file = instrumental_file = None

        try:
            for fname in os.listdir(str(out_path)):
                if not (fname.startswith(input_stem) and fname.endswith(f".{fmt}")): continue
                if "(Vocals)" in fname: vocal_file = str(out_path / fname)
                elif "(Instrumental)" in fname or "(No Vocals)" in fname: instrumental_file = str(out_path / fname)
        except Exception as e:
            self.log(f"  ⚠️ 寻找分离档案时出错: {str(e)}")

        return vocal_file, instrumental_file

    def create_lr_stereo(self, instrumental_file, vocal_file, output_file):
        """创建左伴奏/右（人声+伴奏）立体声 MP3"""
        try:
            vocal_mix = max(0.0, min(1.0, float(self.vocal_volume_var.get()) / 100.0))
            instrumental_mix = max(0.0, min(1.0, float(self.instrumental_volume_var.get()) / 100.0))
            
            cmd = [
                str(self.ffmpeg_exe), "-y",
                "-i", str(instrumental_file),
                "-i", str(vocal_file),
                "-filter_complex",
                f"[0:a]pan=mono|c0=0.5*c0+0.5*c1,volume={instrumental_mix}[inst_mono];"
                "[inst_mono]asplit=2[inst_a][inst_b];"
                f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume={vocal_mix}[voc_mono];"
                "[inst_a][voc_mono]amix=inputs=2:normalize=0[mixed];"
                "[inst_b][mixed]amerge=inputs=2[lr]",
                "-map", "[lr]",
                "-c:a", "libmp3lame",
                "-b:a", "320k",
                str(output_file)
            ]
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            return True
        except Exception as e:
            self.log(f"  ❌ 创建左伴奏/右（人声+伴奏）立体声失败: {str(e)}")
            return False

    def browse_output_dir(self):
        if d := filedialog.askdirectory():
            self.output_dir_var.set(d); self.config["output_dir"] = d; self.save_config()

    def update_status(self, text, color="blue"):
        self.root.after(0, lambda: (self.status_var.set(f"状态: {text}"), self.status_label.config(fg=color)))

    def _get_target_ai_dir(self, install_mode="auto"):
        """根据安装模式决定主要 AI 套件目录。"""
        if install_mode == "cpu": return self.lib_dir
        if install_mode == "gpu": return self.gpu_lib_dir
        return self.gpu_lib_dir if self._detect_gpu_vendor() == "nvidia" else self.lib_dir

    def _resolve_device(self, device_val=None):
        """将 UI device 字串 ('gpu'/'directml'/'cpu') 转为 AI 推理装置名称"""
        val = device_val or self.device_var.get()
        return {"gpu": "cuda", "directml": "directml"}.get(val, "cpu")

    def _get_runtime_ai_dir(self, device=None):
        target = device or self.device_var.get()
        return self.gpu_lib_dir if target in ("gpu","cuda") else (self.directml_lib_dir if target == "directml" else self.lib_dir)

    def _has_onnxruntime_package(self, target_dir):
        """检查指定 AI 套件目录是否已有 onnxruntime 核心包（非仅 metadata）。"""
        try:
            pkg_dir = target_dir / "onnxruntime"
            return pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
        except Exception:
            return False

    @staticmethod
    def _p(path):
        """将 Path/str 转成 Python 嵌入脚本用的正斜线路径字串"""
        return str(path).replace("\\", "/")

    def _build_python_env(self, lib_dir, include_gpu_runtime=False):
        """依据 CPU / GPU 模式建立隔离的 Python 执行环境。"""
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env["PYTHONPATH"] = os.pathsep.join([str(self.common_lib_dir), str(lib_dir)])
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        search_paths = [str(self.py_dir), str(self.bin_dir), str(lib_dir)]
        ort_pkg_dir = lib_dir / "onnxruntime"
        ort_capi_dir = ort_pkg_dir / "capi"
        if ort_pkg_dir.exists():
            search_paths.append(str(ort_pkg_dir))
        if ort_capi_dir.exists():
            search_paths.append(str(ort_capi_dir))
        if include_gpu_runtime and lib_dir.exists():
            for p in lib_dir.rglob("bin"):
                search_paths.append(str(p))
            for p in lib_dir.rglob("lib"):
                search_paths.append(str(p))

        deduped = list(dict.fromkeys(p for p in search_paths if p))

        env["PATH"] = os.pathsep.join(deduped) + os.pathsep + env.get("PATH", "")
        return env

    def _probe_onnxruntime_stack(self, lib_dir, expect_gpu=False):
        """快速检查指定套件目录内的 ONNX Runtime 是否可正常使用。"""
        if not self.local_python.exists() or not lib_dir.exists():
            return "STACK_MISSING"

        env = self._build_python_env(lib_dir, include_gpu_runtime=expect_gpu)
        lib_dir_posix        = self._p(lib_dir)
        common_lib_dir_posix = self._p(self.common_lib_dir)
        app_bin_dir_posix    = self._p(self.bin_dir)
        app_py_dir_posix     = self._p(self.py_dir)
        check_script = f"""
import sys, os

target_lib = r'{lib_dir_posix}'
common_lib = r'{common_lib_dir_posix}'
app_bin_dir = r'{app_bin_dir_posix}'
app_py_dir = r'{app_py_dir_posix}'

if common_lib not in sys.path:
    sys.path.insert(0, common_lib)
if target_lib not in sys.path:
    sys.path.insert(0, target_lib)

# numpy 的 DLL 搜寻：numpy.libs 和 numpy/core 必须在 PATH 最前面，让 Windows loader 能找到
numpy_libs = os.path.join(common_lib, 'numpy.libs')
numpy_core = os.path.join(common_lib, 'numpy', 'core')
os.environ['PATH'] = numpy_libs + os.pathsep + numpy_core + os.pathsep + app_bin_dir + os.pathsep + app_py_dir + os.pathsep + os.environ.get('PATH', '')

if hasattr(os, 'add_dll_directory'):
    dll_dirs = [app_bin_dir, app_py_dir, common_lib, target_lib]
    ort_pkg = os.path.join(target_lib, 'onnxruntime')
    ort_capi = os.path.join(ort_pkg, 'capi')
    for p in [ort_pkg, ort_capi, numpy_libs, numpy_core]:
        if os.path.isdir(p):
            dll_dirs.append(p)
    if {str(expect_gpu)}:
        for root, dirs, files in os.walk(target_lib):
            for sub in ['bin', 'lib']:
                p = os.path.join(root, sub)
                if os.path.isdir(p):
                    dll_dirs.append(p)
    seen = set()
    for p in dll_dirs:
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            os.add_dll_directory(p)
        except Exception:
            pass
try:
    import onnxruntime as ort
    providers = ort.get_available_providers()
    if {str(expect_gpu)}:
        if 'CUDAExecutionProvider' in providers:
            print('ORT_OK_GPU')
        else:
            print(f'ORT_NO_CUDA {{providers}}')
    else:
        print('ORT_OK_CPU')
except Exception as e:
    err = str(e)
    err_lower = err.lower()
    if 'dll' in err_lower or '初始化' in err or 'initialization routine' in err_lower:
        if any(token in err_lower for token in ['vcruntime', 'msvcp', 'api-ms-win-crt', 'ucrtbase']):
            print(f'ORT_DLL_FAIL:VC_RUNTIME_MISSING:{{err[:220]}}')
        else:
            print(f'ORT_DLL_FAIL:{{err[:220]}}')
    else:
        # 增加诊断资讯：若找不到模组，输出当前 sys.path 的前几个项目
        if 'No module named' in err:
            print(f'ORT_ERR:{{err}} (Search Path: {{target_lib}})')
        else:
            print(f'ORT_ERR:{{err[:120]}}')
"""
        try:
            res = subprocess.run(
                [str(self.local_python), "-c", check_script],
                capture_output=True, text=True, env=env,
                creationflags=self.subp_flags, timeout=30,
                encoding="utf-8", errors="replace"
            )
            out = (res.stdout or "").strip()
            if out:
                return out
            if res.returncode not in (0, None):
                err = (res.stderr or "").strip().replace("\n", " ")[:240]
                return f"ORT_PROC_FAIL:rc={res.returncode}:stderr={err}"
            return "ORT_NO_OUTPUT"
        except subprocess.TimeoutExpired:
            return "ORT_TIMEOUT"
        except Exception as e:
            return f"ORT_ERR:{str(e)}"

    def _ensure_runtime_stack_ready(self, device):
        """确保 AI 核心可用。GPU/DirectML 不可用时询问安装或回退 CPU；CPU 缺件自动补装。回传 (is_ready, actual_device, lib_dir)"""
        is_gpu      = (device == "cuda")
        is_directml = (device == "directml")
        runtime_lib_dir = self._get_runtime_ai_dir(device)
        expected = "ORT_OK_GPU" if is_gpu else "ORT_OK_CPU"
        diag_out = self._probe_onnxruntime_stack(runtime_lib_dir, expect_gpu=is_gpu)
        self.log(f"🔍 运算环境诊断: {diag_out}")

        if diag_out == expected:
            return True, device, runtime_lib_dir

        if is_gpu or is_directml:
            mode_name = "GPU" if is_gpu else "DirectML"
            msg = f"侦测到 {mode_name} AI 核心尚未就绪！\n\n是否要现在安装 {mode_name} 元件？\n（按「否」会自动切换至 CPU 模式）"
            install_now = messagebox.askyesno(f"安装 {mode_name} 元件", msg)

            if install_now:
                self.log(f"🛠️ 正在安装 {mode_name} AI 核心...")
                self.update_status(f"正在安装 {mode_name} AI 核心...", "orange")

                install_mode = "gpu" if is_gpu else "directml"
                if self.install_packages_locally(install_mode=install_mode):
                    retry_lib_dir = self.gpu_lib_dir if is_gpu else self.directml_lib_dir
                    retry_out = self._probe_onnxruntime_stack(retry_lib_dir, expect_gpu=is_gpu)
                    self.log(f"🔁 {mode_name} 安装后再次诊断: {retry_out}")

                    if retry_out == expected:
                        self.log(f"✅ {mode_name} AI 核心已安装完成，继续执行音讯分离。")
                        return True, device, retry_lib_dir
                    else:
                        self.log(f"❌ {mode_name} 核心安装后仍无法正常载入：{retry_out}")
                else:
                    self.log(f"❌ {mode_name} AI 核心安装失败。")

                fallback_msg = f"{mode_name} 核心安装失败！\n\n是否要切换至 CPU 模式继续？"
                if messagebox.askyesno(f"{mode_name} 安装失败", fallback_msg):
                    self.log("⚠️ 已切换至独立 CPU 核心继续执行。")
                    self.root.after(0, lambda: self.device_var.set("cpu"))
                    self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
                    return self._ensure_runtime_stack_ready("cpu")
                else:
                    return False, device, runtime_lib_dir
            else:
                self.log(f"⚠️ 使用者取消安装 {mode_name} 元件，已切换至独立 CPU 核心继续执行。")
                self.root.after(0, lambda: self.device_var.set("cpu"))
                self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
                return self._ensure_runtime_stack_ready("cpu")

        repairable_tokens = ["ORT_ERR", "STACK_MISSING", "ORT_DLL_FAIL", "ORT_NO_OUTPUT", "ORT_TIMEOUT"]
        if any(token in diag_out for token in repairable_tokens):
            self.log("🛠️ 侦测到 CPU AI 核心缺失或损坏，正在自动补齐必要组件...")
            self.update_status("正在修复 CPU AI 核心...", "orange")

            if self.install_packages_locally(install_mode="cpu"):
                retry_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
                self.log(f"🔁 CPU 修复后再次诊断: {retry_out}")
                if retry_out == "ORT_OK_CPU":
                    self.log("✅ CPU AI 核心已自动修复完成，继续执行音讯分离。")
                    return True, "cpu", self.lib_dir

                self.log(f"❌ CPU 核心修复后仍无法正常载入：{retry_out}")
            else:
                self.log("❌ 自动修复 CPU AI 核心失败。")

        self.log("❌ CPU 核心无法正常载入，请执行「一键修复/初始化环境」。")
        return False, "cpu", self.lib_dir

    def _schedule_ort_fix_prompt(self, issue_key="gpu_runtime_fallback", delay_ms=0):
        """统一排程修复提示，避免重复排入事件伫列。"""
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            self.log(f"ℹ️ [PROMPT] 已略过修复提示（本次已拒绝）: {issue_key}"); return
        if self._ort_fix_prompt_active or self._ort_fix_prompt_pending:
            reason = "显示中" if self._ort_fix_prompt_active else "已排程"
            self.log(f"ℹ️ [PROMPT] 修复提示{reason}，略过重复请求: {issue_key}"); return
        if issue_key in self._ort_fix_prompt_shown_keys:
            self.log(f"ℹ️ [PROMPT] 修复提示本次已显示过，略过: {issue_key}"); return

        self._ort_fix_prompt_pending = True
        self.log(f"ℹ️ [PROMPT] 已排程修复提示: {issue_key} ({delay_ms}ms)")

        def _fire():
            self._ort_fix_prompt_after_id = None
            self._ort_fix_prompt_pending = False
            self._prompt_ort_fix(issue_key=issue_key)

        self._ort_fix_prompt_after_id = self.root.after(delay_ms, _fire)

    def _reset_ort_fix_prompt_state(self, clear_history=False):
        """清理修复提示的排程与显示状态。"""
        with suppress(Exception):
            if self._ort_fix_prompt_after_id is not None: self.root.after_cancel(self._ort_fix_prompt_after_id)
        self._ort_fix_prompt_after_id = None
        self._ort_fix_prompt_pending = self._ort_fix_prompt_active = False
        if clear_history:
            self._ort_fix_prompt_shown_keys.clear()
            self._ort_fix_prompt_suppressed_keys.clear()
        self.log(f"ℹ️ [PROMPT] 已重置修复提示状态 clear_history={clear_history}")

    def check_gpu_env(self):
        self.log("\n---[开始 GPU 环境深度检测] ---")

        if not self._detect_gpu_vendor() == "nvidia":
            self.log("ℹ️ 系统目前未侦测到启用的 NVIDIA 显示卡。")
            self.log("💡 笔电使用者：请确认已插上电源，且系统已切换至独立显示卡（NVIDIA GPU）。")
            self.log("💡 若您的电脑没有 NVIDIA 显示卡，请使用 CPU 模式，这是正常状态，无需修复。")
            messagebox.showinfo( "未侦测到 NVIDIA GPU", "目前系统未侦测到启用的 NVIDIA 显示卡。\n\n" "• 若您是笔电使用者，请插上电源后再试。\n" "• 若电脑没有 NVIDIA 显示卡，请直接使用 CPU 模式即可，不需要下载 GPU 组件。" )
            return

        if not self.local_python.exists():
            self.log("[ERROR] 内建 Python 核心尚未安装，无法进行检测。")
            if messagebox.askyesno("初始化环境", "侦测到环境尚未初始化，是否要现在开始下载并配置基础环境？"):
                self.check_components(prompt=True)
            return

        env = self._build_python_env(self.gpu_lib_dir, include_gpu_runtime=True)
        gpu_lib_dir_posix    = self._p(self.gpu_lib_dir)
        common_lib_dir_posix = self._p(self.common_lib_dir)

        check_script = f"""
import sys, os
# 使用正斜线避免 Windows 转义问题
target_lib = r'{gpu_lib_dir_posix}'
common_lib = r'{common_lib_dir_posix}'
if common_lib not in sys.path:
    sys.path.insert(0, common_lib)
if target_lib not in sys.path:
    sys.path.insert(0, target_lib)

# 动态加入所有 NVIDIA 相关 DLL 目录
if hasattr(os, 'add_dll_directory'):
    for root, dirs, files in os.walk(target_lib):
        if 'bin' in dirs or 'lib' in dirs:
            for d in ['bin', 'lib']:
                p = os.path.join(root, d)
                if os.path.isdir(p):
                    try: os.add_dll_directory(p)
                    except Exception: pass

libs_found =[]

try:
    import onnxruntime as ort
    libs_found.append('onnxruntime')
    print(f'[OK] ONNX Runtime 版本: {{ort.__version__}}')
    providers = ort.get_available_providers()
    print(f'[OK] 可用运算提供者 (Providers): {{providers}}')

    if 'CUDAExecutionProvider' in providers:
        print('[SUCCESS] ONNX CUDA 提供者已就绪')
    else:
        print('[INFO] ONNX 找不到 CUDA 提供者')
except ImportError:
    print(f'[ERROR] 尚未安装 onnxruntime 套件 (搜寻路径: {{target_lib}})')
except Exception as e:
    err_str = str(e)
    if 'DLL' in err_str or 'dll' in err_str or '初始化' in err_str or 'initialization routine' in err_str:
        print('[ERROR] onnxruntime DLL 载入失败：安装的是 GPU 版本但缺少 CUDA 环境')
        print('[HINT] 请点击「一键修复/初始化环境」重新安装正确版本')
    else:
        print(f'[ERROR] ONNX 检测出错: {{err_str}}')

try:
    import torch
    libs_found.append('torch')
    print(f'[OK] PyTorch 版本: {{torch.__version__}}')
    print(f'[DEBUG] PyTorch 路径: {{torch.__file__}}')
    if torch.cuda.is_available():
        try:
            # 尝试进行一个简单的运算以确保算力相容
            test_tensor = torch.zeros(1).cuda()
            print(f'[OK] PyTorch CUDA 是否可用: True')
            print(f'[OK] 侦测到 GPU: {{torch.cuda.get_device_name(0)}}')
        except Exception as e:
            print(f'[ERROR] PyTorch 虽然侦测到 CUDA，但运算失败 (可能是算力不相容): {{str(e)}}')
    else:
        if "+cpu" in torch.__version__:
            print('[INFO] 当前安装的是 PyTorch CPU 版本，无法使用 GPU 加速')
        else:
            print('[INFO] PyTorch 侦测不到 CUDA，请检查驱动程式')
except ImportError:
    print('[ERROR] 尚未安装 torch 套件')
except Exception as e:
    print(f'[ERROR] PyTorch 检测出错: {{str(e)}}')

if not libs_found:
    print('[STATUS] 核心 AI 套件尚未安装')
"""
        try:
            res = subprocess.run([str(self.local_python), "-c", check_script],
                                 capture_output=True, text=True, env=env,
                                 encoding='utf-8', errors='replace',
                                 creationflags=self.subp_flags)
            stdout_str = (res.stdout or "").strip()
            self.log(stdout_str)
            if res.stderr: self.log(f"[DEBUG] 错误资讯: {res.stderr.strip()}")

            libs_installed = "onnxruntime" in stdout_str and "torch" in stdout_str

            is_sm120_incompatible = "sm_120 is not compatible" in res.stderr

            cuda_ready = ("ONNX CUDA 提供者已就绪" in stdout_str) and \
                         ("PyTorch CUDA 是否可用: True" in stdout_str) and \
                         (not is_sm120_incompatible)

            if not cuda_ready:
                if not libs_installed:
                    self.log("\n💡 侦测到核心组件缺失 (Torch 或 ONNX)。")
                    msg = ("侦测到程式尚未安装「AI 加速组件」或组件损坏。\n\n" "程式需要下载约 1.7GB 的加速库才能发挥 GPU 效能。\n\n" "是否立即执行「一键全自动修复」？")
                    if messagebox.askyesno("一键修复", msg):
                        self._start_async_setup()
                    return
                else:
                    self.log("\n💡 侦测到 CUDA 加速环境配置不完全或不相容。")
                    if is_sm120_incompatible:
                        msg = ("侦测到您的 GPU (RTX 50 系列) 与当前 PyTorch 版本不相容。\n\n" "程式需要重新下载支援 Blackwell 架构的运算核心 (CUDA 12.6+)。\n\n" "是否立即执行「一键修复」？")
                    elif "PyTorch CUDA 是否可用: True" in stdout_str:
                        msg = ("您的 PyTorch 运作正常，但 ONNX 引擎尚未完全对接。\n\n" "是否让程式自动尝试修复 DLL 补丁？")
                    else:
                        if "运算失败" in stdout_str:
                            msg = ("侦测到您的 GPU 与当前 AI 组件版本不相容。\n\n" "这通常是因为您的显示卡太新，需要更新版本的运算核心。\n\n" "是否立即执行「一键修复」以下载最新的相容版本？")
                        else:
                            msg = ("侦测到您的系统 PyTorch 无法使用 GPU (当前可能是 CPU 版本)。\n\n" "是否立即执行「一键修复」以下载正确的 GPU 版本？")

                    if messagebox.askyesno("配置 CUDA 加速", msg):
                        self._start_async_setup()

        except Exception as e:
            self.log(f"[ERROR] 执行检测失败: {str(e)}")

        self.log("--- [检测结束] ---\n")

    def _start_async_setup(self):
        if not self.is_processing:
            self.is_processing = True
            self.update_status("正在执行一键修复...", "orange")
            threading.Thread(target=self._async_setup_environment, daemon=True).start()

    def _render_component_row(self, *args, **kwargs):
        pass  # 已由 Treeview 統一渲染，此函式保留供相容

    def _detect_gpu_vendor(self):
        """侦测系统显示卡厂商，回传 ('nvidia', 'amd_intel', 'none') 之一"""
        # 先試 wmic，失敗再用 PowerShell（Windows 11 部分版本已移除 wmic）
        cmds = [
            ["wmic", "path", "win32_VideoController", "get", "Name"],
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class Display | Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName"],
        ]
        for cmd in cmds:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                   creationflags=self.subp_flags, encoding="utf-8", errors="replace")
                if r.returncode == 0:
                    out = r.stdout.upper()
                    if "NVIDIA" in out: return "nvidia"
                    if "AMD" in out or "RADEON" in out or "INTEL" in out: return "amd_intel"
                    return "none"
            except Exception:
                pass
        return "none"

    def check_components(self, prompt=True, show_list=True):
        """更新环境修复标签页的元件列表"""
        if self.is_processing: return

        if show_list:
            for widget in self.repair_components_frame.winfo_children():
                widget.destroy()

            self.repair_component_vars = {}

        has_gpu = self._detect_gpu_vendor() == "nvidia"

        has_nvidia_gpu_stack   = (self.gpu_lib_dir / "torch").exists() and self._has_onnxruntime_package(self.gpu_lib_dir)
        has_directml_stack     = self._has_onnxruntime_package(self.directml_lib_dir)
        has_cpu_stack          = (self.lib_dir / "torch").exists() and self._has_onnxruntime_package(self.lib_dir)

        try:
            has_amd_or_intel_gpu = self._detect_gpu_vendor() == "amd_intel"
        except Exception:
            has_amd_or_intel_gpu = False

        ai_gpu_essential      = has_gpu and not has_nvidia_gpu_stack
        ai_directml_essential = not has_gpu and has_amd_or_intel_gpu and not has_directml_stack
        ai_cpu_essential      = not has_gpu and not has_amd_or_intel_gpu and not has_cpu_stack

        ai_common_essential = ai_cpu_essential or ai_gpu_essential or ai_directml_essential

        all_components = [
            ("python",        "内建 Python 核心",          "程式运行基础环境",              "runtime_python",        not self.local_python.exists(), True),
            ("ffmpeg",        "音讯引擎 FFmpeg",            "音影片转档与分离",              "engine_ffmpeg",         not (self.bin_dir / "ffmpeg.exe").exists(), True),
            ("ytdlp",         "YouTube 下载器 yt-dlp",     "下载 YouTube 影片与音讯",       "yt-dlp",                not self._is_ytdlp_installed(), True),
            ("ai_common",     "AI 共用函式库",              "人声分离核心套件",              "ai_libraries_common",   not (self.common_lib_dir / "audio_separator").exists(), ai_common_essential),
            ("ai_cpu",        "AI CPU 运算核心",            "CPU 推理，相容性最高",          "ai_libraries_cpu",      not has_cpu_stack, ai_cpu_essential),
            ("whisper",       "Whisper 歌词辨识模型",       "自动辨识歌词并输出 SRT 字幕",   "ai_models_whisper",     not (self.whisper_models_dir.exists() and any(self.whisper_models_dir.iterdir())), False),
            ("stable_whisper","stable-ts 时间轴对齐",       "提升逐字时间轴精度，KTV 必备",  "ai_libraries_common",   not self._is_stable_whisper_installed(), False),
            ("deno",          "Deno JavaScript 执行环境",   "解锁高画质下载与儿童影片限制",  "yt-dlp",                not (self.ytdlp_dir / ("deno.exe" if sys.platform == "win32" else "deno")).exists(), False),
            ("zhconv",        "中文简繁转换库 zhconv",      "歌词简繁中文互转",              "ai_libraries_common",   not self._is_zhconv_installed(), False),
        ]

        if has_gpu:
            all_components.extend([
                ("ai_gpu",      "AI GPU 运算核心 (NVIDIA)",  "NVIDIA GPU 推理，速度最快",     "ai_libraries_gpu",      not has_nvidia_gpu_stack, ai_gpu_essential),
                ("ai_directml", "AI DirectML 运算核心",      "AMD/Intel 显卡推理",            "ai_libraries_directml", not has_directml_stack, ai_directml_essential),
            ])
        elif has_amd_or_intel_gpu:
            all_components.append(("ai_directml", "AI DirectML 运算核心", "AMD/Intel 显卡推理", "ai_libraries_directml", not has_directml_stack, ai_directml_essential))

        if not show_list:
            base_ok = self.local_python.exists() and (self.bin_dir / "ffmpeg.exe").exists()
            sep_exists = (self.common_lib_dir / "audio_separator").exists()
            has_any_ai = sep_exists and (
                ((self.lib_dir / "torch").exists() and self._has_onnxruntime_package(self.lib_dir)) or
                (has_gpu and (self.gpu_lib_dir / "torch").exists() and self._has_onnxruntime_package(self.gpu_lib_dir)) or
                (self.directml_lib_dir / "onnxruntime").exists()
            )
            if not prompt:
                if base_ok and has_any_ai:
                    self._check_ytdlp()
                    threading.Thread(target=self._startup_ort_check, daemon=True).start()
                elif self._startup_component_prompt_shown:
                    self.log("ℹ️ 启动修复提示本次已显示过，略过重复弹窗。")
                else:
                    self._startup_component_prompt_shown = True
                    self.log("⚠️ 侦测到重要元件缺少，自动切换到「环境修复」页签...")
                    self.root.after(0, lambda: self.switch_tab(6))
            return

        loading_label = tk.Label(self.repair_components_frame, text="🔍 正在扫描元件状态...", font=("Arial", 11), fg=self.ui_colors['primary'], bg=self._bg)
        loading_label.pack(pady=30)
        self.repair_components_frame.update_idletasks()

        def on_select_all():
            for v in self.repair_component_vars.values(): v.set(True)

        def on_select_none():
            for v in self.repair_component_vars.values(): v.set(False)

        def on_repair_click():
            selected = [cid for cid, var in self.repair_component_vars.items() if var.get()]
            if not selected:
                messagebox.showwarning("提示", "请至少选择一个要修复的组件！")
                return
            self.is_processing = True
            self.update_status("正在修复选定的组件...", "orange")
            threading.Thread(target=self._async_repair_components, args=(selected,), daemon=True).start()

        self.repair_select_all_btn.config(command=on_select_all)
        self.repair_select_none_btn.config(command=on_select_none)
        self.repair_start_btn.config(command=on_repair_click)

        def _build_component_list_bg():
            """背景执行绪：做所有慢速 I/O 侦测，完成后切回主执行绪更新 UI"""
            _ver_getters = {
                "ytdlp":         lambda: self._get_ytdlp_version(),
                "ai_common":     lambda: self._get_package_version(self.common_lib_dir, "audio-separator"),
                "ai_cpu":        lambda: self._get_package_version(self.lib_dir, "torch"),
                "ai_gpu":        lambda: self._get_package_version(self.gpu_lib_dir, "torch"),
                "whisper":       lambda: self._get_package_version(self.common_lib_dir, "openai-whisper"),
                "stable_whisper":lambda: self._get_package_version(self.common_lib_dir, "stable-ts"),
                "zhconv":        lambda: self._get_package_version(self.common_lib_dir, "zhconv"),
                "ai_directml":   lambda: self._get_package_version(self.directml_lib_dir, "onnxruntime-directml"),
                "deno":          lambda: self._get_deno_version(),
            }
            component_versions = {}
            for comp_id, *_ in all_components:
                version = _ver_getters[comp_id]() if comp_id in _ver_getters else None
                if comp_id == "whisper" and not version and self.whisper_models_dir.exists() and any(self.whisper_models_dir.iterdir()):
                    version = "模型已下载"
                component_versions[comp_id] = version
            self.root.after(0, lambda: _render_component_list(all_components, component_versions))

        def _render_component_list(all_components, component_versions):
            """主执行绪：清除 loading，绘制元件列表"""
            for widget in self.repair_components_frame.winfo_children():
                widget.destroy()
            self.repair_component_vars = {}

            has_versions = any(v is not None for v in component_versions.values())
            if has_versions:
                info_label = tk.Label(self.repair_components_frame, text="💡 部分元件已显示目前版本，您可以勾选以重新安装更新", font=("Arial", 11), fg="#1E88E5", bg=self._bg)
                info_label.pack(pady=2, anchor=tk.W)

            if ai_gpu_essential:
                detect_text = "🖥️ 已侦测到 NVIDIA 显示卡，建议安装「AI GPU 运算核心」（速度最快）"
                detect_color = "#1B5E20"
            elif ai_directml_essential:
                detect_text = "🖥️ 已侦测到 AMD / Intel 显示卡，建议安装「AI DirectML 运算核心」"
                detect_color = "#1B5E20"
            elif ai_cpu_essential:
                detect_text = "🖥️ 未侦测到独立显示卡，建议安装「AI CPU 运算核心」"
                detect_color = "#E65100"
            else:
                detect_text = "✅ 已根据您的显示卡自动选取建议的运算核心（已安装）"
                detect_color = "#1B5E20"
            tk.Label(self.repair_components_frame, text=detect_text, font=("Arial", 11, "bold"), fg=detect_color, bg=self._bg).pack(pady=(0, 6), anchor=tk.W)

            # ── Treeview（可拖拉欄寬，類檔案總管）──
            core_components = []
            download_components = []
            ai_core_components = []
            ai_model_components = []

            _groups = {"python": core_components, "ffmpeg": core_components,
                       "ytdlp": download_components, "deno": download_components, "zhconv": download_components,
                       "ai_common": ai_core_components, "ai_cpu": ai_core_components,
                       "ai_gpu": ai_core_components, "ai_directml": ai_core_components,
                       "whisper": ai_model_components, "stable_whisper": ai_model_components}
            for comp in all_components:
                _groups.get(comp[0], []).append(comp)

            style = ttk.Style()
            style.configure("Repair.Treeview",        rowheight=26, font=("Arial", 10))
            style.configure("Repair.Treeview.Heading", font=("Arial", 10, "bold"))

            tv_frame = tk.Frame(self.repair_components_frame, bg=self._bg)
            tv_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

            tv = ttk.Treeview(tv_frame, style="Repair.Treeview", show="headings",
                               columns=("check", "name", "desc", "folder", "ver", "status"),
                               selectmode="none")
            tv.heading("check",  text="☑",     anchor="center")
            tv.heading("name",   text="名称",   anchor="w")
            tv.heading("desc",   text="说明",   anchor="w")
            tv.heading("folder", text="资料夹", anchor="w")
            tv.heading("ver",    text="版本",   anchor="w")
            tv.heading("status", text="状态",   anchor="center")
            tv.column("check",  width=36,  minwidth=36,  stretch=False, anchor="center")
            tv.column("name",   width=180, minwidth=100, stretch=True,  anchor="w")
            tv.column("desc",   width=200, minwidth=100, stretch=True,  anchor="w")
            tv.column("folder", width=160, minwidth=80,  stretch=True,  anchor="w")
            tv.column("ver",    width=90,  minwidth=60,  stretch=True,  anchor="w")
            tv.column("status", width=80,  minwidth=60,  stretch=False, anchor="center")
            tv.tag_configure("group",          background="#dce8f7", font=("Arial", 10, "bold"), foreground=self.ui_colors["primary"])
            tv.tag_configure("checked_ok",     foreground="#000000", background="#f0fff0")
            tv.tag_configure("unchecked_ok",   foreground="#000000", background="#f0fff0")
            tv.tag_configure("checked_miss",   foreground="#000000", background="#fff0f0")
            tv.tag_configure("unchecked_miss", foreground="#000000", background="#fff0f0")
            tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            tv_sb = tk.Scrollbar(tv_frame, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=tv_sb.set)
            tv_sb.pack(side=tk.RIGHT, fill=tk.Y)

            # iid → (comp_id, BooleanVar)
            self._tv_repair = tv
            self._tv_repair_rows = {}   # iid: (comp_id, BooleanVar)

            sections = [
                ("【 核心元件 】",    core_components),
                ("【 下载工具 】",    download_components),
                ("【 AI 运算核心 】", ai_core_components),
                ("【 AI 模型 】",     ai_model_components),
            ]
            for title, comps in sections:
                if not comps:
                    continue
                g_iid = tv.insert("", "end", values=("", title, "", "", "", ""), tags=("group",))
                for comp_id, comp_name, comp_desc, folder, is_missing, is_essential in comps:
                    pre_checked = is_essential and is_missing
                    var = tk.BooleanVar(value=pre_checked)
                    self.repair_component_vars[comp_id] = var
                    chk = "☑" if pre_checked else "☐"
                    ver = component_versions[comp_id]
                    ver_text = f"v{ver}" if ver else "—"
                    status = "⚠ 缺少" if is_missing else "✔ 正常"
                    row_tag = ("checked" if pre_checked else "unchecked") + ("_ok" if not is_missing else "_miss")
                    iid = tv.insert("", "end", values=(chk, comp_name, comp_desc, folder, ver_text, status), tags=(row_tag,))
                    self._tv_repair_rows[iid] = (comp_id, var)

            def _tv_toggle(event):
                iid = tv.identify_row(event.y)
                if not iid or iid not in self._tv_repair_rows:
                    return
                comp_id, var = self._tv_repair_rows[iid]
                new_val = not var.get()
                var.set(new_val)
                chk = "☑" if new_val else "☐"
                vals = list(tv.item(iid, "values"))
                vals[0] = chk
                # 保留 ok/miss 後綴
                old_tags = tv.item(iid, "tags")
                suffix = "_ok" if any(t.endswith("_ok") for t in old_tags) else "_miss"
                tv.item(iid, values=vals, tags=(("checked" if new_val else "unchecked") + suffix,))

            tv.bind("<ButtonRelease-1>", _tv_toggle)

            # 全選 / 全不選 也要同步更新 Treeview
            def on_select_all_tv():
                for iid, (comp_id, var) in self._tv_repair_rows.items():
                    var.set(True)
                    vals = list(tv.item(iid, "values")); vals[0] = "☑"
                    old_tags = tv.item(iid, "tags")
                    suffix = "_ok" if any(t.endswith("_ok") for t in old_tags) else "_miss"
                    tv.item(iid, values=vals, tags=("checked" + suffix,))
            def on_select_none_tv():
                for iid, (comp_id, var) in self._tv_repair_rows.items():
                    var.set(False)
                    vals = list(tv.item(iid, "values")); vals[0] = "☐"
                    old_tags = tv.item(iid, "tags")
                    suffix = "_ok" if any(t.endswith("_ok") for t in old_tags) else "_miss"
                    tv.item(iid, values=vals, tags=("unchecked" + suffix,))
            self.repair_select_all_btn.config(command=on_select_all_tv)
            self.repair_select_none_btn.config(command=on_select_none_tv)

        threading.Thread(target=_build_component_list_bg, daemon=True).start()

    def _check_component_update(self, comp_id, comp_name, parent_dialog):
        """检查指定元件是否有更新"""
        self.log(f"🔍 正在检查 {comp_name} 的更新...")

        version_getters = {
            "ytdlp":     self._get_ytdlp_version,
            "ai_common": lambda: self._get_package_version(self.common_lib_dir, "audio-separator"),
            "ai_cpu":    lambda: self._get_package_version(self.lib_dir, "torch"),
            "ai_gpu":    lambda: self._get_package_version(self.gpu_lib_dir, "torch"),
            "whisper":   lambda: self._get_package_version(self.common_lib_dir, "openai-whisper"),
        }
        current_ver = version_getters[comp_id]() if comp_id in version_getters else None

        msg = f"元件: {comp_name}\n"
        if current_ver:
            msg += f"目前版本: {current_ver}\n"
        msg += "\n是否要重新安装/更新此元件？"

        if messagebox.askyesno(f"检查更新 - {comp_name}", msg):
            parent_dialog.destroy()
            self.is_processing = True
            self.update_status(f"正在更新 {comp_name}...", "orange")
            threading.Thread(target=self._async_repair_components, args=([comp_id],), daemon=True).start()

    def _get_ytdlp_version(self):
        """取得 yt-dlp 版本（优先读 dist-info，避免启动 subprocess）"""
        try:
            ver = self._get_package_version(self.ytdlp_dir, "yt-dlp")
            if ver:
                return ver
            ver_file = self.ytdlp_dir / "yt_dlp" / "version.py"
            if ver_file.exists():
                for line in ver_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "__version__" in line and "=" in line:
                        v = line.split("=", 1)[1].strip()
                        return v.strip("'").strip('"').strip()
        except Exception:
            pass
        return None

    def _startup_update_ytdlp(self):
        """启动时静默更新 yt-dlp（背景执行，不阻挡 UI）"""
        try:
            if not self.local_python.exists():
                return  # Python 環境尚未安裝，跳過
            self.log("🔄 正在背景更新 yt-dlp 至最新版本...")
            result = subprocess.run(
                [str(self.local_python), "-m", "pip", "install", "--upgrade", "yt-dlp",
                 "--target", str(self.ytdlp_dir), "--no-warn-script-location",
                 "--retries", "3", "--timeout", "30"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, creationflags=self.subp_flags
            )
            if result.returncode == 0:
                ver = self._get_ytdlp_version()
                ver_str = f" ({ver})" if ver else ""
                if "already up-to-date" in result.stdout.lower() or "already satisfied" in result.stdout.lower():
                    self.log(f"✅ yt-dlp 已是最新版本{ver_str}，无需更新。")
                else:
                    self.log(f"✅ yt-dlp 已更新至最新版本{ver_str}。")
            else:
                self.log(f"⚠️ yt-dlp 背景更新失败（不影响使用），错误: {result.stderr.strip()[:100]}")
        except subprocess.TimeoutExpired:
            self.log("⚠️ yt-dlp 背景更新逾时，略过。")
        except Exception as e:
            self.log(f"⚠️ yt-dlp 背景更新出错（不影响使用）: {str(e)[:80]}")

    def _get_package_version(self, target_dir, package_name):
        """取得指定目录中套件的版本（直接读 dist-info，不跑 subprocess）"""
        try:
            norms = {package_name.lower().replace("-", "_"), package_name.lower().replace("_", "-")}
            target = Path(target_dir)
            for dist_info in target.glob("*.dist-info"):
                folder_lower = dist_info.name.lower()
                if any(folder_lower.startswith(n + "-") for n in norms):
                    for fname in ("METADATA", "PKG-INFO"):
                        record = dist_info / fname
                        if record.exists():
                            with open(record, "r", encoding="utf-8", errors="ignore") as f:
                                for line in f:
                                    if line.startswith("Version:"):
                                        return line.split(":", 1)[1].strip()
                            break
                    parts = dist_info.stem.split("-")
                    if len(parts) >= 2:
                        return parts[1]
        except Exception:
            pass
        return None

    def _log_install_result(self, ok, name, success_msg=None, fail_msg=None):
        """统一记录安装结果"""
        self.log((success_msg or f"✅ {name} 已安装完成。") if ok else (fail_msg or f"❌ {name} 安装失败。"))
        return ok

    def _install_component_python(self):
        """安装 Python 核心"""
        if not self.local_python.exists():
            self.log("🚀 正在下载内建 Python 核心 (约 10MB)...")
            if not self.download_portable_python():
                self.log("❌ Python 下载失败，请检查网路连线。"); return False
        else:
            self.fix_python_pth()
        ok = self.local_python.exists()
        self.log("✅ 内建 Python 核心已就绪。" if ok else "❌ Python 部署异常：路径存在但找不到执行档。")
        return ok

    def _install_component_ytdlp(self):
        """安装 YouTube 下载器 yt-dlp"""
        if self._is_ytdlp_installed():
            self.log("✅ YouTube 下载器 yt-dlp 已就绪。")
        else:
            self.log("🚀 正在安装 YouTube 下载器 yt-dlp（约 10‑20MB）...")
            self._install_ytdlp_silent()
        return True

    def _get_deno_version(self):
        """取得本地 Deno 版本字串"""
        local_deno = self.ytdlp_dir / ("deno.exe" if sys.platform == "win32" else "deno")
        if not local_deno.exists():
            return None
        try:
            res = subprocess.run([str(local_deno), "--version"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=self.subp_flags,
                                 encoding="utf-8", errors="replace")
            for line in (res.stdout or "").splitlines():
                if line.startswith("deno"):
                    return line.strip()
        except Exception:
            pass
        return "已安装"

    def _install_component_deno(self):
        """下载免安装版 Deno 到 yt-dlp 目录，供 yt-dlp --js-runtimes 使用"""
        local_deno = self.ytdlp_dir / ("deno.exe" if sys.platform == "win32" else "deno")
        if local_deno.exists():
            self.log("✅ Deno JavaScript 执行环境已就绪。")
            # 重置 js_runtime 快取，讓下次下載時重新偵測到新版 deno
            self._yt_js_runtime_cache = None
            return True

        if sys.platform != "win32":
            # 非 Windows：檢查系統是否已有 deno 或 node
            if shutil.which("deno") or shutil.which("node"):
                self.log("✅ 已侦测到系统 deno / node，Deno 元件无需另行安装。")
                self._yt_js_runtime_cache = None
                return True
            self.log("⚠️ 非 Windows 系统，请确保系统已安装 deno 或 node。")
            return True

        self.log("🚀 正在下载免安装版 Deno（约 50-100MB）...")
        deno_url = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
        zip_path = self.ytdlp_dir / "deno_tmp.zip"
        try:
            self._setup_ssl_opener()
            self._last_log_percent = -1
            urllib.request.urlretrieve(deno_url, str(zip_path), reporthook=self._download_reporthook)
            self.log("📦 正在解压缩 Deno...")
            with zipfile.ZipFile(str(zip_path), 'r') as zf:
                zf.extractall(str(self.ytdlp_dir))
            zip_path.unlink(missing_ok=True)
            if local_deno.exists():
                self.log("✅ Deno 安装完成，儿童影片 / n challenge 解锁功能已启用。")
                self._yt_js_runtime_cache = None  # 重置快取
                return True
            else:
                self.log("❌ Deno 解压后找不到执行档，请手动安装。")
                return False
        except Exception as e:
            zip_path.unlink(missing_ok=True)
            self.log(f"❌ Deno 下载失败: {str(e)}")
            self.log("💡 可手动安装 Node.js 作为替代（也支援 --js-runtimes）。")
            return False

    def _install_component_ffmpeg(self):
        """安装 FFmpeg 音讯引擎"""
        if (self.bin_dir / "ffmpeg.exe").is_file():
            self.log("✅ 音讯引擎 FFmpeg 已就绪。"); return True
        self.log("🚀 正在下载音讯引擎 FFmpeg (约 100MB+)...")
        ok = self.download_ffmpeg()
        if not ok: self.log("❌ FFmpeg 下载失败。")
        return ok
    def _install_component_ai_common(self):
        """安装 AI 共用函式库 (audio-separator)"""
        self.common_lib_dir.mkdir(parents=True, exist_ok=True)
        self.log("📦 正在安装 AI 共用函式库 (audio-separator)...")
        ok = self._run_pip(["--upgrade", "audio-separator"], self.common_lib_dir)
        return self._log_install_result(ok, "AI 共用函式库 (audio-separator)")

    def _install_component_ai_cpu(self):
        """安装 AI CPU 运算核心"""
        is_rtx50 = self._is_rtx_50_series()
        return self._install_ai_stack(self.lib_dir, "cpu", is_rtx50, clean=False)

    def _install_component_ai_gpu(self):
        """安装 AI GPU 运算核心"""
        if self._detect_gpu_vendor() != "nvidia":
            self.log("❌ 未侦测到 NVIDIA GPU，跳过 GPU 核心安装。")
            return False
        is_rtx50 = self._is_rtx_50_series()
        return self._install_ai_stack(self.gpu_lib_dir, "gpu", is_rtx50, clean=False)

    def _install_component_whisper(self):
        """安装 Whisper AI 歌词识别模型"""
        self.log("📥 正在安装 Whisper 相关套件...")
        try:
            if not self._run_pip(["openai-whisper", "ffmpeg-python"], self.common_lib_dir, log_all=True):
                self.log("❌ Whisper 套件安装失败。")
                return False
            self.log("✅ Whisper 套件安装完成。")

            # 安裝 stable-ts：對 Whisper word_timestamps 做強制對齊後校正，
            # 大幅提升逐字時間軸精度（±50ms vs 原生 ±300ms）
            self.log("📥 正在安装 stable-ts（时间轴精准对齐套件）...")
            if self._run_pip(["stable-ts"], self.common_lib_dir, log_all=False):
                self.log("✅ stable-ts 安装完成。")
            else:
                self.log("⚠️ stable-ts 安装失败，将以原生 Whisper 时间轴继续（精度较低）。")

            self.whisper_models_dir.mkdir(parents=True, exist_ok=True)

            self.log("📥 正在下载 Whisper small 模型...")
            script = f'''
import os
import sys
sys.path.insert(0, r"{str(self.common_lib_dir)}")
os.environ['WHISPER_MODELS_DIR'] = r"{str(self.whisper_models_dir)}"
import whisper
print("[INFO] Loading Whisper model...")
model = whisper.load_model("small", download_root=r"{str(self.whisper_models_dir)}")
print("[SUCCESS] Model loaded successfully!")
'''
            process2 = subprocess.Popen(
                [str(self.local_python), "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                encoding='utf-8', errors='replace', creationflags=self.subp_flags)
            self._current_process = process2

            model_loaded = False
            for line in process2.stdout:
                clean_line = line.strip()
                if clean_line: self.log(f"  > {clean_line}")
                if "[SUCCESS]" in clean_line: model_loaded = True

            process2.wait()
            if process2.returncode == 0 or model_loaded:
                self.log("✅ Whisper small 模型下载完成。")
                return True
            self.log("❌ Whisper 模型下载失败。")
            return False

        except Exception as e:
            self.log(f"❌ 安装 Whisper 时发生错误: {str(e)}")
            return False

    def _is_zhconv_installed(self):
        import importlib.util
        if str(self.common_lib_dir) not in sys.path: sys.path.insert(0, str(self.common_lib_dir))
        return importlib.util.find_spec("zhconv") is not None

    def _is_stable_whisper_installed(self):
        """检查 stable-ts 是否已安装（模组目录名为 stable_whisper）"""
        if str(self.common_lib_dir) not in sys.path: sys.path.insert(0, str(self.common_lib_dir))
        with suppress(Exception):
            import stable_whisper  # noqa: F401
            return True
        with suppress(Exception):
            return any(p.name.startswith("stable_ts-") and p.suffix in (".dist-info", ".egg-info")
                       for p in self.common_lib_dir.iterdir())
        return False

    def _install_pip_component(self, pkg_name, display_name, target_dir=None, log_all=False):
        """通用：pip 安装单一套件并记录结果"""
        if target_dir is None:
            target_dir = self.common_lib_dir
        self.log(f"🚀 正在安装 {display_name}...")
        ok = self._run_pip([pkg_name], target_dir, log_all=log_all)
        return self._log_install_result(ok, display_name)

    def _install_component_ai_directml(self):
        """安装 AI DirectML 运算核心"""
        self.log("🚀 正在安装 AI DirectML 运算核心...")
        self.directml_lib_dir.mkdir(parents=True, exist_ok=True)
        ok = self._run_pip(["onnxruntime-directml"], self.directml_lib_dir)
        return self._log_install_result(ok, "AI DirectML 运算核心")

    def _ensure_setup_dirs(self, show_progress=False):
        """确保所有工作目录存在，失败时记录错误并回传 False。"""
        setup_dirs = [
            ("音讯引擎", self.bin_dir), ("Python环境", self.py_dir),
            ("yt-dlp目录", self.ytdlp_dir), ("共用AI函式库", self.common_lib_dir),
            ("CPU AI函式库", self.lib_dir), ("GPU AI函式库", self.gpu_lib_dir),
            ("DirectML AI函式库", self.directml_lib_dir), ("模型目录", self.models_dir),
        ]
        for i, (name, d) in enumerate(setup_dirs):
            try:
                d.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 目录已就绪: {d.name}")
                if show_progress: self.update_status(f"正在准备目录... ({i+1}/{len(setup_dirs)})", "orange")
            except Exception as e:
                self.log(f"❌ 无法建立 {name} 目录: {d}\n   错误讯息: {str(e)}")
                return False
        return True

    # ── 環境修復：混合版（集中 registry + 單一 loop），兼顧可維護與精簡 ──
    def _components(self):
        """所有可修复/可更新元件集中在这里，新增/调整只需改此清单。"""
        return {
            "python":         ("内建 Python 核心",                         self._install_component_python),
            "ffmpeg":         ("音讯引擎 FFmpeg",                           self._install_component_ffmpeg),
            "ytdlp":          ("YouTube 下载器 yt-dlp",                      self._install_component_ytdlp),
            "deno":           ("Deno JavaScript 执行环境",                    self._install_component_deno),
            "ai_common":      ("AI 共用函式库 (audio-separator)",            self._install_component_ai_common),
            "ai_cpu":         ("AI CPU 运算核心 (PyTorch + ONNX Runtime)",   self._install_component_ai_cpu),
            "ai_gpu":         ("AI GPU 运算核心 (PyTorch + ONNX Runtime)",   self._install_component_ai_gpu),
            "ai_directml":    ("AI DirectML 运算核心",                       self._install_component_ai_directml),
            "whisper":        ("Whisper AI 歌词识别模型",                    self._install_component_whisper),
            "stable_whisper": ("stable-ts 时间轴精准对齐套件",               lambda: self._install_pip_component("stable-ts", "stable-ts 时间轴精准对齐套件", log_all=True)),
            "zhconv":         ("中文简繁转换库 zhconv",                      lambda: self._install_pip_component("zhconv", "中文简繁转换库 zhconv")),
        }

    def _run_components(self, ids, *, start=0, end=90, status_color="orange", overrides=None):
        """用同一套 loop 跑所有元件安装/更新。ids: 元件 id 列表; start/end: 进度范围; overrides: {id: {pct,step,status}}"""
        comps = self._components()
        overrides = overrides or {}
        ids = [cid for cid in ids if cid in comps]
        total = len(ids)
        if not ids:
            return True

        ok_all = True
        for i, cid in enumerate(ids, start=1):
            name, fn = comps[cid]
            ov = overrides.get(cid) or {}

            pct = ov.get("pct", int(start + (end - start) * (i - 1) / max(1, total)))

            status_txt = ov.get("status", f"正在安装 {name}... ({i}/{total})")
            step_txt   = ov.get("step",   f"步骤 {i}/{total}：{name}")

            self.update_status(status_txt, status_color)
            self.update_progress(pct, step_text=step_txt)
            self.log(f"\n--- 正在安装组件: {cid} ---")

            try:
                ok = bool(fn())
            except Exception as e:
                ok = False
                self.log(f"  ❌ 安装流程发生例外：{str(e)}")

            if not ok:
                ok_all = False

        return ok_all

    def _async_repair_components(self, selected_components):
        """修复选定的组件"""
        self.log(f"--- 开始修复选定的组件: {', '.join(selected_components)} ---")
        self.update_status("正在准备修复环境...", "orange")
        try:
            if not self._ensure_setup_dirs(show_progress=True):
                self.update_status("修复失败", "red")
                return

            # 用同一套共用 loop 跑所有元件安裝/更新
            success = self._run_components(selected_components, start=0, end=90, status_color="orange")

            if any(comp in selected_components for comp in ["ai_common", "ai_cpu", "ai_gpu"]):
                self.update_status("正在重新检测运算环境...", "orange")
                self.update_progress(95, step_text="正在验证运算环境...")
                self.log("\n🔄 正在根据新环境自动切换运算装置...")
                self._startup_ort_check()

            self.update_progress(100, "修复完成", step_text="")
            self._reset_ort_fix_prompt_state(clear_history=True)
            self._startup_component_prompt_shown = False
            self.update_status("准备就绪", "green")
            self.log("\n--- 所有选定组件修复完成 ---" if success else "\n--- 部分组件修复失败，请查看上方日志 ---")

            if self.current_tab_index == 6:
                self.root.after(100, lambda: self.check_components(prompt=False, show_list=True))
        finally:
            self.is_processing = False

    def _is_ytdlp_installed(self):
        """检查 yt-dlp 是否已安装（Scripts/ 或 lib_dir 或 ytdlp_dir 均算）"""
        return any((
            (self.ytdlp_dir / "yt-dlp.exe").exists(),
            (self.ytdlp_dir / "yt_dlp" / "__main__.py").exists(),
            (self.py_dir / "Scripts" / "yt-dlp.exe").exists(),
            (self.lib_dir / "yt_dlp" / "__main__.py").exists(),
        ))

    def _check_ytdlp(self):
        """检查 yt-dlp 是否安装，若无则静默安装"""
        if not self._is_ytdlp_installed():
            self.log("🚀 侦测到缺少 YouTube 下载组件，正在自动补齐...")
            threading.Thread(target=self._install_ytdlp_silent, daemon=True).start()

    def _install_ytdlp_silent(self):
        try:
            result = subprocess.run(
                [str(self.local_python), "-m", "pip", "install", "--upgrade", "yt-dlp",
                 "--target", str(self.ytdlp_dir), "--no-warn-script-location"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, creationflags=self.subp_flags
            )
            if result.returncode == 0:
                self.log("✅ YouTube 下载组件已补齐。")
                return
            self.log(f"❌ YouTube 下载组件安装失败: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            self.log("❌ YouTube 下载组件安装逾时（超过 120 秒），请手动点击「初始化/修复环境」。")
        except Exception as e:
            self.log(f"❌ YouTube 下载组件安装出错: {str(e)}")

    def _async_setup_environment(self, install_mode="auto"):
        self.log(f"--- 开始自动化环境部署 (模式: {install_mode}) ---")
        self.update_progress(0, step_text="步骤 1/5：建立目录结构...")
        try:
            if not self._ensure_setup_dirs():
                return

            # 基礎元件：用同一套安裝 loop 跑（避免重複寫安裝流程）
            overrides = {
                "python": {"pct": 10, "step": "步骤 2/5：安装内建 Python 核心 (约 10MB)..."},
                "ytdlp":  {"pct": 25, "step": "步骤 3/5：安装 YouTube 下载器 yt-dlp (约 10-20MB)..."},
                "ffmpeg": {"pct": 40, "step": "步骤 4/5：安装音讯引擎 FFmpeg (约 100MB+)..."},
            }
            base_ok = self._run_components(["python", "ytdlp", "ffmpeg"], start=10, end=40, status_color="orange", overrides=overrides)
            if not base_ok:
                return

            self.update_progress(60, step_text="步骤 5/5：检查 AI 运算环境...")
            self.log("🔍 正在进行 AI 运算环境深度检查...")
            packages_ok = False
            target_ai_dir = self._get_target_ai_dir(install_mode)
            expect_gpu_stack = (target_ai_dir == self.gpu_lib_dir)

            has_torch = (target_ai_dir / "torch").exists()
            has_sep = (self.common_lib_dir / "audio_separator").exists()
            has_ort = self._has_onnxruntime_package(target_ai_dir)

            if has_torch and has_sep and has_ort:
                try:
                    self.log(f"  > 正在测试 {'GPU' if expect_gpu_stack else 'CPU'} 组件导入...")
                    check_cmd = f"""
    import sys, os, subprocess
    common_lib_dir = r'{self.common_lib_dir}'
    target_lib_dir = r'{target_ai_dir}'
    if common_lib_dir not in sys.path:
        sys.path.insert(0, common_lib_dir)
    if target_lib_dir not in sys.path:
        sys.path.insert(0, target_lib_dir)
    if {str(expect_gpu_stack)} and hasattr(os, 'add_dll_directory'):
        lib_path = r'{target_ai_dir}'
        for root_dir, dirs, files in os.walk(lib_path):
            for sub in ['bin', 'lib']:
                p = os.path.join(root_dir, sub)
                if os.path.isdir(p):
                    try: os.add_dll_directory(p)
                    except Exception: pass

    has_nvidia_gpu = False
    try:
        r = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            has_nvidia_gpu = True
    except Exception:
        pass

    ort_import_ok = False
    torch_import_ok = False
    ort_err = ''

    try:
        import onnxruntime as ort
        ort_import_ok = True
    except ImportError as e:
        ort_err = str(e)
    except Exception as e:
        ort_err = str(e)
        if 'DLL' in ort_err or 'dll' in ort_err or 'initialization routine' in ort_err or '初始化' in ort_err:
            if not has_nvidia_gpu:
                print('CHECK_RESULT:WRONG_BUILD_FOR_CPU')
            else:
                print(f'CHECK_RESULT:ORT_DLL_FAIL_{{ort_err[:80]}}')
            import sys; sys.exit(0)

    try:
        import torch
        torch_import_ok = True
    except ImportError as e:
        print(f'CHECK_RESULT:MISSING_torch_{{str(e)}}')
        import sys; sys.exit(0)
    except Exception as e:
        print(f'CHECK_RESULT:ERROR_torch_{{str(e)}}')
        import sys; sys.exit(0)

    if not ort_import_ok:
        print(f'CHECK_RESULT:MISSING_ort_{{ort_err}}')
    else:
        providers = ort.get_available_providers()
        cuda_available = torch.cuda.is_available()
        sm_compatible = True
        if cuda_available:
            try: torch.zeros(1).cuda()
            except Exception as e:
                if 'sm_120' in str(e) or 'sm_' in str(e): sm_compatible = False
        cuda_ok = 'CUDAExecutionProvider' in providers and cuda_available and sm_compatible
        is_cpu_build = '+cpu' in torch.__version__
        if cuda_ok:
            print('CHECK_RESULT:OK')
        elif not sm_compatible:
            print('CHECK_RESULT:SM120_INCOMPATIBLE')
        elif not has_nvidia_gpu and is_cpu_build:
            print('CHECK_RESULT:CPU_OK')
        elif not has_nvidia_gpu and not is_cpu_build:
            print('CHECK_RESULT:WRONG_BUILD_FOR_CPU')
        else:
            print(f'CHECK_RESULT:NO_CUDA providers={{providers}} cuda={{cuda_available}}')
    """
                    env = self._build_python_env(target_ai_dir, include_gpu_runtime=expect_gpu_stack)
                    res = subprocess.run([str(self.local_python), "-c", check_cmd],
                                         capture_output=True, text=True, creationflags=self.subp_flags,
                                         env=env, timeout=60,
                                         encoding="utf-8", errors="replace")

                    check_out = res.stdout.strip() if res.stdout else ""
                    self.log(f"  > 核心组件状态: {check_out}")

                    if "CHECK_RESULT:OK" in check_out:
                        packages_ok = True
                    elif "CHECK_RESULT:CPU_OK" in check_out:
                        self.log("✅ 无 NVIDIA 显示卡，CPU 版本组件运作正常。")
                        packages_ok = True
                    elif "CHECK_RESULT:SM120_INCOMPATIBLE" in check_out:
                        self.log("🔍 侦测到 RTX 50 系列显示卡与现有运算核心不相容，将执行强制升级。")
                        packages_ok = False
                    elif "CHECK_RESULT:WRONG_BUILD_FOR_CPU" in check_out:
                        self.log("🔍 侦测到安装的是 GPU 版本但主机无 NVIDIA 显示卡，将重装为 CPU 版本。")
                        packages_ok = False
                    else:
                        self.log("🔍 侦测到加速组件不完整或不支援 GPU，将执行修复。")
                        packages_ok = False
                except Exception as e:
                    self.log(f"⚠️ 检查过程发生异常: {str(e)}")
            else:
                missing = [n for flag, n in [(not has_torch, "PyTorch 核心组件"), (not has_sep, "音讯分离核心组件"), (not has_ort, "ONNX Runtime 核心")] if flag]
                if missing: self.log("🔍 侦测到缺少：" + "、".join(missing))
                packages_ok = False

            if not packages_ok or install_mode != "auto":
                self.update_progress(65, step_text="步骤 5/5：安装 AI 运算组件 (需数分钟)...")
                self.log(f"🚀 准备执行 AI 运算组件安装/修复 (模式: {install_mode})...")
                if not self.install_packages_locally(install_mode=install_mode):
                    self.log("❌ AI 组件安装失败，请查看上方详细日志。")
                    return
                self.log("✅ AI 组件安装/修复完成。")
                self.log("🔄 正在根据新环境自动切换运算装置...")
                self._startup_ort_check()

            self.update_progress(100, "全部就绪", step_text="")
            self._reset_ort_fix_prompt_state(clear_history=True)
            self._startup_component_prompt_shown = False
            self.update_status("准备就绪", "green")
            self.log("--- 环境部署完成 ---")
        finally:
            self.is_processing = False

    def fix_python_pth(self):
        try:
            pth_files = list(self.py_dir.glob("*._pth"))
            if not pth_files:
                self.log("⚠️ 找不到 Python .pth 设定档，跳过路径校正。")
                return
            pth_file = pth_files[0]
            with open(pth_file, "r") as f:
                lines = f.readlines()

            lines = [l.strip() for l in lines if l.strip()]
            legacy_entries = {"..\\ai_libraries", "..\\ai_libraries_cpu", "..\\ai_libraries_gpu", "..\\ai_libraries_directml", "..\\ai_libraries_common"}
            orig_len = len(lines)
            lines = [l for l in lines if l not in legacy_entries]
            removed_legacy = len(lines) < orig_len

            py_zip = next((f.name for f in self.py_dir.glob("python*.zip")), "python310.zip")

            required = [ py_zip, ".", "Lib/site-packages", "import site" ]
            needs_update = removed_legacy

            for item in required:
                if item not in lines:
                    if f"#{item}" in lines:
                        lines[lines.index(f"#{item}")] = item
                    else:
                        lines.append(item)
                    needs_update = True

            if needs_update:
                with open(pth_file, "w") as f:
                    f.write("\n".join(lines) + "\n")
                self.log("🔧 已校正 Python 路径设定档（改为执行时动态注入 CPU/GPU 套件路径）。")
        except Exception as e:
            self.log(f"⚠️ 路径校正失败: {str(e)}")

    def _prepare_download_dir(self, target_dir, label):
        """确保下载目标目录存在且可写，失败时记录错误并回传 False。"""
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"❌ 无法建立 {label} 目录: {target_dir}\n   原因: {str(e)}")
            return False
        _t = target_dir / ".write_test"
        try:
            _t.write_text("ok"); _t.unlink()
        except Exception as e:
            self.log(f"❌ {label} 目录无写入权限: {target_dir}\n   原因: {str(e)}")
            if label == "Python":
                self.log("💡 请以系统管理员身份执行程式，或更换输出目录位置。")
            return False
        return True

    def download_portable_python(self):
        py_urls = [
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip",
        ]

        if not self._prepare_download_dir(self.py_dir, "Python"):
            self.log("💡 请手动建立该资料夹，或将程式移至桌面等较短路径后重试。")
            return False

        zip_path = self.py_dir / "py.zip"
        if zip_path.exists():
            with suppress(Exception):
                zip_path.unlink()

        self._setup_ssl_opener()

        for attempt, url in enumerate(py_urls, 1):
            self.log(f"🚀 正在下载 Python 核心 (来源 {attempt}/{len(py_urls)})...")
            try:
                self._last_log_percent = -1
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)

                if not zip_path.exists() or zip_path.stat().st_size < 1024:
                    self.log(f"⚠️ 来源 {attempt} 下载的档案过小或不存在，尝试下一个...")
                    zip_path.unlink(missing_ok=True)
                    continue

                if not zipfile.is_zipfile(str(zip_path)):
                    self.log(f"⚠️ 来源 {attempt} 下载的档案损坏，尝试下一个...")
                    zip_path.unlink(missing_ok=True)
                    continue

                self.log("📦 正在解压缩 Python...")
                with zipfile.ZipFile(str(zip_path), "r") as zip_ref:
                    zip_ref.extractall(str(self.py_dir))

                self.fix_python_pth()
                zip_path.unlink(missing_ok=True)

                self.log("📦 正在安装 pip 套件管理工具...")
                get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
                get_pip_path = self.py_dir / "get-pip.py"
                try:
                    urllib.request.urlretrieve(get_pip_url, str(get_pip_path))
                    subprocess.run( [str(self.local_python), str(get_pip_path)], capture_output=True, text=True, creationflags=self.subp_flags, timeout=180 )
                    get_pip_path.unlink(missing_ok=True)
                    self.log("✅ pip 安装完成。")
                except Exception as e:
                    self.log(f"⚠️ pip 安装失败: {str(e)}")

                self.log(f"✅ Python 核心安装完成（来源 {attempt}）。")
                return True

            except Exception as e:
                err_msg = str(e)
                self.log(f"⚠️ 来源 {attempt} 下载失败: {err_msg}")
                if "No such file or directory" in err_msg:
                    self.log("   ⚠️ 写入路径失败，目标目录可能在下载过程中消失或被锁定。")
                    self.log(f"   目标路径: {zip_path}")
                zip_path.unlink(missing_ok=True)
                if attempt < len(py_urls):
                    self.log("🔄 尝试下一个备用来源...")

        self.log("❌ Python 核心所有下载来源均失败。")
        self.log("💡 可能原因：(1) 网路连线问题  (2) 防火墙封锁  (3) 磁碟空间不足")
        self.log("💡 请确认网路正常后重试，或手动下载 Python embed zip 放入 runtime_python 资料夹。")
        return False

    def _download_reporthook(self, count, block_size, total_size):
        if total_size > 0:
            percent = min(int(count * block_size * 100 / total_size), 100)
            self.update_progress(percent, "正在下载")
            if percent % 10 == 0 and percent != self._last_log_percent:
                self.log(f"  > 下载进度: {percent}%")
                self._last_log_percent = percent

    def _setup_ssl_opener(self):
        """建立忽略 SSL 验证的 urllib opener（解决 CERTIFICATE_VERIFY_FAILED）"""
        ssl_context = ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
        opener.addheaders = [('User-agent', 'Mozilla/5.0')]
        urllib.request.install_opener(opener)

    def download_ffmpeg(self):
        primary_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
        fallback_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = self.bin_dir / "ffmpeg.zip"

        if not self._prepare_download_dir(self.bin_dir, "FFmpeg"):
            return False

        for attempt, url in enumerate([primary_url, fallback_url], 1):
            try:
                self.log(f"🚀 正在连线至下载伺服器 (来源 {attempt}/2)...")
                self._setup_ssl_opener()
                self._last_log_percent = -1

                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)
                self.log("📦 正在提取 FFmpeg 引擎与共享函式库 (DLLs)...")
                with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                    for file in zip_ref.namelist():
                        normalized_file = file.replace('\\', '/')
                        if "/bin/" in normalized_file and (normalized_file.endswith(".exe") or normalized_file.endswith(".dll")):
                            filename = os.path.basename(normalized_file)
                            with zip_ref.open(file) as source, open(self.bin_dir / filename, "wb") as target:
                                shutil.copyfileobj(source, target)
                zip_path.unlink(missing_ok=True)
                return True
            except Exception as e:
                self.log(f"⚠️ 来源 {attempt} 下载失败: {str(e)}")
                zip_path.unlink(missing_ok=True)
                if attempt < 2:
                    self.log("🔄 尝试备用下载来源...")

        self.log("❌ FFmpeg 所有下载来源均失败，请检查网路连线。")
        return False

    def _clean_ai_packages_in_dir(self, target_dir):
        patterns = [ "torch*", "torchvision*", "torchaudio*", "onnxruntime*", "onnxruntime_gpu*", "onnxruntime-directml*", "audio_separator*", "nvidia*" ]
        removed_any = False
        for pattern in patterns:
            for p in target_dir.glob(pattern):
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                    removed_any = True
                except Exception as e:
                    self.log(f"  ⚠️ 清理旧组件失败: {p.name} ({str(e)})")
        if removed_any:
            self.log(f"  ✅ 已清理旧组件: {target_dir.name}")

    def _install_ai_stack(self, target_dir, target_mode="cpu", is_rtx50=False, clean=False):
        target_dir.mkdir(parents=True, exist_ok=True)

        base_pkgs = ["setuptools", "wheel", "pip", "msvc-runtime>=14.40"]
        if target_mode in ("cpu", "directml"):
            self.log(f"📦 正在部署独立 {'CPU' if target_mode == 'cpu' else 'DirectML'} AI 核心...")
            torch_index = "https://download.pytorch.org/whl/cpu"
            tv, ta = "2.5.1+cpu", "2.5.1+cpu"; tv_v = "0.20.1+cpu"
            ort_pkg = "onnxruntime==1.18.0" if target_mode == "cpu" else "onnxruntime-directml==1.18.0"
            extra = [] if target_mode == "cpu" else ["torch-directml"]
            install_steps = [base_pkgs,
                ["--extra-index-url", torch_index, f"torch=={tv}", f"torchvision=={tv_v}", f"torchaudio=={ta}",
                 ort_pkg, "audio-separator"] + extra]
        else:
            if is_rtx50:
                self.log("📦 正在部署独立 GPU AI 核心（cu128 / RTX 50）...")
                torch_index = "https://download.pytorch.org/whl/cu128"
                torch_ver, tv_v, ta_v = "2.7.1+cu128", "0.22.1+cu128", "2.7.1+cu128"
            else:
                self.log("📦 正在部署独立 GPU AI 核心（cu124）...")
                torch_index = "https://download.pytorch.org/whl/cu124"
                torch_ver, tv_v, ta_v = "2.5.1+cu124", "0.20.1+cu124", "2.5.1+cu124"
            install_steps = [
                base_pkgs,
                ["nvidia-cuda-runtime-cu12", "nvidia-cudnn-cu12", "nvidia-cublas-cu12",
                 "nvidia-curand-cu12", "nvidia-cufft-cu12", "nvidia-cuda-nvrtc-cu12", "nvidia-ml-py"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_v}", f"torchaudio=={ta_v}",
                 "onnxruntime-gpu", "audio-separator[gpu]"]]

        if clean:
            self._clean_ai_packages_in_dir(target_dir)

        pip_base_cmd = [
            str(self.local_python), "-m", "pip", "install",
            "--target", str(target_dir),
            "--upgrade",
            "--retries", "10",
            "--timeout", "100",
            "--no-warn-script-location"
        ]
        pip_env = self._build_python_env(target_dir, include_gpu_runtime=(target_mode == "gpu"))

        for i, step_pkgs in enumerate(install_steps):
            self.log(f"📦 正在执行安装进度 ({i+1}/{len(install_steps)}): {' '.join(step_pkgs[-3:])}...")
            cmd = pip_base_cmd + step_pkgs
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, creationflags=self.subp_flags, encoding='utf-8',
                errors='replace', env=pip_env)

            has_output = False
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    has_output = True
                    clean_line = line.strip()
                    if any(x in clean_line for x in ["Downloading", "Installing", "Collecting", "ERROR", "Exception", "Traceback", "Requirement already satisfied"]):
                        if "satisfied" in clean_line and len(clean_line) > 100:
                            clean_line = clean_line[:100] + "..."
                        self.log(f"  > {clean_line}")
                        if "Downloading" in clean_line:
                            self.update_status(f"正在下载组件 ({i+1}/{len(install_steps)})...", "orange")

            process.wait()
            if process.returncode != 0:
                self.log(f"❌ 第 {i+1} 阶段安装失败 (代码: {process.returncode})。")
                return False
            if not has_output and i > 0:
                self.log(f"⚠️ 第 {i+1} 阶段安装似乎没有输出，请检查环境。")

        checks = [
            ("torch",          (target_dir / "torch").exists),
            ("audio_separator",(target_dir / "audio_separator").exists),
            ("onnxruntime",    lambda: self._has_onnxruntime_package(target_dir)),
        ]
        for pkg, check in checks:
            if not check():
                self.log(f"❌ 安装程序已结束，但未能在 {target_dir.name} 中找到 {pkg}。")
                return False
        return True

    def install_packages_locally(self, install_mode="auto"):
        try:
            self.log("📥 下载 Pip 安装工具...")
            pip_script = self.py_dir / "get-pip.py"
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", pip_script)

            self.log("📥 正在安装 Pip 组件...")
            subprocess.run([str(self.local_python), str(pip_script)], creationflags=self.subp_flags, check=True)

            pip_check = subprocess.run(
                [str(self.local_python), "-m", "pip", "--version"],
                capture_output=True, text=True, creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
            )
            if pip_check.returncode != 0:
                self.log("❌ Pip 安装失败，无法继续。")
                return False
            self.log(f"✅ Pip 已就绪: {pip_check.stdout.strip()}")
            self.log(f"📥 正在准备 AI 运算环境 (模式: {install_mode})...")

            has_nvidia_gpu = self._detect_gpu_vendor() == "nvidia"
            is_rtx50 = self._is_rtx_50_series() if has_nvidia_gpu else False

            if any(ord(c) > 127 for c in str(self.app_dir)):
                self.log("⚠️ 侦测到路径中含有中文或特殊字元，这极易导致安装失败。")
                self.log("💡 强烈建议：将程式资料夹移至磁碟根目录 (例如 C:\\mp3_tool)，避免路径问题。")

            if install_mode == "gpu" and not has_nvidia_gpu:
                self.log("❌ 目前未侦测到 NVIDIA 显示卡，无法安装纯 GPU 版本。")
                return False
            _mode_map = {
                "cpu":      ("ℹ️ 使用者选择强制安装 CPU 版本。",     [(self.lib_dir, "cpu", False)]),
                "gpu":      ("ℹ️ 使用者选择强制安装 GPU 版本。",     [(self.gpu_lib_dir, "gpu", is_rtx50)]),
                "directml": ("ℹ️ 使用者选择强制安装 DirectML 版本。", [(self.directml_lib_dir, "directml", False)]),
            }
            if install_mode in _mode_map:
                msg, install_targets = _mode_map[install_mode]
                self.log(msg)
            elif install_mode == "both":
                self.log("ℹ️ 使用者选择安装 CPU + GPU 双支援版（分开存放）。")
                install_targets = [(self.lib_dir, "cpu", False)]
                if has_nvidia_gpu:
                    install_targets.append((self.gpu_lib_dir, "gpu", is_rtx50))
                else:
                    self.log("⚠️ 目前未侦测到 NVIDIA 显示卡，本次仅安装 CPU 套件。")
            else:
                if not has_nvidia_gpu:
                    self.log("ℹ️ 未侦测到 NVIDIA 显示卡，将安装 CPU 版本。")
                    install_targets = [(self.lib_dir, "cpu", False)]
                else:
                    self.log(f"{'🚀 侦测到 RTX 50 系列，将安装独立 GPU cu128 核心。' if is_rtx50 else '✅ 侦测到 NVIDIA 显示卡，将安装独立 GPU cu124 核心。'}")
                    install_targets = [(self.gpu_lib_dir, "gpu", is_rtx50)]

            for target_dir, target_mode, target_is_rtx50 in install_targets:
                if not self._install_ai_stack(target_dir, target_mode=target_mode, is_rtx50=target_is_rtx50, clean=True):
                    return False

            pip_script.unlink(missing_ok=True)
            return True
        except Exception as e:
            err_text = str(e)
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                self.log("❌ 磁碟空间不足，无法继续安装 AI 组件。")
                self.log("💡 建议先释放磁碟空间后再重试。")
                self.log("💡 若不需要 GPU 加速，请改选「仅安装 CPU 版」，所需空间会比双支援版少很多。")
            else:
                self.log(f"安装错误: {err_text}")
            return False

    def _is_rtx_50_series(self):
        """检查是否有 RTX 50 系列显示卡"""
        try:
            res = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True, text=True,
                creationflags=self.subp_flags, timeout=10,
                encoding="utf-8", errors="replace"
            )
            return res.returncode == 0 and "RTX 50" in res.stdout
        except Exception:
            return False

    def _startup_ort_check(self):
        """启动时背景执行绪：依优先顺序检查 NVIDIA GPU → DirectML → CPU 核心可用性并自动切换。"""
        if not self.local_python.exists():
            return
        if self._startup_ort_check_running:
            self.log("ℹ️ 启动环境检测已在执行中，略过重复请求。")
            return

        self._startup_ort_check_running = True
        try:
            has_nvidia_gpu = self._detect_gpu_vendor() == "nvidia"
            gpu_out = "STACK_SKIPPED"
            directml_out = "STACK_SKIPPED"
            cpu_out = "STACK_SKIPPED"

            if has_nvidia_gpu and (self.gpu_lib_dir / "torch").exists():
                gpu_out = self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True)
                if gpu_out == "ORT_OK_GPU":
                    self.log("✅ 侦测到独立 NVIDIA GPU 核心已就绪，自动切换至 GPU 模式。")
                    self.root.after(0, lambda: self.device_var.set("gpu"))
                    self._reset_ort_fix_prompt_state(clear_history=False)
                    return

            if (self.directml_lib_dir / "onnxruntime").exists():
                directml_out = self._probe_onnxruntime_stack(self.directml_lib_dir, expect_gpu=False)
                if directml_out == "ORT_OK_CPU":
                    self.log("✅ 侦测到 DirectML 核心已就绪，自动切换至 DirectML 模式。")
                    self.root.after(0, lambda: self.device_var.set("directml"))
                    self._reset_ort_fix_prompt_state(clear_history=False)
                    return

            cpu_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
            if cpu_out == "ORT_OK_CPU":
                self.log("✅ 基础环境已就绪（CPU 模式）。")
                if self.device_var.get() in ["gpu", "directml"]:
                    self.root.after(0, lambda: self.device_var.set("cpu"))
                self._reset_ort_fix_prompt_state(clear_history=False)
            else:
                self.log(f"ℹ️ CPU 核心检测结果: {cpu_out}")

            if has_nvidia_gpu:
                if gpu_out not in ["STACK_SKIPPED", "STACK_MISSING"]:
                    self.log(f"🔍 NVIDIA GPU 核心检测结果: {gpu_out}")
                if gpu_out in ["ORT_DLL_FAIL", "ORT_NO_OUTPUT"] and cpu_out == "ORT_OK_CPU":
                    self.log("⚠️ NVIDIA GPU 核心存在但无法载入，已保留 CPU 模式，不影响纯 CPU 使用。")
            if directml_out not in ["STACK_SKIPPED", "STACK_MISSING"]:
                self.log(f"🔍 DirectML 核心检测结果: {directml_out}")
            if not has_nvidia_gpu and cpu_out == "ORT_OK_CPU":
                self.log("💡 未侦测到 NVIDIA 显示卡，CPU 模式为正常运行状态。")
        except Exception as e:
            self.log(f"ℹ️ 启动时环境检测失败: {str(e)}")
        finally:
            self._startup_ort_check_running = False

    def _prompt_ort_fix(self, issue_key="gpu_runtime_fallback"):
        """弹窗询问使用者是否立即修复 onnxruntime 版本问题"""
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            self.log(f"ℹ️ [PROMPT] 已抑制修复提示，不再显示: {issue_key}")
            return
        if self._ort_fix_prompt_active:
            self.log(f"ℹ️ [PROMPT] 修复提示已在显示中: {issue_key}")
            return
        if self.is_processing:
            self._schedule_ort_fix_prompt(issue_key=issue_key, delay_ms=5000)
            return
        self._ort_fix_prompt_active = True
        self._ort_fix_prompt_shown_keys.add(issue_key)
        self.log(f"ℹ️ [PROMPT] 显示修复提示: {issue_key}")
        try:
            answer = messagebox.askyesno(
                "建议修复 AI 组件",
                "侦测到 AI 组件版本与您的系统不符（GPU 版装在无 NVIDIA 显示卡的电脑上）。\n\n"
                "目前程式已自动切换至 CPU 模式，音讯分离功能仍可正常使用。\n\n"
                "建议执行修复以取得最佳效能并避免每次启动的诊断延迟。\n"
                "（重新下载适合的 CPU 版本，约 800MB）\n\n"
                "是否立即自动修复？"
            )
            if answer:
                self._start_async_setup()
            else:
                self._ort_fix_prompt_suppressed_keys.add(issue_key)
                self.log(f"ℹ️ [PROMPT] 使用者已拒绝本次修复提示: {issue_key}")
        finally:
            self._ort_fix_prompt_active = False

    def _quick_check_gpu(self):
        """快速检测 GPU 是否可用（不弹窗）"""
        return (self.local_python.exists()
                and self._detect_gpu_vendor() == "nvidia"
                and (self.gpu_lib_dir / "torch").exists()
                and self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True) == "ORT_OK_GPU")

    def _check_gpu_before_start(self):
        """若选用 GPU，执行快速检测；不通过时询问修复或降回 CPU。回传 True 才可继续。"""
        if self.device_var.get() != "gpu":
            return True
        if not self._quick_check_gpu():
            if messagebox.askyesno("环境未就绪",
                    "侦测到您的 GPU 环境尚未配置完成，是否现在进行一键修复？\n(若不修复将改用 CPU 运行，速度较慢)"):
                self.check_gpu_env()
                return False
            self.log("⚠️ 使用者选择忽略，将尝试改用 CPU 模式。")
            self.device_var.set("cpu")
        return True

    def start_separation(self):
        if not self.file_list:
            messagebox.showwarning("警告", "请先加入音档！")
            return
        if not self._check_gpu_before_start():
            return
        self._begin_processing("正在处理中...", self.batch_process)

    def start_yt_process(self):
        url = self.yt_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入 YouTube 网址！")
            return
        if not self._check_gpu_before_start():
            return
        self._begin_processing("正在从 YouTube 下载并处理...", self.yt_process, url)

    def yt_process(self, url):
        start_time = time.time()
        output_dir = self.output_dir_var.get()
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self._last_downloaded_subtitle = None
        subtitle_mode = self.yt_subtitle_mode_var.get() if self.yt_cc_var.get() else "none"

        self.log(f"--- 正在处理 YouTube 影片: {url} ---")
        self.update_progress(5, "正在获取影片资讯", step_text="步骤 1/5：获取影片资讯")
        if subtitle_mode == "srt_only":
            self.log("📝 字幕模式：只抓 SRT，不封装进成品")
        elif subtitle_mode == "mux":
            self.log("📝 字幕模式：抓字幕并合成到成品")

        yt_quality = self.yt_quality_var.get()
        self.log(f"🚀 开始下载任务 (格式: BOTH，画质: {yt_quality})...")
        dl_result = self.download_youtube(
            url, output_dir, mode="both",
            quality=yt_quality,
            download_subtitles=(subtitle_mode in ("srt_only", "mux")),
        )
        video_file, audio_file = dl_result if dl_result else (None, None)

        if not video_file or not audio_file:
            self.log("❌ YouTube 影片下载失败。" if not video_file else "❌ 音讯撷取失败。")
            self.finish_processing()
            return

        self.update_progress(40, "正在分离人声与伴奏", step_text="步骤 3/5：AI 人声分离中")

        success = self.run_audio_separator(audio_file, output_dir)

        if success:
            enable_lyrics = self.enable_lyrics_recognition_var.get()
            srt_subtitle = json_subtitle = None
            if enable_lyrics:
                self.update_progress(70, "正在识别歌词", step_text="步骤 4/5：AI 歌词辨识中")
                srt_subtitle, json_subtitle = self._recognize_and_rename_lyrics(audio_file, output_dir, Path(video_file).stem)

            self.log("📦 正在整理并重新命名产出档案...")
            voc_file, inst_file = self.consolidate_stems(audio_file, video_file, output_dir)

            if voc_file and inst_file:
                vfmt = self.video_format_var.get()
                self.update_progress(80, f"正在合成 {vfmt.upper()} 伴唱带", step_text=f"步骤 5/5：合成 {vfmt.upper()} 伴唱带")
                output_file = Path(output_dir) / f"{Path(video_file).stem}_KTV.{vfmt}"

                subtitle_for_mux = self._last_downloaded_subtitle if subtitle_mode == "mux" else None
                if srt_subtitle and not subtitle_for_mux:
                    subtitle_for_mux = srt_subtitle

                mkv_success = self.synthesize_mkv( video_file, voc_file, inst_file, str(output_file), subtitle_file=subtitle_for_mux )

                if mkv_success:
                    self.log(f"✅ 成功生成 {vfmt.upper()} 伴唱带: {output_file.name}")
                    if self._last_downloaded_subtitle:
                        self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, str(output_file))

                    self.update_progress(100, "处理完成", step_text="✅ 完成！")
                    elapsed_time = time.time() - start_time
                    self.log(f"⏱️ YouTube 处理完成，总花费时间: {elapsed_time:.2f} 秒")
                    messagebox.showinfo("成功", f"YouTube 处理完成！\n总花费时间: {elapsed_time:.2f} 秒\n档案已储存至: {output_dir}")
                    if os.name == 'nt' and os.path.exists(output_dir):
                        self._open_folder_no_dup(str(output_dir))
                else:
                    self.log("❌ MKV 合成失败。")
            else:
                self.log("❌ 找不到分离后的必要档案 (人声或伴奏)。")
        else:
            self.log("❌ 音讯分离失败。")

        self.finish_processing()

    def consolidate_stems(self, input_audio, reference_video, output_dir):
        """整理分离后的音轨：重新命名人声，并合并多音轨为伴奏 (针对 Demucs)"""
        fmt = self.output_format_var.get()
        video_stem = Path(reference_video).stem
        out_path = Path(output_dir)

        voc_final  = out_path / f"{MyKTVApp.sanitize_filename(video_stem, max_len=60)}_人声.{fmt}"
        inst_final = out_path / f"{MyKTVApp.sanitize_filename(video_stem, max_len=60)}_伴奏.{fmt}"

        # 直接用關鍵字搜尋，不依賴前綴比對，
        # 避免 audio-separator 把連續底線壓縮後導致 startswith 失敗。
        voc_kw    = ["(Vocals)"]
        inst_kw   = ["(Instrumental)", "(No Vocals)"]
        demucs_kw = ["(Bass)", "(Drums)", "(Other)", "(Guitar)", "(Piano)",
                     "_Bass", "_Drums", "_Other", "_Guitar", "_Piano"]
        all_kw = voc_kw + inst_kw + demucs_kw

        all_sep_files = [
            f for f in out_path.iterdir()
            if f.suffix == f".{fmt}" and any(kw in f.name for kw in all_kw)
        ]

        for f in all_sep_files:
            if any(kw in f.name for kw in voc_kw):
                if voc_final.exists(): voc_final.unlink()
                f.rename(voc_final); break

        found_inst = False
        for f in all_sep_files:
            if any(kw in f.name for kw in inst_kw):
                if inst_final.exists(): inst_final.unlink()
                f.rename(inst_final); found_inst = True; break

        if not found_inst:
            stems_to_merge = [f for f in all_sep_files if any(kw in f.name for kw in demucs_kw)]

            if stems_to_merge:
                stem_names = ", ".join(sorted(f.name for f in stems_to_merge))
                self.log(f"  > 侦测到 Demucs 多音轨，正在合并 {len(stems_to_merge)} 个音轨为伴奏...")
                self.log(f"  > 参与合并的音轨: {stem_names}")
                inputs = []
                for f in stems_to_merge:
                    inputs.extend(["-i", str(f)])

                filter_str = "".join([f"[{i}:a]" for i in range(len(stems_to_merge))])
                filter_str += f"amix=inputs={len(stems_to_merge)}:duration=first:normalize=0[out]"

                merge_cmd = [str(self.bin_dir / "ffmpeg.exe"), "-y"] + inputs + \
                           ["-filter_complex", filter_str, "-map", "[out]", "-b:a", "320k", str(inst_final)]

                try:
                    subprocess.run(merge_cmd, check=True, creationflags=self.subp_flags)
                    found_inst = True
                except Exception as e:
                    self.log(f"  ❌ 合并音轨失败: {str(e)}")

        self.log("🧹 正在清理暂存档案...")
        for f in out_path.iterdir():
            if f.suffix == f".{fmt}" and any(kw in f.name for kw in all_kw) and '_karaoke.mp3' not in f.name:
                try: f.unlink()
                except Exception as e:
                    self.log(f"  ⚠️ 清理暂存档失败: {f.name} ({str(e)})")
        if Path(input_audio).exists():
            try: os.remove(input_audio)
            except Exception as e:
                self.log(f"  ⚠️ 清理原始音档失败: {str(e)}")

        return (str(voc_final) if voc_final.exists() else None,
                str(inst_final) if inst_final.exists() else None)

    def _recognize_and_rename_lyrics(self, audio_file, output_dir, stem):
        """辨识歌词并将输出档案更名为 {stem}_KTV.srt/json，回传 (srt, json)"""
        result = self.recognize_lyrics_and_generate_srt(str(audio_file), output_dir, output_stem=stem)
        if not result:
            return None, None
        srt, json_sub, _ = result
        # Whisper 腳本內部會對 output_stem 做 sanitize_filename，
        # 但 original_stem（PH_9）注入的是未 sanitize 的原始值，
        # 兩者可能不一致導致 srt/json 實際路徑不同。
        # 以 sanitize 後的 stem 作為 rename 目標的基底，確保能找到來源檔。
        safe_stem = self.sanitize_filename(stem, max_len=80)
        out = Path(output_dir)
        # 如果回傳路徑不存在，改用 safe_stem 路徑補找
        if srt and not Path(srt).exists():
            candidate = out / f"{safe_stem}.srt"
            if candidate.exists():
                srt = str(candidate)
        if json_sub and not Path(json_sub).exists():
            candidate = out / f"{safe_stem}.json"
            if candidate.exists():
                json_sub = str(candidate)
        srt = self._rename_output_file(srt, out / f"{safe_stem}_KTV.srt", "SRT 字幕档")
        if json_sub:
            json_sub = self._rename_output_file(json_sub, out / f"{safe_stem}_KTV.json", "JSON 歌词档")
        return srt, json_sub

    def _set_merge_cancel_btn(self, state):
        if hasattr(self, "merge_cancel_btn"):
            with suppress(Exception): self.merge_cancel_btn.config(state=state)

    def _begin_processing(self, status_msg, target_fn, *args):
        """通用：锁定 UI、清除日志、启动背景执行绪"""
        if self.is_processing:
            return False
        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self._set_merge_cancel_btn(tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status(status_msg, "orange")
        threading.Thread(target=target_fn, args=args, daemon=True).start()
        return True

    def finish_processing(self):
        self.is_processing = False
        self.cancel_event.clear()
        self._current_process = None
        def _ui_reset():
            self.start_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)
            self._set_merge_cancel_btn(tk.DISABLED)
            self.refresh_start_button_text()
        self.root.after(0, _ui_reset)
        self.update_status("准备就绪", "green")
        self.update_progress(0, step_text="")

    def cancel_processing(self):
        """中止当前正在执行的任务"""
        if not self.is_processing:
            return
        self.cancel_event.set()
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
                self.log("⚠️ 已发送中止讯号给子程序...")
            except Exception as e:
                self.log(f"⚠️ 中止子程序时出错: {str(e)}")
        self.log("🛑 使用者已取消任务。")
        self.cancel_btn.config(state=tk.DISABLED)
        self._set_merge_cancel_btn(tk.DISABLED)

    @staticmethod
    def sanitize_filename(title, max_len=80):
        """将标题截短并清除 Windows 非法字元，避免路径过长导致各种失败"""
        for ch in r'\/:*?"<>|':
            title = title.replace(ch, '_')
        title = re.sub(r'[\s_]+', '_', "".join(c for c in title if c.isprintable())).strip('_')
        title = title[:max_len]
        while len(title.encode('utf-8', errors='replace')) > 180:
            title = title[:-1]
        return title.strip() or 'video'

    def extract_audio_from_video(self, video_file, mp3_out):
        """用 ffmpeg 从影片档抽取 MP3 音讯，回传输出路径或 None"""
        cmd = [str(self.ffmpeg_exe), "-y", "-i", video_file,
               "-vn", "-acodec", "libmp3lame", "-ab", "320k", mp3_out]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                encoding='utf-8', errors='replace', creationflags=self.subp_flags)
            self._current_process = proc
            for line in proc.stdout:
                if self.cancel_event.is_set():
                    proc.terminate()
                    self._current_process = None
                    return None
                line = line.strip()
                if line:
                    self.log(f"    {line}")
            proc.wait()
            self._current_process = None
            if proc.returncode == 0 and os.path.exists(mp3_out):
                self.log(f"  ✅ 音讯撷取完成: {os.path.basename(mp3_out)}")
                return mp3_out
            else:
                self.log("  ❌ ffmpeg 音讯撷取失败")
                return None
        except Exception as e:
            self.log(f"  ❌ 音讯撷取出错: {str(e)}")
            return None

    def upscale_video_to_1080p(self, input_video: str, *, replace_original: bool = True) -> str:
        """
        强制等比输出 1080p（不足补黑边）。
        - 主要用于「纯下载」分页：下载到的 MP4 若不是 1920x1080，也会做一次缩放输出。
        - replace_original=True：成功后会覆盖原影片（先输出暂存档，再取代原档名），不留 _1080p 结尾档。
        - 回传：成功则回传（覆盖后的）原档案路径；失败则回传原档案路径。
        """
        try:
            if not input_video or (not os.path.exists(input_video)):
                return input_video

            # 既有程式使用 _get_video_size() 取得解析度
            w, h = self._get_video_size(input_video)
            if w == 1920 and h == 1080:
                self.log("🖼️ 影片已是 1080p（1920x1080），跳过放大。")
                return input_video

            in_path = Path(input_video)
            # 一律先輸出暫存檔，再視需求覆蓋原檔（避免直接覆蓋失敗造成檔案損毀）
            tmp_out = in_path.with_name(f"{in_path.stem}__tmp_1080p_{int(time.time())}.mp4")

            scale_filter = self._get_1080p_scale_pad_filter()
            cmd = [
                str(self.ffmpeg_exe), "-y",
                "-i", str(in_path),
                "-vf", scale_filter,
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(tmp_out)
            ]

            self.log("🖼️ 正在将影片缩放为 1080p（等比＋补黑边）...")
            subprocess.run(cmd, check=True, creationflags=self.subp_flags, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
            if tmp_out.exists():
                if replace_original:
                    try:
                        os.replace(str(tmp_out), str(in_path))  # 覆蓋原檔（Windows 也可用）
                        self.log(f"  ✅ 1080p 放大完成（已覆盖原档）: {in_path.name}")
                        return str(in_path)
                    except Exception as e:
                        # 覆蓋失敗就保留暫存檔，避免白做
                        self.log(f"  ⚠️ 覆盖原档失败，已保留放大后档案: {tmp_out.name}")
                        self.log(f"     错误讯息: {str(e)}")
                        return str(tmp_out)
                else:
                    self.log(f"  ✅ 1080p 影片已输出: {tmp_out.name}")
                    return str(tmp_out)
            return input_video
        except subprocess.CalledProcessError as e:
            self.log("  ❌ 1080p 放大失败（ffmpeg 回传错误）")
            self._log_ffmpeg_stderr(e)
            return input_video
        except Exception as e:
            self.log(f"  ❌ 1080p 放大失败: {str(e)}")
            return input_video

    def download_youtube(self, url, output_dir, mode="both", download_subtitles=False, quality="1080"):
        """使用 yt-dlp 下载影片与音讯"""
        self.log("🚀 正在下载 YouTube 内容...")
        self._last_downloaded_subtitle = None

        # 方法3：自動清洗網址，移除播放清單/電台等多餘參數，確保抓單一影片
        clean_url = self.clean_youtube_url(url)
        if clean_url != url:
            self.log(f"  🔗 已清洗网址（移除播放清单参数）: {clean_url}")
            url = clean_url

        video_id = self.extract_youtube_video_id(url) or "temp_id"

        ytdlp_cmd_base = self._get_ytdlp_command_base()
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = os.pathsep.join([str(self.ytdlp_dir), str(self.common_lib_dir), str(self.lib_dir)])
        js_runtime_opts = self._get_ytdlp_js_runtime_opts() if download_subtitles else []

        # 還原舊版 common_opts：不加 extractor-args / user-agent，讓 yt-dlp 用預設方式取得高畫質
        _base_opts = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "2",
            "--sleep-interval", "3",
        ]
        common_opts_with_cookie = _base_opts + js_runtime_opts + self._get_cookie_opts(force_no_cookie=False)
        common_opts_no_cookie   = _base_opts + js_runtime_opts + self._get_cookie_opts(force_no_cookie=True)
        # 若目錄下有 cookies.txt 也自動套用
        _cookies_txt = self.app_dir / "cookies.txt"
        if _cookies_txt.exists():
            common_opts_with_cookie += ["--cookies", str(_cookies_txt)]

        def run_ytdlp_with_logging(cmd, step_name):
            self.log(f"  > 正在下载 {step_name}...")
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=self.subp_flags,
                encoding='utf-8', errors='replace', env=ytdlp_env)
            self._current_process = process

            last_percent = -1
            recent_errors = []
            recent_lines = []
            has_cookie_error = False
            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    return False, ["使用者取消"], False
                line = line.strip()
                if not line: continue
                recent_lines = (recent_lines + [line])[-12:]

                if "could not copy chrome cookie" in line.lower() or "cookie database" in line.lower():
                    has_cookie_error = True

                if "[download]" in line and "%" in line:
                    match = re.search(r"(\d+\.\d+)%", line)
                    if match:
                        percent = float(match.group(1))
                        if int(percent) > last_percent:
                            self.log(f"    {line}")
                            last_percent = int(percent)
                            mapped = 5 + int(percent * 0.23)
                            self.update_progress(mapped, f"正在下载 {step_name}", step_text=f"步骤 1/5：下载 {step_name} {int(percent)}%")
                elif any(x in line for x in ["[ffmpeg]", "Merging", "Extracting", "Destination"]):
                    self.log(f"    {line}")
                elif "ERROR" in line.upper():
                    self._log_ytdlp_error(line)
                    recent_errors = (recent_errors + [line])[-6:]

            process.wait()
            self._current_process = None
            if process.returncode != 0 and recent_errors:
                self.log(f"  ⚠️ {step_name} 失败摘要：{recent_errors[-1][:220]}")
            elif process.returncode != 0 and recent_lines:
                self.log(f"  ⚠️ {step_name} 最后输出：{recent_lines[-1][:220]}")
            return process.returncode == 0, (recent_errors or recent_lines), has_cookie_error

        def find_downloaded_file(pattern):
            """使用 glob 寻找包含特定 ID 的档案，解决 Windows 编码导致的路径变数乱码问题"""
            files = list(Path(output_dir).glob(pattern))
            if files:
                files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return str(files[0])
            return None

        video_file = None
        audio_file = None

        safe_name = video_id  # fallback
        try:
            for try_cookie in [True, False]:
                ytdlp_env_info = ytdlp_env.copy()
                ytdlp_env_info["PYTHONIOENCODING"] = "utf-8"
                title_cmd = ytdlp_cmd_base + ["--no-playlist"] + js_runtime_opts + [
                    "--extractor-args", "youtube:player_client=mweb,android",
                    "--user-agent", "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
                    "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "--add-header", "Accept-Language:en-US,en;q=0.9",
                    "--add-header", "Referer:https://www.youtube.com/",
                    "--add-header", "Origin:https://www.youtube.com",
                ] + self._get_cookie_opts(force_no_cookie=not try_cookie) + ["--print", "%(title)s", url]
                title_result = subprocess.run(
                    title_cmd,
                    capture_output=True, text=True, creationflags=self.subp_flags,
                    timeout=30, encoding='utf-8', errors='replace', env=ytdlp_env_info
                )
                raw_title = title_result.stdout.strip().splitlines()[0] if title_result.stdout.strip() else ""
                if raw_title:
                    raw_title = raw_title.replace('[', '(').replace(']', ')')
                    safe_name = self.sanitize_filename(raw_title, max_len=80)
                    self.log(f"  📝 影片标题: {raw_title}")
                    self.log(f"  📝 安全档名: {safe_name}")
                    break
        except Exception as e:
            self.log(f"  ⚠️ 取得标题失败，使用影片 ID 作为档名: {str(e)}")

        if download_subtitles and mode in ["both", "mp4"]:
            self._last_downloaded_subtitle = self.download_youtube_subtitle(url, output_dir, video_id)

        if mode in ["both", "mp4"]:
            mp4_out = os.path.join(output_dir, f"{safe_name}.mp4")

            # 每次下載前刪除同名舊檔，避免 yt-dlp 因檔案已存在而跳過（直接用舊的低畫質檔）
            with suppress(Exception):
                if os.path.exists(mp4_out):
                    os.remove(mp4_out)
                    self.log("  🗑️ 已移除同名旧档，重新下载。")

            # 第一優先：舊版方式（限 ext=mp4，正常情況高畫質）
            # 若遇到限制保護導致找不到 mp4 流，後續方案改用不限 ext 讓 ffmpeg 合併
            if quality == "best":
                video_format_primary  = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                video_format_fallback = "bestvideo+bestaudio/best"
                video_format_loose    = "bestvideo+bestaudio/best"
            else:
                video_format_primary  = (f"bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]"
                                         f"/best[ext=mp4][height<={quality}]/best[ext=mp4]/best")
                video_format_fallback = (f"bestvideo[height<={quality}]+bestaudio"
                                         f"/bestvideo[height<={quality}]+bestaudio[ext=m4a]"
                                         f"/best[height<={quality}]/best")
                # tv/tv_embedded/android_vr 的格式 height 標記較不穩定；
                # 方法1：先嘗試帶高度限制抓高畫質，再 fallback 到不限制（讓 yt-dlp 自動選）
                video_format_loose    = (f"bestvideo[height<={quality}]+bestaudio"
                                         f"/bestvideo[height<={quality}]+bestaudio[ext=m4a]"
                                         f"/bestvideo+bestaudio/best")

            current_common_opts = common_opts_with_cookie
            try_no_cookie = True

            mp4_ok = False
            last_mp4_errors = []

            while try_no_cookie:
                try_no_cookie = False

                # 嘗試順序：
                # 1. 預設：舊版高畫質方式（無額外 extractor-args）
                # 2. Deno js-runtime 模式：搭配 bestvideo+bestaudio，解決 n challenge / 兒童影片限制
                local_deno = self.ytdlp_dir / ("deno.exe" if sys.platform == "win32" else "deno")
                _deno_js_opts = (["--js-runtimes", f"deno:{local_deno}"]
                                 if local_deno.exists() else self._get_ytdlp_js_runtime_opts())

                mp4_attempts = [
                    (
                        "MP4 影片（预设，高画质）",
                        ytdlp_cmd_base + current_common_opts + [
                            "-f", video_format_primary,
                            "--merge-output-format", "mp4",
                            "-o", mp4_out,
                            url
                        ]
                    ),
                    # 方法2：Deno js-runtime 模式，解決 n challenge 與兒童影片高畫質限制
                    (
                        "MP4 模式2（Deno js-runtime，儿童影片/n challenge 解锁）",
                        ytdlp_cmd_base + current_common_opts + _deno_js_opts + [
                            "-f", video_format_loose,
                            "--merge-output-format", "mp4",
                            "-o", mp4_out,
                            url
                        ]
                    ),
                ]

                for idx, (attempt_name, mp4_cmd) in enumerate(mp4_attempts, start=1):
                    if idx == 1:
                        self.log(f"  📥 尝试下载方法 {idx}/{len(mp4_attempts)}：{attempt_name}")
                    else:
                        self.log(f"  ℹ️ 预设方式失败，切换至方法 {idx}/{len(mp4_attempts)}：{attempt_name}")
                    success, err_lines, has_cookie_error = run_ytdlp_with_logging(mp4_cmd, attempt_name)
                    last_mp4_errors = err_lines

                    if has_cookie_error and current_common_opts is common_opts_with_cookie:
                        self.log("  ℹ️ 侦测到 Cookie 错误，正在切换到无 Cookie 模式重试...")
                        current_common_opts = common_opts_no_cookie
                        try_no_cookie = True
                        break

                    if not success:
                        continue

                    video_file = mp4_out if os.path.exists(mp4_out) else find_downloaded_file("*.mp4")
                    if video_file:
                        mp4_ok = True
                        self.log(f"  ✅ MP4 下载完成（方法 {idx}：{attempt_name}）：{os.path.basename(video_file)}")
                        if self._last_downloaded_subtitle:
                            self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, video_file)
                        break

                if mp4_ok:
                    break

            if not mp4_ok:
                if last_mp4_errors:
                    self.log(f"  ❌ MP4 下载失败摘要：{last_mp4_errors[-1][:220]}")
                self.log("  ❌ MP4 下载过程出错")
                if mode == "both": return None, None
                else: return None
            elif not video_file:
                self.log("  ❌ MP4 下载失败: 找不到下载后的档案")
                if mode == "both": return None, None
                else: return None

        if mode in ["both", "mp3"]:
            self.log("  > 正在准备 MP3 音讯...")
            mp3_out = os.path.join(output_dir, f"{safe_name}.mp3")
            mp3_ok = False

            # mode=both 且已有 MP4：直接用 ffmpeg 從 MP4 抽音軌，省去重複下載
            if mode == "both" and video_file and os.path.exists(video_file):
                self.log("  > 已有 MP4，直接用 ffmpeg 从影片抽取 MP3...")
                try:
                    extract_cmd = [
                        str(self.ffmpeg_exe), "-y", "-i", video_file,
                        "-vn", "-acodec", "libmp3lame", "-ab", "320k", mp3_out
                    ]
                    proc = subprocess.Popen(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding='utf-8', errors='replace', creationflags=self.subp_flags)
                    self._current_process = proc
                    for line in proc.stdout:
                        if self.cancel_event.is_set():
                            proc.terminate()
                            self._current_process = None
                            return video_file, None
                        line = line.strip()
                        if line: self.log(f"    {line}")
                    proc.wait()
                    self._current_process = None
                    if proc.returncode == 0 and os.path.exists(mp3_out):
                        audio_file = mp3_out
                        mp3_ok = True
                        self.log(f"  ✅ MP3 抽取完成: {os.path.basename(mp3_out)}")
                    else:
                        self.log("  ⚠️ ffmpeg 抽取失败，改用 yt-dlp 下载...")
                except Exception as e:
                    self.log(f"  ⚠️ ffmpeg 抽取出错: {e}，改用 yt-dlp 下载...")

            if not mp3_ok:
                current_common_opts_mp3 = common_opts_with_cookie
                while True:
                    mp3_cmd = ytdlp_cmd_base + current_common_opts_mp3 + [
                        "-f", "bestaudio/best",
                        "-x", "--audio-format", "mp3", "--audio-quality", "320K",
                        "--keep-video",
                        "-o", mp3_out, url
                    ]
                    success, err_lines, has_cookie_error = run_ytdlp_with_logging(mp3_cmd, "MP3 音讯")

                    if has_cookie_error and current_common_opts_mp3 is common_opts_with_cookie:
                        self.log("  ℹ️ 侦测到 Cookie 错误，正在切换到无 Cookie 模式重试...")
                        current_common_opts_mp3 = common_opts_no_cookie
                        continue

                    if success:
                        audio_file = mp3_out if os.path.exists(mp3_out) else find_downloaded_file("*.mp3")
                        if audio_file:
                            self.log(f"  ✅ MP3 下载完成: {os.path.basename(audio_file)}")
                            mp3_ok = True
                            for webm_f in Path(output_dir).glob(f"{safe_name}*.webm"):
                                with suppress(Exception):
                                    webm_f.unlink()
                                    self.log(f"  🗑️ 已删除暂存档: {webm_f.name}")
                    break

            if not mp3_ok:
                self.log("  ❌ MP3 下载过程出错")
                if mode == "both": return video_file, None
                else: return None

        if mode == "both":
            return video_file, audio_file
        else:
            return video_file if mode == "mp4" else audio_file

    def download_youtube_subtitle(self, url, output_dir, video_id):
        """下载 YouTube 字幕，若同时存在多语字幕则让使用者选择。"""
        self.log("  > 正在检查 YouTube CC 字幕...")

        ytdlp_cmd_base = self._get_ytdlp_command_base()

        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)
        ytdlp_env["PYTHONIOENCODING"] = "utf-8"
        js_runtime_opts = self._get_ytdlp_js_runtime_opts()
        cookie_opts = self._get_cookie_opts()

        subtitle_out = os.path.join(output_dir, f"{video_id}.%(ext)s")
        subtitle_patterns = [ f"{video_id}*.srt", f"{video_id}*.vtt", f"{video_id}*.ass", f"{video_id}*.srv3", ]
        def clear_old_subtitles():
            for pat in subtitle_patterns:
                for f in Path(output_dir).glob(pat):
                    try: f.unlink()
                    except Exception: pass

        def collect_subtitle_candidates():
            subtitle_candidates = []
            for pattern in subtitle_patterns:
                subtitle_candidates.extend(Path(output_dir).glob(pattern))
            return sorted(
                {str(path): path for path in subtitle_candidates}.values(),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

        def get_available_langs_from_metadata():
            """取得所有可用的字幕语言清单，并标注是否为手动字幕"""
            lang_display_map = {
                "zh-TW": "繁体中文 (台湾)",
                "zh-Hant": "繁体中文",
                "zh-HK": "繁体中文 (香港)",
                "cmn-Hant": "繁体中文",
                "zh-CN": "简体中文 (中国)",
                "zh-Hans": "简体中文",
                "cmn-Hans": "简体中文",
                "zh": "中文",
                "en": "英文",
                "ja-orig": "日文 (原始)",
                "ja": "日文",
                "ko": "韩文",
                "fr": "法文",
                "de": "德文",
                "es": "西班牙文",
                "pt": "葡萄牙文",
                "it": "义大利文",
                "ru": "俄文",
                "ar": "阿拉伯文",
                "hi": "印地文",
                "th": "泰文",
                "vi": "越南文",
                "id": "印尼文",
                "ms": "马来文",
                "tl": "他加禄语",
            }

            metadata_cmd = ytdlp_cmd_base + [
            "--skip-download",
            "--no-playlist",
            "--dump-single-json",
            "--retries", "10",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "2",
            "--sleep-interval", "3"
        ] + js_runtime_opts + cookie_opts + [url]

            try:
                result = subprocess.run(
                    metadata_cmd,
                    capture_output=True,
                    text=True,
                    creationflags=self.subp_flags,
                    timeout=60,
                    encoding='utf-8',
                    errors='replace',
                    env=ytdlp_env
                )
                if result.returncode != 0 or not result.stdout.strip():
                    err_preview = (result.stderr or result.stdout or "").strip()
                    if err_preview:
                        self.log(f"  ⚠️ 读取字幕语言清单失败（代码 {result.returncode}）：{err_preview[:180]}")
                    return []

                data = json.loads(result.stdout)
                manual = {k for k in (data.get("subtitles") or {}).keys() if k and k != "live_chat"}
                auto = {k for k in (data.get("automatic_captions") or {}).keys() if k and k != "live_chat"}

                langs_with_info = []
                for lang in sorted(manual):
                    langs_with_info.append((lang, lang_display_map.get(lang, lang), True))
                for lang in sorted(auto):
                    if lang not in manual:
                        langs_with_info.append((lang, lang_display_map.get(lang, lang), False))
                return langs_with_info
            except Exception as e:
                self.log(f"  ⚠️ 读取字幕语言清单失败，改用精简策略重试：{str(e)}")
                return []

        def show_language_selection_dialog(langs_with_info):
            """显示语言选择对话框，让使用者选择要下载的字幕语言"""
            selected_lang = [None]

            dialog = tk.Toplevel(self.root)
            dialog.title("选择字幕语言")
            dialog.geometry("500x400")
            dialog.transient(self.root)
            dialog.grab_set()

            dialog.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")

            tk.Label(dialog, text="请选择要下载的字幕语言：", font=("Arial", 12)).pack(pady=10)

            listbox_frame = tk.Frame(dialog)
            listbox_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

            listbox = tk.Listbox(listbox_frame, font=("Arial", 11))
            scrollbar = tk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=listbox.yview)
            listbox.configure(yscrollcommand=scrollbar.set)

            listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            for i, (lang_code, display_name, is_manual) in enumerate(langs_with_info):
                tag = "手动字幕" if is_manual else "自动字幕"
                listbox.insert(tk.END, f"{display_name} ({lang_code}) - {tag}")
                if is_manual:
                    listbox.itemconfig(i, {'fg': 'blue'})

            if langs_with_info:
                listbox.selection_set(0)

            def on_select():
                if listbox.curselection():
                    idx = listbox.curselection()[0]
                    selected_lang[0] = langs_with_info[idx][0]
                dialog.destroy()

            def on_cancel():
                dialog.destroy()

            btn_frame = tk.Frame(dialog)
            btn_frame.pack(pady=15)

            tk.Button(btn_frame, text="确定", command=on_select, bg="#4CAF50", fg="white", width=12).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="取消", command=on_cancel, width=12).pack(side=tk.LEFT, padx=10)

            self.root.wait_window(dialog)
            return selected_lang[0]

        langs_with_info = get_available_langs_from_metadata()

        if not langs_with_info:
            self.log("  ℹ️ 这支影片没有可用的 YouTube CC 字幕。")
            return None

        self.log(f"  ℹ️ 找到 {len(langs_with_info)} 种可用字幕语言")

        selected_lang = None
        try:
            selected_lang = show_language_selection_dialog(langs_with_info)
        except Exception as e:
            self.log(f"  ⚠️ 显示语言选择对话框失败：{str(e)}")

        if not selected_lang:
            self.log("  ℹ️ 使用者取消字幕下载。")
            return None

        self.log(f"  > 已选择字幕语言：{selected_lang}")

        clear_old_subtitles()
        cmd = ytdlp_cmd_base + [
            "--skip-download",
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", selected_lang,
            "--sub-format", "srt/best",
            "--convert-subs", "srt",
            "-o", subtitle_out,
        ] + js_runtime_opts + cookie_opts + [url]

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            creationflags=self.subp_flags, encoding='utf-8', errors='replace', env=ytdlp_env)
        self._current_process = process

        for line in process.stdout:
            if self.cancel_event.is_set():
                process.terminate()
                self.log("🛑 字幕下载已取消。")
                self._current_process = None
                return None
            line = line.strip()
            if not line: continue
            if any(token in line for token in ["subtitle", "Subtitles", "Writing video subtitles", "Deleting original file"]):
                self.log(f"    {line}")
            elif "WARNING" in line.upper(): self.log(f"  ⚠️ {line}")
            elif "ERROR"   in line.upper(): self.log(f"  ❌ {line}")

        process.wait()
        self._current_process = None
        subtitle_candidates = collect_subtitle_candidates()

        if subtitle_candidates:
            if process.returncode != 0:
                self.log("  ⚠️ 字幕下载程序部分失败，但已找到可用字幕档，将直接采用。")
            selected = subtitle_candidates[0]
            self.log(f"  ✅ 已找到字幕档：{selected.name}")
            return str(selected)
        self.log(f"  ⚠️ 选择的语言 {selected_lang} 没有可下载字幕。")
        return None

    def synthesize_mkv(self, video_file, vocal_file, instrumental_file, output_file, subtitle_file=None):
        """合成 KTV 伴唱带：支援双音轨模式与左伴唱/右人声单音轨模式"""
        vfmt = self.video_format_var.get().upper()
        track_mode = self.audio_track_mode_var.get()  # "dual" or "lr"
        vocal_mix = max(0.0, min(1.0, float(self.vocal_volume_var.get()) / 100.0))
        instrumental_mix = max(0.0, min(1.0, float(self.instrumental_volume_var.get()) / 100.0))
        vocal_pct = int(round(vocal_mix * 100))
        inst_pct = int(round(instrumental_mix * 100))
        force_1080p = self.force_1080p_var.get()

        self.log(f"🎬 正在合成 {vfmt} 伴唱带（音轨模式：{'双音轨' if track_mode == 'dual' else '左伴唱/右人声+伴奏' if track_mode == 'lr' else '纯伴唱'}）...")
        if track_mode == "dual":
            self.log(f"🎚️ 导唱混合比例：人声 {vocal_pct}% / 伴奏 {inst_pct}%")
        elif track_mode == "inst":
            self.log("🎚️ 纯伴唱模式：仅输出伴奏音轨，不含人声。")
        else:
            self.log("🎚️ 目前为左伴唱／右人声+伴奏模式，混合比例设定不套用于此模式。")
        if force_1080p:
            self.log("🖼️ 已启用强制等比输出 1080p，必要时会补黑边。")

        missing = [f"{lbl}: {os.path.basename(p)}" for lbl, p in [
            ("影片档", video_file), ("人声档", vocal_file), ("伴奏档", instrumental_file)
        ] if not os.path.exists(p)]
        if missing:
            self.log("  ❌ 合成失败，缺少必要档案：\n    - " + "\n    - ".join(missing))
            return False

        cmd = [str(self.ffmpeg_exe), "-y", "-i", str(video_file)]

        if track_mode == "lr":
            lr_tmp = Path(tempfile.mktemp(suffix="_lr_stereo.mp3"))
            self.log("🎚️ 正在制作「左伴奏／右人声+伴奏」立体声音轨...")
            if not self.create_lr_stereo(str(instrumental_file), str(vocal_file), str(lr_tmp)):
                self.log("  ❌ 无法制作 LR 立体声音轨，合成中止。")
                return False

            cmd += ["-i", str(lr_tmp)]   # 1: 已完成的 LR 立體聲

            audio_filter = None          # 不需要音訊 filter
            audio_maps = ["-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "title=左伴唱／右人声+伴奏"]
        elif track_mode == "inst":
            cmd += ["-i", str(instrumental_file)]   # 1: 伴奏

            audio_filter = None
            audio_maps = ["-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "title=伴唱 (纯伴奏)"]
        else:
            cmd += ["-i", str(vocal_file), "-i", str(instrumental_file)]
            audio_filter = f"[1:a]volume={vocal_mix:.2f}[vocal_adj];[2:a]volume={instrumental_mix:.2f}[inst_adj];[vocal_adj][inst_adj]amix=inputs=2:duration=first:normalize=0[mix]"
            audio_maps = [
                "-map", "0:v",
                "-map", "[mix]",                 # 音軌 1: 導唱 (人聲+伴奏)
                "-map", "2:a",                   # 音軌 2: 純伴奏
                "-metadata:s:a:0", f"title=导唱 (人声{vocal_pct}% + 伴奏{inst_pct}%)",
                "-metadata:s:a:1", "title=伴唱 (纯伴奏)",
            ]

        if force_1080p:
            scale_filter = self._get_1080p_scale_pad_filter()
            if audio_filter is not None:
                full_filter = f"[0:v]{scale_filter}[vout];{audio_filter}"
            else:
                full_filter = f"[0:v]{scale_filter}[vout]"
            video_map = "[vout]"
        else:
            full_filter = audio_filter  # 可能為 None（LR 模式）
            video_map = "0:v"

        audio_maps = [video_map if x == "0:v" else x for x in audio_maps]

        if full_filter is not None:
            cmd += ["-filter_complex", full_filter]
        cmd += [*audio_maps, "-c:a", "aac", "-b:a", "320k"]

        if force_1080p:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "copy"]

        if vfmt == "MP4":
            cmd += ["-movflags", "+faststart"]

        cmd += [str(output_file)]

        try:
            subprocess.run( cmd, check=True, creationflags=self.subp_flags, capture_output=True, text=True, encoding="utf-8", errors="replace" )
            self.log(f"  ✅ {vfmt} 合成完成: {os.path.basename(output_file)}")
            return True
        except subprocess.CalledProcessError as e:
            self.log(f"  ❌ {vfmt} 合成出错 (代码: {e.returncode})")
            self._log_ffmpeg_stderr(e)
            return False
        except Exception as e:
            self.log(f"  ❌ {vfmt} 合成出错: {str(e)}")
            return False
        finally:
            if track_mode == "lr":
                with suppress(Exception): lr_tmp.unlink(missing_ok=True)

    def batch_process(self):
        start_time = time.time()
        total = len(self.file_list)
        output_dir = self.output_dir_var.get()
        enable_lyrics = self.enable_lyrics_recognition_var.get()

        for i, input_file in enumerate(self.file_list):
            if self.cancel_event.is_set():
                self.log("🛑 批次分离已中止。")
                break
            if not os.path.exists(input_file):
                self.log(f"⚠️ 找不到档案: {input_file}")
                continue

            self.log(f"--- 正在处理 ({i+1}/{total}): {os.path.basename(input_file)} ---")
            self.update_progress(int(i / total * 100), f"正在处理 {i+1}/{total}")

            success = self.run_audio_separator(input_file, output_dir)

            if success:
                self.log(f"✅ 档案处理完成: {os.path.basename(input_file)}")

                if enable_lyrics:
                    self.recognize_lyrics_and_generate_srt(input_file, output_dir)
            else:
                self.log(f"❌ 档案处理失败: {os.path.basename(input_file)}，请检查上方日志。")

        self.update_progress(100, "全部完成")
        elapsed_time = time.time() - start_time
        self.log(f"⏱️ 批次处理完成，总花费时间: {elapsed_time:.2f} 秒 (已处理 {total} 个档案)")
        self.update_status("批次处理完成！", "green")
        messagebox.showinfo("成功", f"批次处理完成！\n已处理 {total} 个档案。\n总花费时间: {elapsed_time:.2f} 秒")
        if os.name == 'nt' and os.path.exists(output_dir):
            self._open_folder_no_dup(str(output_dir))
        self.finish_processing()

    def run_audio_separator(self, input_file, output_dir):
        self.fix_python_pth()

        fmt = self.output_format_var.get()
        device = self._resolve_device()
        runtime_ready, device, runtime_lib_dir = self._ensure_runtime_stack_ready(device)
        if not runtime_ready:
            return False

        env = self._build_python_env(runtime_lib_dir, include_gpu_runtime=(device == "cuda"))
        start_time = time.time()
        runtime_lib_dir_posix = self._p(runtime_lib_dir)
        common_lib_dir_posix  = self._p(self.common_lib_dir)
        app_bin_dir_posix     = self._p(self.bin_dir)
        app_py_dir_posix      = self._p(self.py_dir)
        script = f"""
import sys, os
import argparse
import logging
import json
from importlib import metadata

target_lib = r'{runtime_lib_dir_posix}'
common_lib = r'{common_lib_dir_posix}'
app_bin_dir = r'{app_bin_dir_posix}'
app_py_dir = r'{app_py_dir_posix}'
is_directml = {str(device == "directml")}

if common_lib not in sys.path:
    sys.path.insert(0, common_lib)
if target_lib not in sys.path:
    sys.path.insert(0, target_lib)

numpy_libs = os.path.join(common_lib, 'numpy.libs')
numpy_core = os.path.join(common_lib, 'numpy', 'core')
os.environ["PATH"] = numpy_libs + os.pathsep + numpy_core + os.pathsep + app_bin_dir + os.pathsep + app_py_dir + os.pathsep + os.environ['PATH']

if hasattr(os, 'add_dll_directory'):
    dll_dirs = [app_bin_dir, app_py_dir, common_lib, target_lib]
    ort_pkg = os.path.join(target_lib, 'onnxruntime')
    ort_capi = os.path.join(ort_pkg, 'capi')
    for p in [ort_pkg, ort_capi, numpy_libs, numpy_core]:
        if os.path.isdir(p):
            dll_dirs.append(p)
    if {str(device == "cuda")}:
        for root, dirs, files in os.walk(target_lib):
            for sub in ['bin', 'lib']:
                p = os.path.join(root, sub)
                if os.path.isdir(p):
                    dll_dirs.append(p)
    seen = set()
    for p in dll_dirs:
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            os.add_dll_directory(p)
        except Exception:
            pass

from audio_separator.separator import Separator

# 建立 logger（和 cli.py 一样）
logger = logging.getLogger(__name__)
log_handler = logging.StreamHandler()
log_formatter = logging.Formatter(fmt="%(asctime)s.%(msecs)03d - %(levelname)s - %(module)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

# 解析参数（和 cli.py 一样）
parser = argparse.ArgumentParser(description="Separate audio file into different stems.")
parser.add_argument("audio_files", nargs="*", help="The audio file paths or directory to separate.")
parser.add_argument("-m", "--model_filename", default="model_bs_roformer_ep_317_sdr_12.9755.ckpt")
parser.add_argument("--extra_models", nargs="+", default=None)
parser.add_argument("--output_format", default="FLAC")
parser.add_argument("--output_bitrate", default=None)
parser.add_argument("--output_dir", default=None)
parser.add_argument("--model_file_dir", default="/tmp/audio-separator-models/")
parser.add_argument("--download_model_only", action="store_true")
parser.add_argument("--invert_spect", action="store_true")
parser.add_argument("--normalization", type=float, default=0.9)
parser.add_argument("--amplification", type=float, default=0.0)
parser.add_argument("--single_stem", default=None)
parser.add_argument("--sample_rate", type=int, default=44100)
parser.add_argument("--use_soundfile", action="store_true")
parser.add_argument("--use_autocast", action="store_true")
parser.add_argument("--chunk_duration", type=float, default=None)
parser.add_argument("--ensemble_algorithm", default=None)
parser.add_argument("--ensemble_weights", nargs="+", type=float, default=None)
parser.add_argument("--ensemble_preset", default=None)
parser.add_argument("--list_presets", action="store_true")
parser.add_argument("--custom_output_names", type=json.loads, default=None)
parser.add_argument("--mdx_segment_size", type=int, default=256)
parser.add_argument("--mdx_overlap", type=float, default=0.25)
parser.add_argument("--mdx_batch_size", type=int, default=1)
parser.add_argument("--mdx_hop_length", type=int, default=1024)
parser.add_argument("--mdx_enable_denoise", action="store_true")
parser.add_argument("--vr_batch_size", type=int, default=1)
parser.add_argument("--vr_window_size", type=int, default=512)
parser.add_argument("--vr_aggression", type=int, default=5)
parser.add_argument("--vr_enable_tta", action="store_true")
parser.add_argument("--vr_enable_post_process", action="store_true")
parser.add_argument("--vr_post_process_threshold", type=float, default=0.2)
parser.add_argument("--vr_high_end_process", action="store_true")
parser.add_argument("--demucs_segment_size", type=str, default="Default")
parser.add_argument("--demucs_shifts", type=int, default=2)
parser.add_argument("--demucs_overlap", type=float, default=0.25)
parser.add_argument("--demucs_segments_enabled", type=bool, default=True)
parser.add_argument("--mdxc_segment_size", type=int, default=256)
parser.add_argument("--mdxc_override_model_segment_size", action="store_true")
parser.add_argument("--mdxc_overlap", type=int, default=8)
parser.add_argument("--mdxc_batch_size", type=int, default=1)
parser.add_argument("--mdxc_pitch_shift", type=int, default=0)

args = parser.parse_args()
logger.setLevel(logging.INFO)

# 建立 Separator，加入 use_directml 参数！
separator = Separator(
    log_formatter=log_formatter,
    log_level=logging.INFO,
    model_file_dir=args.model_file_dir,
    output_dir=args.output_dir,
    output_format=args.output_format,
    output_bitrate=args.output_bitrate,
    normalization_threshold=args.normalization,
    amplification_threshold=args.amplification,
    output_single_stem=args.single_stem,
    invert_using_spec=args.invert_spect,
    sample_rate=args.sample_rate,
    use_soundfile=args.use_soundfile,
    use_autocast=args.use_autocast,
    use_directml=is_directml,
    chunk_duration=args.chunk_duration,
    ensemble_algorithm=args.ensemble_algorithm,
    ensemble_weights=args.ensemble_weights,
    ensemble_preset=args.ensemble_preset,
    mdx_params={{
        "hop_length": args.mdx_hop_length,
        "segment_size": args.mdx_segment_size,
        "overlap": args.mdx_overlap,
        "batch_size": args.mdx_batch_size,
        "enable_denoise": args.mdx_enable_denoise,
    }},
    vr_params={{
        "batch_size": args.vr_batch_size,
        "window_size": args.vr_window_size,
        "aggression": args.vr_aggression,
        "enable_tta": args.vr_enable_tta,
        "enable_post_process": args.vr_enable_post_process,
        "post_process_threshold": args.vr_post_process_threshold,
        "high_end_process": args.vr_high_end_process,
    }},
    demucs_params={{
        "segment_size": args.demucs_segment_size,
        "shifts": args.demucs_shifts,
        "overlap": args.demucs_overlap,
        "segments_enabled": args.demucs_segments_enabled,
    }},
    mdxc_params={{
        "segment_size": args.mdxc_segment_size,
        "batch_size": args.mdxc_batch_size,
        "overlap": args.mdxc_overlap,
        "override_model_segment_size": args.mdxc_override_model_segment_size,
        "pitch_shift": args.mdxc_pitch_shift,
    }},
)

# 处理模型和分离（和 cli.py 一样）
audio_files = args.audio_files
if not audio_files:
    parser.print_help()
    sys.exit(1)

if args.ensemble_preset and args.model_filename == "model_bs_roformer_ep_317_sdr_12.9755.ckpt" and not args.extra_models:
    separator.load_model()
else:
    model_filenames = [args.model_filename] + (args.extra_models or [])
    if len(model_filenames) == 1:
        model_filenames = model_filenames[0]
    separator.load_model(model_filename=model_filenames)

output_files = separator.separate(audio_files, custom_output_names=args.custom_output_names)
logger.info(f"Separation complete! Output file(s): {{' '.join(output_files)}}")
"""

        selected_model = self.model_var.get().split(" ")[0]
        fallback_model = "htdemucs.yaml"

        def build_command(model_name):
            is_demucs = model_name.endswith(".yaml")
            command = [
                str(self.local_python), "-c", script,
                input_file,
                "-m", model_name,
                "--model_file_dir", str(self.models_dir),
                "--output_dir", output_dir,
                "--output_format", fmt,
                "--output_bitrate", "320k",
                "--normalization", "0.9"
            ]

            if is_demucs:
                command.extend([ "--demucs_segment_size", "None", "--demucs_shifts", "2", "--demucs_overlap", "0.25", ])
            else:
                command.extend([ "--mdx_overlap", str(self.overlap_var.get()), "--mdx_segment_size", "256", "--mdx_hop_length", "1024" ])
                if self.denoise_var.get():
                    command.append("--mdx_enable_denoise")

            if device == "cuda":
                command.append("--use_autocast")
                if not is_demucs:
                    command.extend(["--mdx_batch_size", "4"])
            elif device == "directml":
                if not is_demucs:
                    command.extend(["--mdx_batch_size", "4"])
            else:
                if not is_demucs:
                    command.extend(["--mdx_batch_size", "1"])
            return command, is_demucs

        def has_output_files():
            input_stem = Path(input_file).stem
            out_path = Path(output_dir)

            keywords = ["_(Vocals)", "_(Instrumental)", "_(No Vocals)", "_(Bass)", "_(Drums)", "_(Other)"]

            found_files = []
            try:
                self.log(f"  🔍 正在检查输出目录: {out_path}")
                self.log(f"  🔍 预期前缀: {input_stem}")

                for fname in os.listdir(str(out_path)):
                    if fname.endswith(f".{fmt}"):
                        found_files.append(fname)
                        if any(kw in fname for kw in keywords):
                            self.log(f"  ✅ 找到输出档案: {fname}")
                            return True

                self.log(f"  ⚠️ 找到的档案数量: {len(found_files)}")
                for f in found_files[:5]:  # 只顯示前5個
                    self.log(f"    - {f}")
            except Exception as e:
                self._log_exception("  ❌ 检查输出档案时出错: ", e)
            return False

        def run_model(model_name, is_retry=False):
            command, is_demucs = build_command(model_name)

            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    encoding='utf-8', errors='replace', creationflags=self.subp_flags, env=env)
                self._current_process = process

                gpu_kernel_error = False
                unsupported_model_error = False
                unsupported_model_hash = None

                for line in process.stdout:
                    if self.cancel_event.is_set():
                        process.terminate()
                        self.log("🛑 AI 分离任务已取消。")
                        return False, "cancelled"

                    line = line.strip()
                    if line:
                        self.log(line)

                        if "no kernel image is available" in line:
                            gpu_kernel_error = True

                        if "Unsupported Model File: parameters for MD5 hash" in line:
                            unsupported_model_error = True
                            hash_match = re.search(r"MD5 hash ([0-9a-fA-F]{32})", line)
                            if hash_match:
                                unsupported_model_hash = hash_match.group(1)

                        if "%" in line:
                            try:
                                match = re.search(r"(\d+)%", line)
                                if match:
                                    pct = int(match.group(1))
                                    mapped = 40 + int(pct * 0.28)
                                    self.update_progress(mapped, "正在分离人声与伴奏", step_text=f"步骤 3/5：AI 人声分离中 {pct}%")
                            except Exception:
                                pass

                process.wait()
                self._current_process = None

                if process.returncode == 0 and not gpu_kernel_error and not unsupported_model_error:
                    if has_output_files():
                        return True, "success"
                    self.log("❌ 虽然程式回报成功，但未能在输出目录找到产出的音档。")
                    return False, "missing_output"

                if gpu_kernel_error:
                    self.log("\n❌ 侦测到 GPU 核心错误 (no kernel image)。")
                    self.log("💡 这通常是因为您的 GPU 太新 (RTX 50 系列)，目前的稳定版组件尚未完全支援。")
                    self.log("💡 建议：请在主介面将「运算装置」切换为 CPU 模式运行，或尝试执行「一键修复」升级至最新实验性核心。")
                    return False, "gpu_kernel"

                if unsupported_model_error:
                    model_path = self.models_dir / model_name
                    self.log("\n❌ 侦测到 UVR 模型参数不相容。")
                    if unsupported_model_hash:
                        self.log(f"💡 此模型的 MD5 杂凑值为: {unsupported_model_hash}")
                    self.log(f"💡 目前模型 `{model_name}` 的内容不在 audio-separator 内建支援表中。")
                    if model_path.exists():
                        self.log(f"💡 建议删除后重新下载模型档: {model_path}")
                    else:
                        self.log("💡 这通常代表模型档下载不完整、版本不相容，或内容已被替换。")

                    if not is_demucs and model_name != fallback_model:
                        if not is_retry:
                            self.log(f"🔁 将自动改用 `{fallback_model}` 再重试一次...")
                            return False, "retry_with_demucs"
                    else:
                        self.log("💡 可改用 `htdemucs.yaml` 或 `htdemucs_ft.yaml` 进行分离。")
                    return False, "unsupported_model"

                return False, "process_failed"
            except Exception as e:
                self.log(f"执行错误: {str(e)}")
                self._current_process = None
                return False, "exception"

        device_display = "NVIDIA GPU" if device == "cuda" else ("DirectML" if device == "directml" else "CPU")
        success, reason = run_model(selected_model, is_retry=False)
        if success:
            elapsed_time = time.time() - start_time
            self.log(f"⏱️ 音讯分离完成，总花费时间: {elapsed_time:.2f} 秒 (装置: {device_display})")
            return True

        if reason == "retry_with_demucs":
            self.log(f"🎯 回退模型: {selected_model} → {fallback_model}")
            retry_success, retry_reason = run_model(fallback_model, is_retry=True)
            if retry_success:
                elapsed_time = time.time() - start_time
                self.log(f"⏱️ 音讯分离完成，总花费时间: {elapsed_time:.2f} 秒 (装置: {device_display})")
                self.log(f"✅ 已改用 `{fallback_model}` 完成音讯分离。")
                self.log("💡 若想恢复使用原本的 MDX 模型，请删除旧的 .onnx 后重新下载。")
                return True

            if retry_reason == "unsupported_model":
                self.log("❌ 备援模型也无法载入，请执行「一键修复/初始化环境」，或手动清理模型目录后再试。")
            else:
                self.log("❌ 已尝试自动切换备援模型，但仍未成功完成分离。")

        return False

    def _browse_media_file(self, title="选择档案", filetypes=None):
        """通用：弹出档案选择对话框，回传路径字串或 None"""
        if filetypes is None:
            filetypes = [("所有档案", "*.*")]
        return filedialog.askopenfilename(title=title, filetypes=filetypes)

    def _browse_video_file(self, title="选择影片档案"):
        return self._browse_media_file(title, [("影片档案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有档案", "*.*")])

    def _browse_audio_file(self, title="选择声音档案"):
        return self._browse_media_file(title, [("声音档案", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus"), ("所有档案", "*.*")])

    @staticmethod
    def _is_image_file(path: str) -> bool:
        try:
            ext = os.path.splitext(path or "")[1].lower()
            return ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        except Exception:
            return False

    def _refresh_merge_audio_ui(self):
        """
        合并字幕与影片页面：
        - 选到「图片」：强制显示声音档案浏览（必选）
        - 选到「影片」：显示「声音档案另外」勾选；勾选后才显示声音档案浏览
        """
        try:
            media_path = (self.merge_video_path_var.get() or "").strip()
            is_img = self._is_image_file(media_path)

            if not media_path:
                self.merge_audio_option_row.pack_forget()
                self.merge_audio_file_row.pack_forget()
                return

            if is_img:
                # 圖片：不顯示勾選，直接顯示音訊選擇
                self.merge_audio_option_row.pack_forget()
                self.merge_audio_file_row.pack(fill=tk.X, pady=5)
            else:
                # 影片：顯示勾選，勾選才顯示音訊選擇
                self.merge_audio_option_row.pack(fill=tk.X, pady=(0, 2))
                if self.merge_use_external_audio_var.get():
                    self.merge_audio_file_row.pack(fill=tk.X, pady=5)
                else:
                    self.merge_audio_file_row.pack_forget()
        except Exception:
            pass

    def browse_merge_video(self):
        file_path = filedialog.askopenfilename(
            title="选择影片或图片",
            filetypes=[
                ("影片或图片", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm *.png *.jpg *.jpeg *.webp *.bmp"),
                ("影片档案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"),
                ("图片档案", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("所有档案", "*.*"),
            ],
        )
        if file_path:
            self.merge_video_path_var.set(file_path)
            self.player_video_path = file_path
            # 圖片模式時：強制外掛音訊（不使用勾選）
            self.merge_use_external_audio_var.set(self._is_image_file(file_path))
            self._refresh_merge_audio_ui()
            self.reload_player()

    def browse_merge_subtitle(self):
        file_path = filedialog.askopenfilename( title="选择字幕档案", filetypes=[("字幕档案", "*.srt *.ass *.ssa *.json"), ("所有档案", "*.*")] )
        if file_path:
            self.merge_subtitle_path_var.set(file_path)
            self.player_subtitle_path = file_path
            is_json = file_path.lower().endswith('.json')
            is_srt  = file_path.lower().endswith('.srt')
            if is_json or is_srt:
                self.ktv_color_frame.pack(fill=tk.X, pady=5)
                _toggle_cmd = self._toggle_srt_border if hasattr(self, '_toggle_srt_border') else None
                if is_json:
                    self.ktv_color_frame.config(text="KTV 逐字渐变颜色设定")
                    self.unplayed_label.config(text="未唱颜色:")
                    if hasattr(self, "border_label"):
                        self.border_label.config(text="未唱边框:", state=tk.NORMAL, command=_toggle_cmd)
                        with suppress(Exception):
                            self._border_entry.config(state=tk.NORMAL)
                            self._border_btn.config(state=tk.NORMAL)
                            self.border_color_preview.config(bg=self.ktv_border_color_var.get())
                    self.played_row.pack(fill=tk.X, pady=5)
                    if hasattr(self, "played_border_row"): self.played_border_row.pack(side=tk.LEFT, padx=(0, 0))
                else:
                    self.ktv_color_frame.config(text="字幕样式设定")
                    self.unplayed_label.config(text="字幕颜色:")
                    if hasattr(self, "border_label"):
                        self.border_label.config(text="边框颜色:", state=tk.NORMAL, command=_toggle_cmd)
                    self.played_row.pack_forget()
                    if hasattr(self, "played_border_row"): self.played_border_row.pack_forget()
                    with suppress(Exception): _toggle_cmd and _toggle_cmd()
                # 雙行字幕在 JSON / SRT 均支援
                for attr in ('two_line_row', 'two_line_x_row'):
                    if hasattr(self, attr): getattr(self, attr).pack(fill=tk.X, pady=5)
            else:
                self.ktv_color_frame.pack_forget()
            # 動態顯示「將 JSON/SRT 轉 ASS 字幕」按鈕
            if hasattr(self, 'json_to_ass_btn') and hasattr(self, 'merge_start_btn'):
                if is_json or is_srt:
                    self.json_to_ass_btn.config(
                        text="📝 将 JSON 转 ASS 字幕" if is_json else "📝 将 SRT 转 ASS 字幕",
                        command=self.start_json_to_ass_only if is_json else self.start_srt_to_ass_only
                    )
                    self.json_to_ass_btn.grid(row=0, column=1, sticky="ew", padx=4)
                else:
                    self.json_to_ass_btn.grid_remove()
            self.reload_player()

    def browse_merge_audio(self):
        if p := self._browse_audio_file(): self.merge_audio_path_var.set(p)

    def browse_rec_video(self):
        if p := self._browse_video_file(): self.rec_video_path_var.set(p)

    def start_recognize_lyrics(self):
        video_path = self.rec_video_path_var.get().strip()
        if not self._require_file(video_path, "请选择影片档案！", "找不到影片档案！"): return
        if self.is_processing: return
        self._begin_processing("正在辨识歌词...", self.recognize_lyrics_process, video_path)

    def recognize_lyrics_process(self, video_path):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)

            temp_dir = tempfile.gettempdir()
            video_stem = Path(video_path).stem
            temp_audio = os.path.join(temp_dir, f"{video_stem}_temp_audio.mp3")

            self.update_status("正在提取音讯...", "orange")
            self.log("\n🎤 开始从影片提取音讯...")

            ffmpeg_cmd = [ str(self.ffmpeg_exe), "-i", video_path, "-vn", "-acodec", "libmp3lame", "-ab", "192k", "-ar", "44100", "-y", temp_audio ]

            self.log(f"  > 执行: {' '.join(ffmpeg_cmd)}")

            process = subprocess.Popen( ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', creationflags=self.subp_flags )
            self._current_process = process

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    self.log("🛑 已取消操作")
                    return
                line = line.strip()
                if line:
                    self.log(f"    {line}")

            process.wait()
            self._current_process = None

            if process.returncode != 0:
                self.log("❌ 提取音讯失败")
                return

            self.log("✅ 音讯提取完成")

            separate_first = self.rec_separate_first_var.get()
            audio_for_recognition = temp_audio  # 預設直接用全音軌

            if separate_first:
                self.update_status("正在分离人声...", "orange")
                self.log("\n🎵 先进行人声分离以提升辨识准确度...")

                sep_success = self.run_audio_separator(temp_audio, output_dir)

                if sep_success:
                    audio_stem = Path(temp_audio).stem
                    found_vocal = None
                    found_inst = None
                    for f in Path(output_dir).iterdir():
                        if not f.name.startswith(audio_stem):
                            continue
                        if "(Vocals)" in f.name and f.suffix in ('.mp3', '.wav', '.flac'):
                            found_vocal = f
                        elif any(x in f.name for x in ["(Instrumental)", "(No Vocals)"]) and f.suffix in ('.mp3', '.wav', '.flac'):
                            found_inst = f

                    safe_stem = MyKTVApp.sanitize_filename(video_stem, max_len=60)

                    def _move_stem(src, label, suffix):
                        dst = Path(output_dir) / f"{safe_stem}_{label}{suffix}"
                        try:
                            if dst.exists(): dst.unlink()
                            shutil.move(str(src), str(dst))
                        except Exception as e:
                            self.log(f"  ⚠️ {label}档重新命名失败: {e}")
                            dst = src
                        self.log(f"  ✅ {label}档案已储存: {dst.name}")
                        return dst

                    if found_vocal:
                        found_vocal = _move_stem(found_vocal, "人声", found_vocal.suffix)
                        audio_for_recognition = str(found_vocal)
                    else:
                        self.log("  ⚠️ 找不到分离后的人声档，改用原始音讯辨识")

                    if found_inst:
                        _move_stem(found_inst, "伴奏", found_inst.suffix)
                else:
                    self.log("  ⚠️ 人声分离失败，改用原始音讯辨识")

            self.update_status("正在进行 AI 歌词识别...", "orange")
            self.update_progress(55, "正在识别歌词", step_text="步骤 3/5：准备载入 Whisper 模型...")
            rec_lang = self.rec_lyrics_language_var.get() if hasattr(self, 'rec_lyrics_language_var') else "traditional"
            result = self.recognize_lyrics_and_generate_srt(audio_for_recognition, output_dir, video_stem, override_language=rec_lang)

            if temp_audio and os.path.exists(temp_audio):
                with suppress(Exception): os.remove(temp_audio)

            if result:
                srt_file, json_file, _ = result
                self.update_progress(100, "完成")
                self.log("\n✅ 歌词辨识完成！")
                self.log(f"   SRT 字幕: {Path(srt_file).name}")
                self.log(f"   JSON 歌词: {Path(json_file).name}")
                self._show_done_and_open("完成", f"歌词辨识完成！\n\nSRT 字幕: {Path(srt_file).name}\nJSON 歌词: {Path(json_file).name}\n\n档案已储存至: {output_dir}", output_dir, select_file=str(srt_file))
            else:
                self.log("❌ 歌词辨识失败")
                messagebox.showerror("错误", "歌词辨识失败，请查看日志！")

        except Exception as e:
            self._log_exception("❌ 发生错误", e)
            messagebox.showerror("错误", f"发生错误: {str(e)}")
        finally:
            # 一律走共同收尾：解鎖 UI、刷新主按鈕文字/狀態
            self.finish_processing()

    def _validate_json_inputs(self):
        """验证 JSON 按钮共用的路径，回传 (video_path, subtitle_path) 或 (None, None)"""
        subtitle_path = self.merge_subtitle_path_var.get().strip()
        if not self._require_file(subtitle_path, "请选择字幕档案！", "找不到字幕档案！"): return None, None
        if not subtitle_path.lower().endswith('.json'):
            messagebox.showwarning("警告", "这三个按钮仅适用于 JSON 字幕档案！"); return None, None
        if self.is_processing: return None, None
        return self.merge_video_path_var.get().strip(), subtitle_path

    def _validate_json_video_inputs(self):
        """验证 JSON + 影片两者路径，回传 (video_path, subtitle_path) 或 (None, None)"""
        video_path, subtitle_path = self._validate_json_inputs()
        if subtitle_path is None: return None, None
        if not self._require_file(video_path, "请选择影片档案！", "找不到影片档案！"): return None, None
        return video_path, subtitle_path

    def _read_two_line_settings(self):
        """读取双行字幕 GUI 设定，回传 (enabled, advance_sec, top_x, bottom_x, line_gap_px)"""
        def _gf(attr, default):
            v = getattr(self, attr, None)
            try: return type(default)(v.get()) if v else default
            except Exception: return default
        two_line_enabled = bool(_gf('ktv_two_line_var', False))
        advance_sec = max(0.0, _gf('ktv_two_line_advance_var', 1.5))
        top_x_offset = _gf('ktv_two_line_top_x_var', 0.0)
        bottom_x_offset = _gf('ktv_two_line_bottom_x_var', 0.0)
        raw = getattr(self, 'ktv_two_line_gap_var', None)
        try: line_gap_px = int(float(raw.get().strip())) if raw and raw.get().strip() else None
        except Exception: line_gap_px = None
        return two_line_enabled, advance_sec, top_x_offset, bottom_x_offset, line_gap_px

    def _collect_json_to_ass_kwargs(self, video_path=None):
        """收集呼叫 _json_to_ass 所需的所有 GUI 设定，回传 kwargs dict"""
        two_line, advance_sec, top_x, bottom_x, line_gap_px = self._read_two_line_settings()
        def _gv(attr, default): v = getattr(self, attr, None); return v.get() if v else default
        use_border = bool(_gv('srt_use_border_var', True))
        outline_size = (int(_gv('ktv_border_map_expand_factor_var', 4)) if use_border else 0)
        use_played_border = hasattr(self, 'ktv_use_played_border_var') and self.ktv_use_played_border_var.get()
        played_border_color = (_gv('ktv_played_border_color_var', None) if use_played_border else None)
        vw, vh = self._get_video_size(video_path) if video_path else (None, None)
        return dict(
            unplayed_color=_gv('ktv_unplayed_color_var', '#FFFFFF') or "#FFFFFF",
            played_color=_gv('ktv_played_color_var', '#0000FF') or "#0000FF",
            border_color=_gv('ktv_border_color_var', '#000000') or "#000000",
            font_name=_gv('ktv_font_var', "微软正黑体"),
            margin_v_offset=int(_gv('subtitle_margin_var', 0)),
            color_mode="slide",
            font_size=int(_gv('ktv_font_size_var', 60)),
            two_line=two_line, advance_sec=advance_sec,
            top_x_offset=top_x, bottom_x_offset=bottom_x, line_gap_px=line_gap_px,
            played_border_color=played_border_color, use_clip_mask=True,
            pre_show_sec=float(_gv('ktv_pre_show_var', 0.0)),
            hold_sec=float(_gv('ktv_hold_sec_var', 0.0)),
            speed_factor=float(_gv('ktv_speed_factor_var', 1.0)),
            singing_end_ratio=float(_gv('ktv_singing_end_ratio_var', 1.0)),
            video_width=vw, video_height=vh, outline_size=outline_size,
        )

    def _json_process_common_setup(self, video_path):
        """各 JSON process 方法共用的开头：取得 output_dir、vfmt、output_file"""
        output_dir = self.output_dir_var.get()
        os.makedirs(output_dir, exist_ok=True)
        vfmt = self.merge_video_format_var.get()
        output_file = str(Path(output_dir) / f"{Path(video_path).stem}_含字幕.{vfmt}")
        return output_dir, vfmt, output_file

    def start_json_burn(self):
        """JSON 转 ASS 后烧录进影片（重新编码）"""
        video_path, subtitle_path = self._validate_json_video_inputs()
        if subtitle_path is None:
            return
        self._begin_processing("JSON 转 ASS 烧录中...", self._json_burn_process, video_path, subtitle_path)

    def _run_ps1(self, script, args):
        """将 PowerShell 脚本写入暂存档执行，自动延迟清除"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ps1', delete=False, encoding='utf-8') as tf:
            tf.write(script); tmp_ps1 = tf.name
        subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', tmp_ps1] + args,
            creationflags=self.subp_flags)
        threading.Thread(target=self._delayed_remove, args=(tmp_ps1,), daemon=True).start()

    def _select_file_in_explorer(self, file_path):
        """在已开著的 Explorer 视窗中选取档案，若尚未开著才开新视窗。"""
        try:
            file_path   = os.path.normpath(str(file_path))
            folder_path = os.path.normpath(str(Path(file_path).parent))
            self._run_ps1(r"""
param([string]$FilePath, [string]$FolderPath)
Add-Type -AssemblyName Microsoft.VisualBasic
$shell = New-Object -ComObject Shell.Application
$found = $false
foreach ($win in $shell.Windows()) {
    try {
        $winPath = $win.Document.Folder.Self.Path
        if ([System.IO.Path]::GetFullPath($winPath).ToLower() -eq [System.IO.Path]::GetFullPath($FolderPath).ToLower()) {
            $hwnd = $win.HWND
            $sig = '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);'
            $t = Add-Type -MemberDefinition $sig -Name WinAPI -Namespace NativeMethods -PassThru -ErrorAction SilentlyContinue
            if ($t) { $t::ShowWindow([IntPtr]$hwnd, 9); $t::SetForegroundWindow([IntPtr]$hwnd) }
            foreach ($item in $win.Document.Folder.Items()) {
                if ([System.IO.Path]::GetFullPath($item.Path).ToLower() -eq [System.IO.Path]::GetFullPath($FilePath).ToLower()) {
                    $win.Document.SelectItem($item, 29); break
                }
            }
            $found = $true; break
        }
    } catch {}
}
if (-not $found) { Start-Process explorer.exe -ArgumentList "/select,`"$FilePath`"" }
""", ['-FilePath', file_path, '-FolderPath', folder_path])
        except Exception:
            with suppress(Exception):
                subprocess.Popen(['explorer', f'/select,{os.path.normpath(str(file_path))}'], creationflags=self.subp_flags)

    def _delayed_remove(self, path, delay=5):
        """延迟 delay 秒后删除暂存档（供 PowerShell 脚本清理使用）"""
        time.sleep(delay)
        with suppress(Exception): os.remove(path)

    def _open_folder_no_dup(self, folder_path):
        """开启资料夹，若已有 Explorer 视窗开著就不重复开。"""
        try:
            folder_path = os.path.normpath(str(folder_path))
            self._run_ps1(r"""
param([string]$FolderPath)
$shell = New-Object -ComObject Shell.Application
foreach ($win in $shell.Windows()) {
    try {
        $winPath = $win.Document.Folder.Self.Path
        if ([System.IO.Path]::GetFullPath($winPath).ToLower() -eq [System.IO.Path]::GetFullPath($FolderPath).ToLower()) {
            $hwnd = $win.HWND
            $sig = '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);'
            $t = Add-Type -MemberDefinition $sig -Name WinAPI2 -Namespace NativeMethods2 -PassThru -ErrorAction SilentlyContinue
            if ($t) { $t::ShowWindow([IntPtr]$hwnd, 9); $t::SetForegroundWindow([IntPtr]$hwnd) }
            exit 0
        }
    } catch {}
}
Start-Process explorer.exe -ArgumentList "`"$FolderPath`""
""", ['-FolderPath', folder_path])
        except Exception:
            if os.path.exists(str(folder_path)):
                os.startfile(str(folder_path))

    def _show_done_and_open(self, title, message, open_path=None, select_file=None):
        """通用：显示完成对话框，并在 Windows 上开启资料夹（若有 select_file 则聚焦该档案）
        若目标资料夹已开著，不重复开新视窗。"""
        messagebox.showinfo(title, message)
        if os.name != 'nt':
            return
        if select_file and os.path.isfile(select_file):
            self._select_file_in_explorer(select_file)
        elif open_path and os.path.exists(open_path):
            folder = str(open_path) if os.path.isdir(open_path) else str(Path(open_path).parent)
            self._open_folder_no_dup(folder)

    def _json_process_finish(self, success, action, output_dir, output_file):
        """_json_burn_process / _json_mux_process 共用的结尾：记录、弹窗、开资料夹"""
        if success:
            self.update_progress(100, f"{action}完成")
            self.log(f"✅ {action}完成: {Path(output_file).name}")
            self._show_done_and_open("完成", f"{action}完成！\n档案已储存至: {output_file}", output_dir, select_file=str(output_file))
        else:
            self.log(f"❌ {action}失败。")
            messagebox.showerror("错误", f"{action}失败，请查看日志！")

    def _json_burn_process(self, video_path, subtitle_path):
        try:
            output_dir, _, output_file = self._json_process_common_setup(video_path)
            self.log("--- [烧录] JSON 转 ASS 后烧录合并 ---")
            self.update_progress(10, "准备中...")
            success = self.process_json_subtitle_to_video(video_path, subtitle_path, output_file)
            self._json_process_finish(success, "烧录", output_dir, output_file)
        except Exception as e:
            self._log_exception("❌ 烧录过程出错", e)
        finally:
            self.finish_processing()

    def start_json_mux(self):
        """JSON 转 ASS 后以封装方式合并（不重新编码，快速）"""
        video_path, subtitle_path = self._validate_json_video_inputs()
        if subtitle_path is None:
            return
        self._begin_processing("JSON 转 ASS 封装中...", self._json_mux_process, video_path, subtitle_path)

    def _load_and_convert_json_to_ass(self, subtitle_path, ass_file, video_path=None):
        """读取 JSON 字幕并转成 ASS 档案，回传是否成功"""
        with open(subtitle_path, 'r', encoding='utf-8') as f:
            lyrics_data = json.load(f)
        kw = self._collect_json_to_ass_kwargs(video_path)
        self._json_to_ass(lyrics_data, ass_file,
                          kw.pop('unplayed_color'), kw.pop('played_color'), kw.pop('border_color'),
                          kw.pop('font_name'), kw.pop('margin_v_offset'), kw.pop('color_mode'), kw.pop('font_size'),
                          **kw)

    def _json_mux_process(self, video_path, subtitle_path):
        try:
            output_dir, vfmt, output_file = self._json_process_common_setup(video_path)
            self.log("--- [封装] JSON 转 ASS 后封装合并 ---")
            self.update_progress(10, "正在转换 JSON → ASS...")

            ass_file = str(Path(output_dir) / f"{Path(subtitle_path).stem}.ass")
            self._load_and_convert_json_to_ass(subtitle_path, ass_file, video_path)
            self.update_progress(50, "正在封装合并...")
            self.log("  🎬 正在使用 FFmpeg 封装（不重新编码）...")
            success = self.merge_subtitle_with_ffmpeg(video_path, ass_file, output_file, vfmt)
            self._json_process_finish(success, "封装", output_dir, output_file)
        except Exception as e:
            self._log_exception("❌ 封装过程出错", e)
        finally:
            self.finish_processing()

    def start_json_to_ass_only(self):
        """只做 JSON → ASS 转换，不合并影片"""
        _, subtitle_path = self._validate_json_inputs()
        if subtitle_path is None:
            return
        self._begin_processing("JSON 转 ASS 中...", self._json_to_ass_only_process, subtitle_path)

    def _json_to_ass_only_process(self, subtitle_path):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            ass_file = str(Path(output_dir) / f"{Path(subtitle_path).stem}.ass")
            self.log("--- [只转换] JSON 转 ASS 字幕 ---")
            self.update_progress(10, "正在转换...")
            self._load_and_convert_json_to_ass(subtitle_path, ass_file)
            self.update_progress(100, "转换完成")
            self.log(f"✅ ASS 字幕已储存: {Path(ass_file).name}")
            self._show_done_and_open("完成", f"转换完成！\nASS 字幕已储存至: {ass_file}", output_dir, select_file=str(ass_file))
        except Exception as e:
            self._log_exception("❌ 转换过程出错", e)
        finally:
            self.finish_processing()

    def start_srt_to_ass_only(self):
        """只做 SRT → ASS 转换，不合并影片"""
        subtitle_path = self.merge_subtitle_path_var.get().strip()
        if not self._require_file(subtitle_path, "请选择字幕档案！", "找不到字幕档案！"): return
        if not subtitle_path.lower().endswith('.srt'):
            messagebox.showwarning("警告", "此按钮仅适用于 SRT 字幕档案！"); return
        if self.is_processing: return
        video_path = (self.merge_video_path_var.get() or "").strip()
        self._begin_processing("SRT 转 ASS 中...", self._srt_to_ass_only_process, subtitle_path, video_path)

    def _get_srt_style_params(self):
        """读取 SRT/ASS 样式设定，回传 (text_color, border_color, font, font_size, margin_v, outline_size)"""
        def _gv(attr, default): v = getattr(self, attr, None); return v.get() if v else default
        text_color   = _gv('ktv_unplayed_color_var', '#FFFFFF') or "#FFFFFF"
        border_color = _gv('ktv_border_color_var', '#000000') or "#000000"
        selected_font = _gv('ktv_font_var', "微软正黑体")
        try: font_size = int(_gv('ktv_font_size_var', 60)); font_size = font_size if 10 <= font_size <= 200 else 60
        except Exception: font_size = 60
        try: margin_v_offset = int(_gv('subtitle_margin_var', 0))
        except Exception: margin_v_offset = 0
        try: outline_size = int(_gv('ktv_border_map_expand_factor_var', 4))
        except Exception: outline_size = 4
        if not (self.srt_use_border_var.get() if hasattr(self, 'srt_use_border_var') and self.srt_use_border_var else True): outline_size = 0
        return text_color, border_color, selected_font, font_size, margin_v_offset, outline_size

    def _srt_to_ass_only_process(self, subtitle_path, video_path=""):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            text_color, border_color, selected_font, font_size, margin_v_offset, outline_size = self._get_srt_style_params()
            two_line, advance_sec, top_x, bottom_x, line_gap_px = self._read_two_line_settings()
            vw, vh = (self._get_video_size(video_path) if video_path and os.path.exists(video_path) else (None, None))

            ass_file = str(Path(output_dir) / f"{Path(subtitle_path).stem}.ass")
            self.log("--- [只转换] SRT 转 ASS 字幕 ---")
            self.update_progress(10, "正在转换...")
            self._srt_to_ass(
                subtitle_path, ass_file,
                text_color, border_color,
                selected_font, font_size,
                margin_v_offset, outline_size,
                two_line=two_line,
                advance_sec=advance_sec,
                top_x_offset=top_x,
                bottom_x_offset=bottom_x,
                line_gap_px=line_gap_px,
                video_width=vw, video_height=vh
            )
            self.update_progress(100, "转换完成")
            self.log(f"✅ ASS 字幕已储存: {Path(ass_file).name}")
            self._show_done_and_open("完成", f"转换完成！\nASS 字幕已储存至: {ass_file}", output_dir, select_file=str(ass_file))
        except Exception as e:
            self._log_exception("❌ 转换过程出错", e)
        finally:
            self.finish_processing()

    def start_merge_subtitle_video(self):
        video_path = self.merge_video_path_var.get().strip()
        subtitle_path = self.merge_subtitle_path_var.get().strip()
        audio_path = self.merge_audio_path_var.get().strip() if hasattr(self, "merge_audio_path_var") else ""

        if not self._require_file(video_path, "请选择影片或图片！", "找不到影片或图片档案！"): return
        if not self._require_file(subtitle_path, "请选择字幕档案！", "找不到字幕档案！"): return
        is_img = self._is_image_file(video_path)
        if is_img:
            if not self._require_file(audio_path, "图片模式必须选择「声音档案」！", "找不到声音档案！"): return
        else:
            use_ext_audio = bool(getattr(self, "merge_use_external_audio_var", tk.BooleanVar(value=False)).get())
            if use_ext_audio:
                if not self._require_file(audio_path, "已勾选「声音档案另外」，请选择声音档案！", "找不到声音档案！"): return
        if self.is_processing:
            return

        # audio_path 可能為空（一般影片、未勾選外掛音訊）
        self._begin_processing("正在合并字幕与影片...", self.merge_subtitle_video_process, video_path, subtitle_path, audio_path)
        self.start_btn.config(text="处理中...")

    def merge_subtitle_video_process(self, video_path, subtitle_path, audio_path=""):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)

            video_stem = Path(video_path).stem
            vfmt = self.merge_video_format_var.get()
            output_file = Path(output_dir) / f"{video_stem}_含字幕.{vfmt}"

            is_img = self._is_image_file(video_path)
            use_ext_audio = bool(getattr(self, "merge_use_external_audio_var", tk.BooleanVar(value=False)).get())
            # 圖片模式強制外掛音訊；影片模式只在勾選時使用外掛音訊
            ext_audio = (audio_path if (is_img or use_ext_audio) else "")

            if ext_audio:
                self.log(f"--- 正在合并: {os.path.basename(video_path)} + {os.path.basename(ext_audio)} + {os.path.basename(subtitle_path)} ---")
            else:
                self.log(f"--- 正在合并: {os.path.basename(video_path)} + {os.path.basename(subtitle_path)} ---")
            self.update_progress(10, "准备合并...")

            is_json_subtitle = subtitle_path.lower().endswith('.json')
            is_srt_subtitle  = subtitle_path.lower().endswith('.srt')
            is_ass_subtitle  = subtitle_path.lower().endswith('.ass') or subtitle_path.lower().endswith('.ssa')

            # ── 先準備工作影片：圖片→影片、或影片替換音軌 ──
            working_video = video_path
            tmp_video_to_cleanup = None
            if ext_audio:
                if not self.ffmpeg_exe.exists():
                    self.log("❌ 找不到 ffmpeg.exe，无法处理图片/外挂音讯。")
                    return False

                # 產生暫存影片：放在 output_dir（方便使用者查看；完成後會嘗試自動清除）
                tmp_suffix = str(vfmt).lower().strip() or "mp4"
                tmp_name = f"{video_stem}__tmp_media_{int(time.time())}.{tmp_suffix}"
                tmp_video = str(Path(output_dir) / tmp_name)
                tmp_video_to_cleanup = tmp_video

                if is_img:
                    # 圖片 + 音訊 → 影片（長度跟音訊一致）
                    self.log("🖼️ 侦测到图片模式：正在将图片+音讯合成影片...")
                    cmd = [
                        str(self.ffmpeg_exe), "-y",
                        "-loop", "1", "-i", str(video_path),
                        "-i", str(ext_audio),
                        "-c:v", "libx264",
                        "-tune", "stillimage",
                        "-r", "30",
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k",
                        "-shortest",
                    ]
                else:
                    # 影片 + 外掛音訊 → 替換音軌（保留原畫面）
                    self.log("🎧 已勾选外挂音讯：正在替换影片音轨...")
                    cmd = [
                        str(self.ffmpeg_exe), "-y",
                        "-i", str(video_path),
                        "-i", str(ext_audio),
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k",
                        "-shortest",
                    ]
                if tmp_suffix == "mp4":
                    cmd += ["-movflags", "+faststart"]
                cmd += [tmp_video]

                try:
                    subprocess.run(cmd, check=True, creationflags=self.subp_flags, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    working_video = tmp_video
                except subprocess.CalledProcessError as e:
                    self.log("❌ 产生工作影片失败（ffmpeg 回传错误）")
                    if getattr(e, "stderr", None):
                        for line in [l for l in e.stderr.splitlines() if l.strip()][-20:]:
                            self.log(f"    [ffmpeg] {line}")
                    return False
                except Exception as e:
                    self.log(f"❌ 产生工作影片失败: {str(e)}")
                    return False

            if is_json_subtitle:
                self.log("🎤 侦测到 JSON 字幕，将处理为 KTV 逐字效果...")
                success = self.process_json_subtitle_to_video(working_video, subtitle_path, str(output_file))
            elif is_srt_subtitle:
                self.log("📝 侦测到 SRT 字幕，将套用字幕样式后烧录...")
                success = self.process_srt_subtitle_to_video(working_video, subtitle_path, str(output_file))
            elif is_ass_subtitle:
                self.log("🎨 侦测到 ASS/SSA 字幕，将直接烧录进影片...")
                success = self.burn_ass_subtitle_to_video(working_video, subtitle_path, str(output_file))
            else:
                self.log("📝 正在使用 FFmpeg 合并字幕...")
                success = self.merge_subtitle_with_ffmpeg(working_video, subtitle_path, str(output_file), vfmt)

            if success:
                self.update_progress(100, "合并完成")
                self.log(f"✅ 成功生成影片: {output_file.name}")
                self._show_done_and_open("完成", f"合并完成！\n档案已储存至: {output_file}", output_dir, select_file=str(output_file))
            else:
                self.log("❌ 合并失败。")
                messagebox.showerror("错误", "合并失败，请查看日志！")

            # 清理暫存工作影片（失敗也嘗試清理）
            with suppress(Exception):
                if tmp_video_to_cleanup: os.remove(tmp_video_to_cleanup)

        except Exception as e:
            self._log_exception("❌ 合并过程中出错", e)
        finally:
            self.finish_processing()

    def merge_subtitle_with_ffmpeg(self, video_path, subtitle_path, output_file, vfmt):
        try:
            subtitle_codec = "mov_text" if vfmt == "mp4" else "srt"
            cmd = [
                str(self.ffmpeg_exe), "-y", "-i", str(video_path), "-i", str(subtitle_path),
                "-map", "0:v:0", "-map", "0:a?", "-map", "1:s:0",
                "-c:v", "copy", "-c:a", "copy", "-c:s", subtitle_codec,
                "-disposition:s:0", "default",
                "-metadata:s:s:0", "title=字幕",
                str(output_file)
            ]
            self.log("  > 正在执行 FFmpeg...")
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            self.log("  ✅ FFmpeg 合并完成")
            return True
        except Exception as e:
            self.log(f"  ❌ FFmpeg 合并失败: {str(e)}")
            return False

    def process_json_subtitle_to_video(self, video_path, json_path, output_file):
        try:
            self.log("  > 正在读取 JSON 字幕...")
            with open(json_path, 'r', encoding='utf-8') as f:
                lyrics_data = json.load(f)

            kw = self._collect_json_to_ass_kwargs(video_path)
            unplayed_color = kw['unplayed_color']
            played_color   = kw['played_color']
            border_color   = kw['border_color']
            use_played_border = hasattr(self, 'ktv_use_played_border_var') and self.ktv_use_played_border_var.get()
            played_border_color = kw['played_border_color']
            two_line_enabled = kw['two_line']
            advance_sec      = kw['advance_sec']
            top_x_offset     = kw['top_x_offset']
            bottom_x_offset  = kw['bottom_x_offset']
            font_size        = kw['font_size']
            selected_font    = kw['font_name']

            self.log("  🎤 正在生成 KTV 滑动渐变字幕...")
            self.log(f"    - 未唱颜色: {unplayed_color}")
            self.log(f"    - 已唱颜色: {played_color}")
            self.log(f"    - 未唱边框: {border_color}")
            self.log(f"    - 已唱边框: {played_border_color if use_played_border else '关闭（边框同未唱，但仍使用滑动渐变）'}")
            self.log(f"    - 字体: {selected_font}  大小: {font_size}pt")
            self.log("    - 变色模式: 滑动渐变")
            if two_line_enabled:
                self.log(f"    - 双行字幕: 开启（下一句提前 {advance_sec:.2f} 秒出现）")
                self.log(f"    - 双行水平位置: 上行 X={top_x_offset:.0f} / 下行 X={bottom_x_offset:.0f}（正右负左）")
            else:
                self.log("    - 双行字幕: 关闭")
            _vw, _vh = kw['video_width'], kw['video_height']
            if _vw and _vh:
                self.log(f"    - 影片解析度: {_vw}x{_vh}（PlayResX 将设为 {int(round(1080 * _vw / _vh))}）")

            ass_file = Path(output_file).parent / f"{Path(json_path).stem}.ass"
            self._json_to_ass(lyrics_data, str(ass_file),
                              kw.pop('unplayed_color'), kw.pop('played_color'), kw.pop('border_color'),
                              kw.pop('font_name'), kw.pop('margin_v_offset'), kw.pop('color_mode'), kw.pop('font_size'),
                              **kw)

            self.log("  🎬 正在使用 FFmpeg 烧录字幕...")
            return self.burn_ass_subtitle_to_video(video_path, str(ass_file), output_file)

        except Exception as e:
            self._log_exception("  ❌ JSON 字幕处理失败: ", e)
            return False

    def process_srt_subtitle_to_video(self, video_path, srt_path, output_file):
        """将 SRT 字幕套用字幕样式后烧录进影片"""
        try:
            text_color, border_color, selected_font, font_size, margin_v_offset, outline_size = self._get_srt_style_params()
            use_border = self.srt_use_border_var.get() if hasattr(self, 'srt_use_border_var') and self.srt_use_border_var else True
            two_line, advance_sec, top_x, bottom_x, line_gap_px = self._read_two_line_settings()

            vw, vh = self._get_video_size(video_path)

            self.log(
                f"  📝 字幕样式: 颜色={text_color}  "
                f"边框={'开启' if use_border else '关闭'}({border_color})  "
                f"字体={selected_font} {font_size}pt  边框粗细={outline_size}px  "
                f"双行={'开启' if two_line else '关闭'}"
            )

            ass_file = str(Path(output_file).parent / (Path(srt_path).stem + ".ass"))
            self._srt_to_ass(
                srt_path, ass_file,
                text_color, border_color,
                selected_font, font_size,
                margin_v_offset, outline_size,
                two_line=two_line,
                advance_sec=advance_sec,
                top_x_offset=top_x,
                bottom_x_offset=bottom_x,
                line_gap_px=line_gap_px,
                video_width=vw, video_height=vh
            )

            self.log("  🎬 正在烧录字幕样式...")
            return self.burn_ass_subtitle_to_video(video_path, ass_file, output_file)

        except Exception as e:
            self._log_exception("  ❌ SRT 字幕处理失败: ", e)
            return False

    @staticmethod
    def _hex_to_bgr(hex_color, inline=False):
        """将 #RRGGBB 色码转换为 ASS &HBBGGRR 格式；inline=True 时结尾带 & 符号"""
        h = hex_color.lstrip('#')
        if len(h) == 3: h = ''.join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H{b:02X}{g:02X}{r:02X}" + ("&" if inline else "")

    def _srt_to_ass(
        self,
        srt_path, ass_file,
        text_color, border_color,
        font_name, font_size,
        margin_v_offset, outline_size=4,
        *,
        two_line=False,
        advance_sec=1.5,
        top_x_offset=0.0,
        bottom_x_offset=0.0,
        line_gap_px=None,
        video_width=None,
        video_height=None,
    ):
        """将 SRT 字幕转换为带样式的 ASS 格式（单一颜色；支援双行预览）"""
        try:
            hex_to_bgr = self._hex_to_bgr

            def parse_srt_time_to_sec(srt_time: str) -> float:
                srt_time = srt_time.strip().replace(',', '.')
                h, m, rest = srt_time.split(':', 2)
                s, ms = rest.split('.')
                return int(h) * 3600 + int(m) * 60 + int(s) + (int(ms[:3]) / 1000.0)

            format_ass_time = self._format_ass_time

            text_bgr = hex_to_bgr(text_color)
            border_bgr = hex_to_bgr(border_color)
            base_margin_v = 50
            margin_v = max(0, base_margin_v + margin_v_offset)

            play_res_y = 1080
            if video_width and video_height and int(video_height) > 0:
                play_res_x = int(round(play_res_y * float(video_width) / float(video_height)))
            else:
                play_res_x = 1920
            base_x = play_res_x // 2

            # 讀取 blocks
            srt_content = Path(srt_path).read_text(encoding='utf-8', errors='ignore')
            srt_content = srt_content.replace('\r\n', '\n').replace('\r', '\n')
            block_re = re.compile(
                r'\d+\s*\n'
                r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n'
                r'(.*?)(?=\n\n|\Z)',
                re.DOTALL
            )
            blocks = []
            for m in block_re.finditer(srt_content):
                raw_start = m.group(1)
                raw_end = m.group(2)
                raw_text = (m.group(3) or "").strip()
                raw_text = re.sub(r'<[^>]+>', '', raw_text)
                raw_text = raw_text.replace('\r\n', '\n').replace('\r', '\n')
                raw_text = raw_text.replace('\n', ' ').strip()
                if not raw_text:
                    continue
                st = parse_srt_time_to_sec(raw_start)
                et = parse_srt_time_to_sec(raw_end)
                if et <= st:
                    continue
                blocks.append((st, et, raw_text))

            with open(ass_file, 'w', encoding='utf-8-sig', newline='\n') as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("YCbCr Matrix: TV.709\n")
                f.write(f"PlayResX: {play_res_x}\n")
                f.write(f"PlayResY: {play_res_y}\n\n")

                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")

                if two_line:
                    line_gap = line_gap_px if line_gap_px is not None else int(font_size * 1.15) + 18
                    margin_v_top = margin_v + line_gap
                    f.write(f"Style: SRT_Bottom,{font_name},{font_size},{text_bgr},{text_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: SRT_Top,{font_name},{font_size},{text_bgr},{text_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v_top},1\n\n")
                else:
                    f.write(f"Style: SRT_Bottom,{font_name},{font_size},{text_bgr},{text_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n\n")

                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

                if not blocks:
                    self.log("  ⚠️ 找不到有效的 SRT 字幕区块。")
                    return

                # 單行：全部放底部；雙行：和 JSON 一樣用「交錯 Top/Bottom + 提前預覽」
                if not two_line:
                    x = float(base_x) + float(bottom_x_offset)
                    y = float(play_res_y - margin_v)
                    for st, et, tx in blocks:
                        ass_start = format_ass_time(st)
                        ass_end = format_ass_time(et)
                        f.write(f"Dialogue: 0,{ass_start},{ass_end},SRT_Bottom,,0,0,0,,{{\\pos({x:.0f},{y:.0f})}}{tx}\n")
                else:
                    try:
                        adv = float(advance_sec)
                    except Exception:
                        adv = 1.5
                    if adv < 0:
                        adv = 0.0
                    line_gap = line_gap_px if line_gap_px is not None else int(font_size * 1.15) + 18

                    for idx, (st, et, tx) in enumerate(blocks):
                        if idx % 2 == 0:
                            style = "SRT_Top"
                            x = float(base_x) + float(top_x_offset)
                            y = float(play_res_y - (margin_v + line_gap))
                        else:
                            style = "SRT_Bottom"
                            x = float(base_x) + float(bottom_x_offset)
                            y = float(play_res_y - margin_v)

                        ass_start = format_ass_time(st)
                        ass_end = format_ass_time(et)

                        # 提前顯示（預覽）— 行為與 JSON 一致：在本句開始前先顯示
                        if adv > 0:
                            preload_start = max(0.0, float(st) - adv)
                            if preload_start < float(st) - 0.001:
                                ass_pre = format_ass_time(preload_start)
                                f.write(f"Dialogue: 0,{ass_pre},{ass_start},{style},,0,0,0,,{{\\pos({x:.0f},{y:.0f})}}{tx}\n")

                        # 正式顯示
                        f.write(f"Dialogue: 0,{ass_start},{ass_end},{style},,0,0,0,,{{\\pos({x:.0f},{y:.0f})}}{tx}\n")

            self.log(f"  ✅ 已生成样式化 ASS 字幕: {Path(ass_file).name}")
        except Exception as e:
            self._log_exception("  ⚠️ SRT 转 ASS 失败: ", e)

    @staticmethod
    def _format_srt_timestamp(seconds):
        """格式化秒数为 SRT 时间戳记 HH:MM:SS,mmm"""
        h, m, s = int(seconds // 3600), int((seconds % 3600) // 60), int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d},{int((seconds - int(seconds)) * 1000):03d}"

    @staticmethod
    def _format_ass_time(seconds):
        """格式化秒数为 ASS 时间戳记 H:MM:SS.cc"""
        try: seconds = max(0.0, float(seconds or 0))
        except Exception: seconds = 0.0
        return f"{int(seconds//3600):01d}:{int((seconds%3600)//60):02d}:{int(seconds%60):02d}.{int((seconds-int(seconds))*100):02d}"

    def _json_to_srt(self, lyrics_data, srt_file):
        try:
            with open(srt_file, 'w', encoding='utf-8-sig') as f:
                srt_idx = 1
                for seg in lyrics_data.get('segments', []):
                    text = seg.get('text', '').replace('\n', ' ').strip()
                    if not text: continue
                    f.write(f"{srt_idx}\n{self._format_srt_timestamp(seg['start'])} --> {self._format_srt_timestamp(seg['end'])}\n{text}\n\n")
                    srt_idx += 1
            self.log(f"  ✅ 已转换为 SRT: {Path(srt_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ JSON 转 SRT 失败: {str(e)}")

    def _json_to_ass(self, lyrics_data, ass_file, unplayed_color, played_color, border_color, font_name="微软正黑体", margin_v_offset=0, color_mode="char", font_size=60, two_line=False, advance_sec=1.5, top_x_offset=0.0, bottom_x_offset=0.0, line_gap_px=None, played_border_color=None, use_clip_mask=True, pre_show_sec=0.0, hold_sec=0.5, speed_factor=1.0, singing_end_ratio=1.0, video_width=None, video_height=None, outline_size=4):
        try:
            hex_to_bgr = self._hex_to_bgr
            format_ass_time = self._format_ass_time

            def escape_ass_text_plain(s: str) -> str:
                """用在一般文字（非 override tag）"""
                if s is None:
                    return ""
                s = str(s)
                s = s.replace("\\", "\\\\")
                s = s.replace("{", "\\{").replace("}", "\\}")
                s = s.replace("\r\n", "\n").replace("\r", "\n")
                s = s.replace("\n", "\\N")
                return s

            def estimate_text_width_px(text: str, font_size_px: int) -> float:
                r"""
                估算字幕文字宽度（像素）。
                目的：用于 \clip 做左右半遮罩，让同一行可同时呈现
                  右半（未唱）：白字+黑边
                  左半（已唱）：蓝字+白边
                注：ASS 无法在同一行内真正做到「边框跟著 Karaoke 逐字变化」，
                    因此使用「两行叠图 + 逐步 \clip」的近似方式；宽度用估算即可。
                """
                if not text:
                    return float(font_size_px) * 2
                total_units = 0.0
                for ch in str(text):
                    cp = ord(ch)
                    if ch.isspace():
                        total_units += 0.35
                    elif (
                        0x4E00 <= cp <= 0x9FFF or
                        0x3400 <= cp <= 0x4DBF or
                        0x20000 <= cp <= 0x2A6DF or
                        0x3040 <= cp <= 0x30FF or
                        0xAC00 <= cp <= 0xD7A3 or
                        0xFF01 <= cp <= 0xFF60
                    ):
                        # 與 Gemini 版一致：CJK 字元字寬 = font_size * 0.8
                        total_units += 0.45
                    else:
                        if ch in "WMmw":
                            total_units += 0.85
                        elif ch in "iltI1|.,;: ":
                            total_units += 0.38
                        elif ch in "frt":
                            total_units += 0.50
                        elif ch.isupper():
                            total_units += 0.72
                        else:
                            total_units += 0.60
                return float(font_size_px) * total_units

            def _make_tk_measurer(font_family: str, font_size_pt: int):
                """
                取得 Tk 的字宽量测函数（像素）。
                注意：Tk 的像素尺度不一定等同 ASS PlayRes 的尺度，
                因此只用来提供「相对宽度分布」（prefix/total 的比例），
                再映射回我们自己的 line_est_px（PlayRes 尺度）以避免整体尺度跑掉。
                """
                try:
                    import tkinter.font as tkfont
                    # root 必須存在；此程式為 Tk GUI，通常可用
                    _root = getattr(self, "root", None)
                    if _root is None:
                        return None
                    fnt = tkfont.Font(root=_root, family=font_family, size=int(font_size_pt))
                    return lambda s: float(fnt.measure(s))
                except Exception:
                    return None

            unplayed_bgr = hex_to_bgr(unplayed_color)
            played_bgr = hex_to_bgr(played_color)
            border_bgr = hex_to_bgr(border_color)
            played_border_bgr = hex_to_bgr(played_border_color) if played_border_color else border_bgr

            base_margin_v = 50
            margin_v = max(0, base_margin_v + margin_v_offset)
            # 依影片實際長寬比動態決定 PlayResX，使 \clip 座標與字幕 \pos 座標比例一致。
            # 例如 1080x1080 正方形影片 → play_res_x = 1080，避免 \clip 左邊界偏移露出已唱色。
            play_res_y = 1080
            if video_width and video_height and video_height > 0:
                play_res_x = int(round(play_res_y * video_width / video_height))
            else:
                play_res_x = 1920  # 預設 16:9
            base_x = play_res_x // 2

            with open(ass_file, 'w', encoding='utf-8-sig', newline='\n') as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("YCbCr Matrix: TV.709\n")
                f.write(f"PlayResX: {play_res_x}\n")
                f.write(f"PlayResY: {play_res_y}\n\n")

                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                if two_line:
                    line_gap = line_gap_px if line_gap_px is not None else int(font_size * 1.15) + 18
                    margin_v_top = margin_v + line_gap
                    f.write(f"Style: KTV_Bottom_Karaoke,{font_name},{font_size},{played_bgr},{unplayed_bgr},{played_border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: KTV_Bottom_Preview,{font_name},{font_size},{unplayed_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: KTV_Top_Karaoke,{font_name},{font_size},{played_bgr},{unplayed_bgr},{played_border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v_top},1\n")
                    f.write(f"Style: KTV_Top_Preview,{font_name},{font_size},{unplayed_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v_top},1\n")

                    f.write(f"Style: KTV_Unplayed,{font_name},{font_size},{unplayed_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: KTV_Played,{font_name},{font_size},{played_bgr},{played_bgr},{played_border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    # 只畫「已唱邊框」用（主色會在 Dialogue 端用 \1a 設為透明）
                    f.write(f"Style: KTV_Played_Border,{font_name},{font_size},{played_bgr},{played_bgr},{played_border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: KTV_Karaoke,{font_name},{font_size},{played_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n\n")
                else:
                    f.write(f"Style: KTV_Unplayed,{font_name},{font_size},{unplayed_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: KTV_Played,{font_name},{font_size},{played_bgr},{played_bgr},{played_border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    # 只畫「已唱邊框」用（主色會在 Dialogue 端用 \1a 設為透明）
                    f.write(f"Style: KTV_Played_Border,{font_name},{font_size},{played_bgr},{played_bgr},{played_border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n")
                    f.write(f"Style: KTV_Karaoke,{font_name},{font_size},{played_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n\n")

                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

                # NOTE: 這段邏輯需與 yt2mkv_tools2-15F.py 保持一致（避免滑動漸變速度感異常）
                def _build_perchar_clip_anim(clip_cs_list, total_line_px,
                                            clip_left, clip_right, H,
                                            pre_ms, singing_end_ms,
                                            clip_text_left_x, clip_line_est_px):
                    tags = f"\\clip({int(clip_left)},0,{int(clip_left)},{H})"
                    denom = float(total_line_px or 1.0)
                    prev_xr    = int(clip_left)
                    prev_end_t = pre_ms
                    last_idx = len(clip_cs_list) - 1
                    for idx, (word_start_ms, word_end_ms, cum_px) in enumerate(clip_cs_list):
                        is_last = (idx == last_idx)
                        frac = min(1.0, float(cum_px) / denom)
                        xr = int(clip_text_left_x + clip_line_est_px * frac)
                        xr = max(int(clip_left), min(int(clip_right), xr))
                        if is_last:
                            xr = int(clip_right)
                        ws = word_start_ms + pre_ms
                        we = word_end_ms   + pre_ms
                        ws = min(ws, singing_end_ms)
                        we = min(we, singing_end_ms)
                        if ws > prev_end_t + 1:
                            tags += f"\\t({prev_end_t},{prev_end_t + 1},\\clip({int(clip_left)},0,{prev_xr},{H}))"
                        t_start = max(prev_end_t, ws)
                        t_end   = max(t_start + 1, we)
                        tags += f"\\t({t_start},{t_end},\\clip({int(clip_left)},0,{xr},{H}))"
                        prev_xr    = xr
                        prev_end_t = t_end
                    if prev_end_t < singing_end_ms:
                        tags += f"\\t({prev_end_t},{singing_end_ms},\\clip({int(clip_left)},0,{int(clip_right)},{H}))"
                    return tags

                if 'segments' in lyrics_data:
                    global_line_idx = 0
                    # ── 效能優化：Font 物件只建立一次，避免在每行迴圈內反覆建立 Tk GUI 資源
                    _shared_tk_measure = _make_tk_measurer(font_name, int(font_size))
                    _all_line_starts = []
                    for _seg in lyrics_data['segments']:
                        _st = _seg.get('start', 0)
                        _et = _seg.get('end', 0)
                        _tx = _seg.get('text', '').strip()
                        if not _tx:
                            continue
                        _tn = _tx.replace('\r\n', '\n').replace('\r', '\n')
                        _cjk = any('\u4e00' <= _c <= '\u9fff' or '\u3040' <= _c <= '\u30ff' or '\uac00' <= _c <= '\ud7a3' for _c in _tn)
                        # Bug 修正：CJK 文字不依賴換行符分句，先把 \n 換成空格再用空格切割，
                        # 避免含 \n 的字串被 escape_ass_text_plain 轉成 \N 造成單行顯示成兩行重疊
                        _sl = [_l.strip() for _l in (_tn.replace('\n', ' ').split(' ') if _cjk else _tn.split('\n')) if _l.strip()]
                        if not _sl:
                            continue
                        _ws = _seg.get('words', [])
                        _sdur = (_et - _st) / len(_sl)
                        for _li, _ln in enumerate(_sl):
                            _ls = _st + _li * _sdur
                            _lw = [_w for _w in _ws if _w.get('word', _w.get('text', '')).strip()]
                            if _lw:
                                _w0_start = float(_lw[0].get('start', _ls))
                                _all_line_starts.append(_w0_start if _w0_start > _ls else _ls)
                            else:
                                _all_line_starts.append(float(_ls))
                    for segment in lyrics_data['segments']:
                        start_time = segment.get('start', 0)
                        end_time = segment.get('end', 0)
                        text = segment.get('text', '').strip()

                        if not text:
                            continue

                        words = segment.get('words', [])

                        text_norm = text.replace('\r\n', '\n').replace('\r', '\n')
                        is_cjk = any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7a3' for c in text_norm)
                        if is_cjk:
                            # Bug 修正：CJK 文字不依賴換行符分句，先把 \n 換成空格再用空格切割，
                            # 避免含 \n 的字串被 escape_ass_text_plain 轉成 \N 造成單行顯示成兩行重疊
                            sub_lines = [l.strip() for l in text_norm.replace('\n', ' ').split(' ') if l.strip()]
                        else:
                            sub_lines = [l.strip() for l in text_norm.split('\n') if l.strip()]
                        if not sub_lines:
                            continue

                        def group_words_to_lines(sub_lines, words):
                            """把 words 依序分配给每个子句，回传 list of list"""
                            groups = [[] for _ in sub_lines]
                            if not words:
                                return groups
                            w_idx = 0
                            for line_idx, line in enumerate(sub_lines):
                                accumulated = ''
                                while w_idx < len(words):
                                    w_text = words[w_idx].get('word', words[w_idx].get('text', '')).strip()
                                    if not w_text:
                                        w_idx += 1
                                        continue
                                    candidate = (accumulated + w_text).replace(' ', '')
                                    target = line.replace(' ', '')
                                    if target.startswith(candidate):
                                        groups[line_idx].append(words[w_idx])
                                        accumulated += w_text
                                        w_idx += 1
                                        if accumulated.replace(' ', '') == target:
                                            break
                                    else:
                                        break
                            while w_idx < len(words):
                                groups[-1].append(words[w_idx])
                                w_idx += 1
                            return groups

                        word_groups = group_words_to_lines(sub_lines, words)
                        seg_dur = (end_time - start_time) / len(sub_lines)

                        for line_idx, line in enumerate(sub_lines):
                            line_start = start_time + line_idx * seg_dur
                            line_end   = start_time + (line_idx + 1) * seg_dur

                            line_words = word_groups[line_idx]
                            if line_words:
                                w_starts = [w.get('start') for w in line_words if w.get('start') is not None]
                                w_ends   = [w.get('end')   for w in line_words if w.get('end')   is not None]
                                if w_starts:
                                    first_word_start = min(w_starts)
                                    # 重要：不要因為第一個字/單字的時間點較晚，就把整句 line_start 往後推，
                                    # 否則會造成「歌手已經唱了 0.x 秒，漸變才開始跑」的延遲感。
                                    # 我們只允許往前（更早）擴張，避免縮短/截斷整句時間。
                                    if first_word_start < line_start:
                                        line_start = first_word_start
                                if w_ends:
                                    last_word_end = max(w_ends)
                                    # 同理：不要把 line_end 縮短成最後一個字的結束時間，
                                    # 否則會變成「歌手還沒唱完，漸變就跑完」。
                                    if last_word_end > line_end:
                                        line_end = last_word_end
                            if line_end <= line_start:
                                line_end = line_start + 0.5

                            # pre_show_sec / hold_sec 由呼叫端傳入（GUI 微調參數）
                            ass_start = format_ass_time(max(0.0, line_start - pre_show_sec))
                            if not two_line and global_line_idx + 1 < len(_all_line_starts):
                                next_start = _all_line_starts[global_line_idx + 1]
                                _ass_end_sec = min(line_end + hold_sec, next_start)
                            else:
                                _ass_end_sec = line_end + hold_sec
                            ass_end   = format_ass_time(_ass_end_sec)

                            if line_words:
                                # ─────────────────────────────────────────────────────────────
                                # 依語言決定漸變模式（依你的需求）：
                                # - 中文(CJK)：滑動漸變（\clip + \t）
                                # - 英文(非CJK)：逐字/逐詞漸變（\k），避免字寬/字型 fallback 導致滑動對不準
                                # ─────────────────────────────────────────────────────────────
                                line_color_mode = color_mode
                                if color_mode == "slide" and (not is_cjk):
                                    line_color_mode = "char"

                                karaoke_text = ''
                                # slide 模式：主色漸變改用純 \clip 線性動畫，不需要 \kf
                                # karaoke_text 在 slide 模式下僅供 use_clip_mask=False 的 fallback 使用
                                tag = "k"

                                flat_chars = []
                                is_first_token = True
                                for w in line_words:
                                    w_start = w.get('start', line_start)
                                    w_end   = w.get('end',   w_start)
                                    w_end   = max(w_start + 0.001, min(w_end, line_end + 0.20))
                                    w_start = max(0.0, min(w_start, line_end - 0.001))
                                    w_raw   = w.get('word', w.get('text', ''))
                                    has_leading_space = w_raw.startswith(' ')
                                    w_stripped = w_raw.strip()
                                    if not w_stripped:
                                        continue

                                    is_cjk_word = any( '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7a3' for c in w_stripped )

                                    if is_cjk_word:
                                        chars_in_word = list(w_stripped)
                                        n_chars = len(chars_in_word)
                                        w_dur = max(0.001, w_end - w_start)
                                        char_dur = w_dur / n_chars
                                        for ci, ch in enumerate(chars_in_word):
                                            c_start = w_start + ci * char_dur
                                            c_end   = w_start + (ci + 1) * char_dur
                                            c_cs    = max(1, int(round(char_dur * 100)))
                                            c_px    = estimate_text_width_px(ch, int(font_size))
                                            leading = has_leading_space and ci == 0 and not is_first_token
                                            # 重要：英文/空白在「滑動漸變」時若把空白寬度算進唱字時間，
                                            # 會造成下一個字(或單字)一開始唱時，clip 先滑過空白而導致整體看起來「慢半拍」。
                                            # 因此把 leading space 的寬度獨立記錄，後續會安排在「字與字的 gap」中提早滑過。
                                            space_px = estimate_text_width_px(' ', int(font_size)) if leading else 0.0
                                            # 最後一欄保留「原始文字」，用於更準確的寬度量測（避免反解 escaped）
                                            flat_chars.append((escape_ass_text_plain(ch), c_cs, tag, leading, c_start, c_end, c_px, ch, space_px))
                                            is_first_token = False
                                    else:
                                        # 非 CJK（英文等）以「單字」為單位（符合你的需求：中文=字，英文=單字）
                                        w_cs = max(1, int(round((w_end - w_start) * 100)))
                                        leading = has_leading_space and not is_first_token
                                        space_px = estimate_text_width_px(' ', int(font_size)) if leading else 0.0
                                        w_px = estimate_text_width_px(w_stripped, int(font_size))
                                        flat_chars.append((escape_ass_text_plain(w_stripped), w_cs, tag, leading, w_start, w_end, w_px, w_stripped, space_px))
                                        is_first_token = False

                                prev_abs_end_cs = 0  # 上一個 token 結束的累計位置（相對 line_start，cs）
                                t_acc_cs = 0          # kf 累積時間（與 \kf 完全相同的時間軸）
                                cum_px = 0.0
                                render_plain = ""
                                clip_cs_list = []

                                # 以 Tk 取得「相對寬度分佈」，用來修正前慢後快（估算字寬偏差造成的非線性）
                                # 注意：tk_measure 已在 segments 迴圈外建立（_shared_tk_measure），此處直接重用
                                line_est_px = float(estimate_text_width_px(line, int(font_size)) or 1.0)
                                tk_measure = _shared_tk_measure
                                tk_total = None
                                if tk_measure:
                                    try:
                                        tk_total = tk_measure(str(line))
                                        if tk_total <= 1:
                                            tk_total = None
                                    except Exception:
                                        tk_total = None

                                prev_token_end_ms = 0
                                for i, (escaped, _old_cs, tag, leading, w_start_t, w_end_t, tok_px, plain_tok, space_px) in enumerate(flat_chars):
                                    total_cs   = max(1, int(round((line_end - line_start) * 100)))
                                    w_start_cs = int(round((w_start_t - line_start) * 100))
                                    w_start_cs = max(0, min(w_start_cs, total_cs - 1))

                                    w_end_cs_abs = int(round((w_end_t - line_start) * 100))
                                    w_end_cs_abs = max(w_start_cs + 1, min(w_end_cs_abs, total_cs + 20))
                                    w_dur_cs = max(1, w_end_cs_abs - w_start_cs)

                                    gap_cs = w_start_cs - prev_abs_end_cs
                                    if gap_cs > 0:
                                        karaoke_text += f"{{\\{tag}{gap_cs}}}"
                                        t_acc_cs += gap_cs  # kf 累積時間跟著 gap 走

                                    char_tag = f"{{\\{tag}{w_dur_cs}}}"
                                    if leading:
                                        karaoke_text += f" {char_tag}{escaped}"
                                    else:
                                        karaoke_text += f"{char_tag}{escaped}"

                                    token_end_cs = w_start_cs + w_dur_cs
                                    prev_abs_end_cs = token_end_cs

                                    # slide 模式：時間軸直接用 Whisper 實際時間（相對 line_start 的 ms）
                                    # 英文單字之間有 gap，必須用實際時間才能對齊歌手演唱位置
                                    actual_start_ms = max(0, int(round((w_start_t - line_start) * 1000)))
                                    actual_end_ms   = max(actual_start_ms + 1, int(round((w_end_t - line_start) * 1000)))

                                    # 取得 prefix 實際字寬比例，再映射到 line_est_px（PlayRes 尺度）
                                    # 重要：把 leading space 的寬度「提早」放到 gap 期間滑過，
                                    # 避免唱字開始時 clip 先滑空白導致整體看起來對不上。
                                    if leading and space_px > 0:
                                        # 先把「空白」加入 prefix
                                        render_plain += " "
                                        if tk_total and tk_measure:
                                            try:
                                                tk_prefix = tk_measure(render_plain)
                                                frac_w = max(0.0, min(1.0, tk_prefix / tk_total))
                                                cum_px = frac_w * line_est_px
                                            except Exception:
                                                cum_px += float(space_px)
                                        else:
                                            cum_px += float(space_px)

                                        # 安排空白的滑動：放在唱字開始前的最後一小段時間
                                        gap_ms = max(0, int(actual_start_ms - prev_token_end_ms))
                                        move_ms = int(min(120, gap_ms)) if gap_ms > 0 else 1
                                        space_start_ms = max(int(prev_token_end_ms), int(actual_start_ms - move_ms))
                                        space_end_ms = max(space_start_ms + 1, int(actual_start_ms))
                                        clip_cs_list.append((space_start_ms, space_end_ms, cum_px))
                                        prev_token_end_ms = int(actual_start_ms)

                                    # 再把實際字(或單字)加入 prefix
                                    render_plain += str(plain_tok)
                                    if tk_total and tk_measure:
                                        try:
                                            tk_prefix = tk_measure(render_plain)
                                            frac_w = max(0.0, min(1.0, tk_prefix / tk_total))
                                            cum_px = frac_w * line_est_px
                                        except Exception:
                                            cum_px += float(tok_px)
                                    else:
                                        cum_px += float(tok_px)

                                    t_acc_cs += w_dur_cs  # 保留供 karaoke_text(\k) 使用
                                    clip_cs_list.append((actual_start_ms, actual_end_ms, cum_px))
                                    prev_token_end_ms = int(actual_end_ms)

                                # 讓 total_line_px 與 clip 的座標系一致（PlayRes 尺度）
                                total_line_px = line_est_px or (cum_px or 1.0)
                                if not clip_cs_list:
                                    clip_cs_list = [(0, 10, 1.0)]

                                # ── 時間軸縮放（speed_factor 等比，不強制對齊整句長度）──
                                # 每個字的漸變時間完全照 Whisper 逐字時間軸，
                                # 0.2 秒的字就快，3 秒的尾音字就慢，原汁原味保留。
                                # 跑完後 clip 停在全亮，hold_sec 期間維持全亮直到字幕消失。
                                #   speed_factor = 1.0 → 完全照 Whisper 時間（預設）
                                #   speed_factor = 0.8 → 整體加速到 80%（提早跑完，留更多靜止尾音）
                                #   speed_factor = 1.2 → 整體減速到 120%（跑更久）
                                if color_mode == "slide" and clip_cs_list and abs(speed_factor - 1.0) > 0.001:
                                    raw0 = int(clip_cs_list[0][0])
                                    new_list = []
                                    prev_end = 0
                                    for s, e, px in clip_cs_list:
                                        ns = int(round((float(s) - float(raw0)) * speed_factor))
                                        ne = int(round((float(e) - float(raw0)) * speed_factor))
                                        if ns < prev_end:
                                            ns = prev_end
                                        if ne <= ns:
                                            ne = ns + 1
                                        ns = max(0, ns)
                                        ne = max(1, ne)
                                        new_list.append((ns, ne, px))
                                        prev_end = ne
                                    clip_cs_list = new_list

                                # kf_total_ms：拉伸後最後一個 token 的結束毫秒
                                kf_total_ms = int(clip_cs_list[-1][1]) if clip_cs_list else 0

                                # 英文（逐字/逐詞）已唱白框：用「逐詞顯示的覆蓋層」達成
                                # - 底層：KTV_Unplayed（白字黑框）
                                # - 覆蓋：KTV_Played（藍字白框）每個單字在自己的 start_ms 才顯示
                                # 這個方法完全不依賴字寬/遮罩，因此英文也能穩定對齊聲音。
                                played_overlay_text = None
                                if line_color_mode == "char" and played_border_color:
                                    parts = []
                                    for (escaped, _old_cs, _tag, leading, w_start_t, _w_end_t, _tok_px, _plain_tok, _space_px) in flat_chars:
                                        start_ms = max(0, int(round((float(w_start_t) - float(line_start)) * 1000)))
                                        # 先隱藏（\1a/\3a 透明），在該單字 start_ms 瞬間顯示（1ms）
                                        show_tag = f"{{\\1a&HFF&\\3a&HFF&\\t({start_ms},{start_ms+1},\\1a&H00&\\3a&H00&)}}"
                                        parts.append((" " if leading else "") + show_tag + escaped)
                                    played_overlay_text = "".join(parts)

                            else:
                                chars = list(str(line))
                                duration = line_end - line_start
                                if duration <= 0:
                                    duration = 1.0
                                total_cs = int(round(duration * 100))
                                n = len(chars)
                                if n == 0:
                                    continue
                                base_cs = total_cs // n
                                remainder = total_cs - base_cs * n
                                # slide 模式不用 \kf，統一用 \k（karaoke_text 僅供 no-clip fallback）
                                tag = "k"
                                karaoke_text = ''
                                clip_cs_list = []
                                cum_ms = 0
                                cum_px = 0.0
                                render_plain = ""
                                line_est_px = float(estimate_text_width_px(line, int(font_size)) or 1.0)
                                tk_measure = _shared_tk_measure
                                tk_total = None
                                if tk_measure:
                                    try:
                                        tk_total = tk_measure(str(line))
                                        if tk_total <= 1:
                                            tk_total = None
                                    except Exception:
                                        tk_total = None
                                for i, char in enumerate(chars):
                                    char_cs = base_cs + (1 if i >= n - remainder else 0)
                                    escaped = escape_ass_text_plain(char)
                                    karaoke_text += f"{{\\{tag}{char_cs}}}{escaped}"
                                    prev_cum_ms = cum_ms
                                    cum_ms += char_cs * 10  # cs → ms
                                    render_plain += str(char)
                                    if tk_total and tk_measure:
                                        try:
                                            tk_prefix = tk_measure(render_plain)
                                            frac_w = max(0.0, min(1.0, tk_prefix / tk_total))
                                            cum_px = frac_w * line_est_px
                                        except Exception:
                                            cum_px += estimate_text_width_px(char, int(font_size))
                                    else:
                                        cum_px += estimate_text_width_px(char, int(font_size))
                                    clip_cs_list.append((prev_cum_ms, cum_ms, cum_px))
                                total_line_px = line_est_px or (cum_px or 1.0)
                                if not clip_cs_list:
                                    clip_cs_list = [(0, 1, 1.0)]
                                kf_total_ms = int(cum_ms)

                                # ── 時間軸拉伸（無 words fallback，均等分配版）──
                                # 保持每字相對比例，把終點拉到 duration * speed_factor
                                if abs(speed_factor - 1.0) > 0.001 and clip_cs_list:
                                    desired_dur_ms = max(1, int(round((line_end - line_start) * 1000)))
                                    target_dur_ms = max(1, int(round(desired_dur_ms * speed_factor)))
                                    raw1 = int(clip_cs_list[-1][1])
                                    denom = max(1, raw1)
                                    scale = float(target_dur_ms) / float(denom)
                                    new_list = []
                                    prev_end = 0
                                    for s, e, px in clip_cs_list:
                                        ns = int(round(float(s) * scale))
                                        ne = int(round(float(e) * scale))
                                        if ns < prev_end:
                                            ns = prev_end
                                        if ne <= ns:
                                            ne = ns + 1
                                        new_list.append((ns, ne, px))
                                        prev_end = ne
                                    ls, le, lpx = new_list[-1]
                                    new_list[-1] = (ls, target_dur_ms, lpx)
                                    clip_cs_list = new_list
                                    kf_total_ms = int(clip_cs_list[-1][1])

                            if two_line:
                                try:
                                    adv = float(advance_sec)
                                except Exception:
                                    adv = 1.5
                                if adv < 0:
                                    adv = 0.0

                                _line_gap = line_gap_px if line_gap_px is not None else int(font_size * 1.15) + 18
                                if global_line_idx % 2 == 0:
                                    preview_style = "KTV_Top_Preview"
                                    x = base_x + float(top_x_offset)
                                    y = play_res_y - (margin_v + _line_gap)
                                else:
                                    preview_style = "KTV_Bottom_Preview"
                                    x = base_x + float(bottom_x_offset)
                                    y = play_res_y - margin_v

                                try:
                                    x = float(x)
                                except Exception:
                                    x = float(base_x)
                                try:
                                    y = float(y)
                                except Exception:
                                    y = float(play_res_y - margin_v)

                                if adv > 0:
                                    preload_start = max(0.0, float(line_start) - adv)
                                    if preload_start < float(line_start) - 0.001:
                                        ass_preload_start = format_ass_time(preload_start)
                                        plain_line = escape_ass_text_plain(line)
                                        f.write(f"Dialogue: 0,{ass_preload_start},{ass_start},{preview_style},,0,0,0,,{{\\pos({x:.0f},{y:.0f})}}{plain_line}\n")

                                plain_line = escape_ass_text_plain(line)
                                center_x = float(x)

                                # 英文逐字：不使用遮罩，直接用 \k（最穩定、最貼聲音）
                                line_use_clip_mask = bool(use_clip_mask and line_color_mode == "slide")
                                if not line_use_clip_mask:
                                    f.write(f"Dialogue: 0,{ass_start},{ass_end},KTV_Unplayed,,0,0,0,,{{\\pos({center_x:.0f},{y:.0f})}}{plain_line}\n")
                                    if played_overlay_text:
                                        # 英文：藍字白框逐詞顯示（已唱邊框）
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Played,,0,0,0,,{{\\pos({center_x:.0f},{y:.0f})\\shad0}}{played_overlay_text}\n")
                                    else:
                                        # 沒勾已唱邊框時，仍用原本的逐字變色
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Karaoke,,0,0,0,,{{\\pos({center_x:.0f},{y:.0f})}}{karaoke_text}\n")
                                else:

                                    # 遮罩基準寬度要跟 \kf 的「時間軸」一致，避免因額外 padding 造成邊框推進變慢/變快
                                    # 重要：clip 的初始右邊界如果離文字太遠（例如有大量左側 padding），
                                    # 會造成「一開始看起來延遲，後面又追很快」的錯覺（因為 \t 在空白區移動）。
                                    # 因此：
                                    # - 左側不加 pad_cover，只保留 outline_pad（避免切到左邊框）
                                    # - 右側才加 pad_cover（避免跑到最右邊時被切）
                                    line_est_px = float(estimate_text_width_px(line, int(font_size)) or 1.0)
                                    pad_cover = max(60, int(font_size * 0.9))
                                    outline_px = outline_size
                                    outline_pad = outline_px * 2 + 6
                                    edge_expand = max(outline_px, outline_px * 2)
                                    # 關鍵修正：
                                    # clip 的左右界限要「寬於」文字，否則最左/最右會露出底下圖層。
                                    # 這裡用較貼近全形字的估算（0.92×字數×font_size）做 clip 寬度，
                                    # 動畫推進則改用 clip_left→clip_right 的座標系線性位移（見 _build_perchar_clip_anim），
                                    # 兩者不再互相打架。
                                    clip_line_est_px = len(line.replace(' ', '')) * float(font_size) * 0.92
                                    clip_text_left_x = float(center_x) - clip_line_est_px / 2.0
                                    clip_left = max(0.0, clip_text_left_x - outline_pad - edge_expand)
                                    clip_real_right_x = clip_text_left_x + clip_line_est_px
                                    clip_right = min(float(play_res_x), clip_real_right_x + outline_pad + pad_cover + edge_expand)
                                    if clip_right <= clip_left:
                                        clip_right = min(float(play_res_x), clip_left + 1)
                                    H = int(play_res_y)
                                    actual_pre_sec = min(pre_show_sec, float(line_start))
                                    tw_pre_ms = int(pre_show_sec * 1000)
                                    singing_end_ms = int(max(1.0, (float(line_end) - float(line_start)) + actual_pre_sec) * 1000)
                                    singing_end_ms = max(singing_end_ms, int(kf_total_ms + tw_pre_ms))
                                    # ── 尾音比例微調（singing_end_ratio < 1.0 → 提早跑完）──
                                    singing_end_ms = max(int(kf_total_ms + tw_pre_ms),
                                                         int(singing_end_ms * singing_end_ratio))

                                    f.write(f"Dialogue: 0,{ass_start},{ass_end},KTV_Unplayed,,0,0,0,,{{\\pos({center_x:.0f},{y:.0f})}}{plain_line}\n")

                                    if line_color_mode == "slide":

                                        clip_tags = _build_perchar_clip_anim(
                                            clip_cs_list, total_line_px,
                                            clip_left, clip_right, H,
                                            tw_pre_ms, singing_end_ms,
                                            clip_text_left_x, clip_line_est_px
                                        )
                                        # Layer 1：已唱邊框層（逐字連續 clip 推進）
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Played_Border,,0,0,0,,{{\\pos({center_x:.0f},{y:.0f}){clip_tags}}}{plain_line}\n")
                                    else:
                                        # char 模式：逐字 \k 瞬間變色（保留舊行為）
                                        n_clips = len(clip_cs_list)
                                        init_r = int(clip_left)
                                        border_tags = f"{{\\pos({center_x:.0f},{y:.0f})\\1a&HFF&\\shad0\\clip({int(clip_left)},0,{init_r},{H})"
                                        for idx, (abs_start_ms, abs_end_ms, cum_px) in enumerate(clip_cs_list):
                                            t_ms = min(singing_end_ms, abs_end_ms + tw_pre_ms)
                                            char_frac = (idx + 1) / n_clips
                                            xr = int(clip_left + (clip_right - clip_left) * char_frac)
                                            if idx == 0:
                                                end_ms = min(singing_end_ms, t_ms + 1)
                                                if tw_pre_ms < end_ms:
                                                    border_tags += f"\\t({tw_pre_ms},{end_ms},\\clip({int(clip_left)},0,{xr},{H}))"
                                            else:
                                                end_ms = min(singing_end_ms, t_ms + 1)
                                                if t_ms < end_ms:
                                                    border_tags += f"\\t({t_ms},{end_ms},\\clip({int(clip_left)},0,{xr},{H}))"
                                        if singing_end_ms >= tw_pre_ms + 2:
                                            border_tags += f"\\t({singing_end_ms-1},{singing_end_ms},\\clip({int(clip_left)},0,{int(clip_right)},{H}))"
                                        border_tags += "}"
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Played_Border,,0,0,0,,{border_tags}{plain_line}\n")
                            else:
                                plain_line = escape_ass_text_plain(line)
                                center_x = float(base_x)
                                # ── 重要：明確加上 \an2\pos(x,y) 以禁止 libass 碰撞偵測
                                # libass 在多個 Dialogue 同時顯示且位置相同時，會自動把各層
                                # 往上推避免重疊（Collision Detection），造成三層字幕分散在
                                # 不同高度而出現「重影/疊字」。
                                # \an2 = Alignment 2（底部置中，與 Style 一致）
                                # \pos(x,y) 明確固定位置後，libass 不再做碰撞偵測。
                                sl_y = float(play_res_y) - float(margin_v)
                                pos_tag = f"\\an2\\pos({center_x:.0f},{sl_y:.0f})"

                                line_use_clip_mask = bool(use_clip_mask and line_color_mode == "slide")
                                if not line_use_clip_mask:
                                    f.write(f"Dialogue: 0,{ass_start},{ass_end},KTV_Unplayed,,0,0,0,,{{{pos_tag}}}{plain_line}\n")
                                    if played_overlay_text:
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Played,,0,0,0,,{{{pos_tag}\\shad0}}{played_overlay_text}\n")
                                    else:
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Karaoke,,0,0,0,,{{{pos_tag}}}{karaoke_text}\n")
                                else:

                                    # 遮罩基準寬度要跟 \kf 的「時間軸」一致，避免因額外 padding 造成邊框推進變慢/變快
                                    line_est_px = float(estimate_text_width_px(line, int(font_size)) or 1.0)
                                    pad_cover = max(60, int(font_size * 0.9))
                                    outline_px = outline_size
                                    outline_pad = outline_px * 2 + 6
                                    edge_expand = max(outline_px, outline_px * 2)
                                    # 關鍵修正：同上，clip 寬度要跟 total_line_px 使用同一套尺度
                                    clip_line_est_px = len(line.replace(' ', '')) * float(font_size) * 0.92
                                    clip_text_left_x = float(center_x) - clip_line_est_px / 2.0
                                    clip_left = max(0.0, clip_text_left_x - outline_pad - edge_expand)
                                    clip_real_right_x = clip_text_left_x + clip_line_est_px
                                    clip_right = min(float(play_res_x), clip_real_right_x + outline_pad + pad_cover + edge_expand)
                                    if clip_right <= clip_left:
                                        clip_right = min(float(play_res_x), clip_left + 1)
                                    H = int(play_res_y)
                                    actual_pre_sec = min(pre_show_sec, float(line_start))
                                    sl_pre_ms = int(pre_show_sec * 1000)
                                    singing_end_ms = int(max(1.0, (float(line_end) - float(line_start)) + actual_pre_sec) * 1000)
                                    singing_end_ms = max(singing_end_ms, int(kf_total_ms + sl_pre_ms))
                                    # ── 尾音比例微調（singing_end_ratio < 1.0 → 提早跑完）──
                                    singing_end_ms = max(int(kf_total_ms + sl_pre_ms),
                                                         int(singing_end_ms * singing_end_ratio))

                                    f.write(f"Dialogue: 0,{ass_start},{ass_end},KTV_Unplayed,,0,0,0,,{{{pos_tag}}}{plain_line}\n")

                                    if line_color_mode == "slide":

                                        clip_tags = _build_perchar_clip_anim(
                                            clip_cs_list, total_line_px,
                                            clip_left, clip_right, H,
                                            sl_pre_ms, singing_end_ms,
                                            clip_text_left_x, clip_line_est_px
                                        )
                                        # Layer 1：已唱邊框層（逐字連續 clip 推進）
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Played_Border,,0,0,0,,{{{pos_tag}{clip_tags}}}{plain_line}\n")
                                    else:
                                        # char 模式：逐字 \k 瞬間變色（保留舊行為）
                                        n_clips = len(clip_cs_list)
                                        init_r = int(clip_left)
                                        border_tags = f"{{{pos_tag}\\1a&HFF&\\shad0\\clip({int(clip_left)},0,{init_r},{H})"
                                        for idx, (abs_start_ms, abs_end_ms, cum_px) in enumerate(clip_cs_list):
                                            t_ms = min(singing_end_ms, abs_end_ms + sl_pre_ms)
                                            char_frac = (idx + 1) / n_clips
                                            xr = int(clip_left + (clip_right - clip_left) * char_frac)
                                            if idx == 0:
                                                end_ms = min(singing_end_ms, t_ms + 1)
                                                if sl_pre_ms < end_ms:
                                                    border_tags += f"\\t({sl_pre_ms},{end_ms},\\clip({int(clip_left)},0,{xr},{H}))"
                                            else:
                                                end_ms = min(singing_end_ms, t_ms + 1)
                                                if t_ms < end_ms:
                                                    border_tags += f"\\t({t_ms},{end_ms},\\clip({int(clip_left)},0,{xr},{H}))"
                                        if singing_end_ms >= sl_pre_ms + 2:
                                            border_tags += f"\\t({singing_end_ms-1},{singing_end_ms},\\clip({int(clip_left)},0,{int(clip_right)},{H}))"
                                        border_tags += "}"
                                        f.write(f"Dialogue: 1,{ass_start},{ass_end},KTV_Played_Border,,0,0,0,,{border_tags}{plain_line}\n")

                            global_line_idx += 1

            mode_desc = "平滑滑动渐变" if color_mode == "slide" else "逐字变色"
            self.log(f"  ✅ 已生成 KTV {mode_desc}字幕: {Path(ass_file).name}")
        except Exception as e:
            self._log_exception("  ⚠️ JSON 转 ASS 失败: ", e)

    def burn_ass_subtitle_to_video(self, video_path, ass_path, output_file):
        try:

            def _escape_filter_path(p: str) -> str:
                """
                给 FFmpeg filter 使用的路径转义：
                - Windows drive letter 的 ':' 需要写成 '\\:'
                - 反斜线改成 '/'
                - 逗号 ',' 在 filter 参数中也建议转义（避免被当成分隔符）
                """
                p = str(p).replace("\\", "/")
                p = p.replace(":", "\\:")
                p = p.replace(",", "\\,")
                return p

            temp_dir = tempfile.mkdtemp()
            try:
                temp_video = os.path.join(temp_dir, "video_input" + Path(video_path).suffix)
                temp_ass = os.path.join(temp_dir, "subtitle.ass")
                temp_output = os.path.join(temp_dir, "output" + Path(output_file).suffix)

                shutil.copy2(video_path, temp_video)
                shutil.copy2(ass_path, temp_ass)

                # 如果有提供 fonts 資料夾，就讓 FFmpeg/libass 優先從此資料夾載入字型。
                # 做法：把字型複製進 temp_dir，fontsdir 用相對路徑「.」，
                # 完全避免 Windows 路徑冒號/反斜線在 libass filter string 中的轉義問題。
                fonts_dir = getattr(self, "app_dir", None)
                fonts_dir = (Path(fonts_dir) / "fonts") if fonts_dir else None
                fonts_dir_ok = False
                if fonts_dir and fonts_dir.exists():
                    try:
                        font_files = (list(fonts_dir.glob("*.ttf")) +
                                      list(fonts_dir.glob("*.otf")) +
                                      list(fonts_dir.glob("*.ttc")))
                        if font_files:
                            for ff in font_files:
                                shutil.copy2(str(ff), os.path.join(temp_dir, ff.name))
                            fonts_dir_ok = True
                    except Exception:
                        fonts_dir_ok = False

                vf = "subtitles=subtitle.ass"
                if fonts_dir_ok:
                    vf = "subtitles=subtitle.ass:fontsdir=."
                # 有些容器的影片時間軸不是從 0 開始（start_time > 0），
                # 燒錄字幕時會看起來整體延遲；先用 setpts 將 PTS 歸零可避免此問題。
                # 另外，「合併字幕與影片」頁面可勾選強制輸出 1080p（等比＋補黑邊），
                # 會在燒錄前先把畫面縮放到 1920x1080，字幕再疊上去。
                vf_chain = "setpts=PTS-STARTPTS"
                if self.force_1080p_var.get():
                    scale_filter = self._get_1080p_scale_pad_filter()
                    if scale_filter:
                        vf_chain += f",{scale_filter}"
                vf = vf_chain + "," + vf

                cmd = [
                    str(self.ffmpeg_exe),
                    "-y",
                    "-i", temp_video,
                    "-vf", vf,
                    "-map", "0:v:0",
                    "-map", "0:a?",
                    "-c:v", "libx264",
                    "-c:a", "copy",
                    temp_output
                ]

                self.log("  > 正在执行 FFmpeg 烧录...")
                subprocess.run(cmd, check=True, creationflags=self.subp_flags, cwd=temp_dir)

                shutil.move(temp_output, output_file)
                return True
            finally:
                with suppress(Exception): shutil.rmtree(temp_dir)
        except Exception as e:
            self._log_exception("  ❌ 烧录字幕失败: ", e)
            return False

    def recognize_lyrics_and_generate_srt(self, audio_file, output_dir, output_stem=None, override_language=None):
        """使用 Whisper 模型识别歌词并生成 SRT 字幕档案"""
        self.update_status("正在进行 AI 歌词识别...", "orange")
        self.log("\n🎤 开始 AI 歌词识别...")

        if output_stem is None:
            output_stem = Path(audio_file).stem

        try:
            device = self._resolve_device()
            runtime_ready, actual_device, runtime_lib_dir = self._ensure_runtime_stack_ready(device)
            if not runtime_ready:
                self.log("⚠️ AI 环境就绪失败，跳过歌词识别。")
                return None

            env = self._build_python_env(runtime_lib_dir, include_gpu_runtime=(actual_device == "cuda"))

            runtime_lib_dir_posix     = self._p(runtime_lib_dir)
            common_lib_dir_posix      = self._p(self.common_lib_dir)
            app_bin_dir_posix         = self._p(self.bin_dir)
            app_py_dir_posix          = self._p(self.py_dir)
            whisper_models_dir_posix  = self._p(self.whisper_models_dir)

            if hasattr(self, 'yt_whisper_language_var') and override_language is None:
                raw_lang = self.yt_whisper_language_var.get()
            elif hasattr(self, 'rec_language_var'):
                raw_lang = self.rec_language_var.get()
            else:
                raw_lang = "auto"

            whisper_lang = (raw_lang.split()[0].strip().lower() if raw_lang else "auto")
            if whisper_lang in ("", "auto", "detect", "none"):
                whisper_lang = "auto"

            if hasattr(self, 'yt_whisper_model_var') and override_language is None:
                whisper_model_size = self.yt_whisper_model_var.get()
            elif hasattr(self, 'whisper_model_var'):
                whisper_model_size = self.whisper_model_var.get()
            else:
                whisper_model_size = "medium"

            lang_display = "auto(自动侦测)" if whisper_lang == "auto" else whisper_lang
            self.log(f"  > 语言: {lang_display}，模型: {whisper_model_size}")

            temp_dir = tempfile.gettempdir()
            temp_audio_path = os.path.join(temp_dir, "temp_whisper_audio.mp3")
            shutil.copy2(audio_file, temp_audio_path)
            self.log(f"  > 已建立暂存档案: {temp_audio_path}")

            temp_audio_posix = self._p(temp_audio_path)
            output_dir_posix = self._p(output_dir)

            _song_name = MyKTVApp.sanitize_filename(output_stem, max_len=80)

            wz = self.config.get("whisper_zh", {})
            we = self.config.get("whisper_en", {})
            _wz_nst   = wz.get("no_speech_threshold", 0.6)
            _wz_crt   = wz.get("compression_ratio_threshold", 1.8)
            _wz_temp  = repr(tuple(wz.get("temperature", [0.0, 0.2, 0.4])))
            _wz_beam  = wz.get("beam_size", 5)
            _wz_nsp   = wz.get("nsp_skip", 0.85)
            _wz_lp    = wz.get("logprob_skip", -1.5)
            _we_nst   = we.get("no_speech_threshold", 0.55)
            _we_crt   = we.get("compression_ratio_threshold", 1.35)
            _we_temp  = repr(tuple(we.get("temperature", [0.0, 0.2])))
            _we_beam  = we.get("beam_size", 5)
            _we_nsp   = we.get("nsp_skip", 0.35)
            _we_lp    = we.get("logprob_skip", -0.7)
            _use_stable_ts = str(self.config.get("use_stable_ts", True))  # 傳入 script 的字串 "True"/"False"

            script = """
import sys, os
import json

target_lib = r'YTMKV_PH_0'
common_lib = r'YTMKV_PH_1'
app_bin_dir = r'YTMKV_PH_2'
app_py_dir = r'YTMKV_PH_3'
whisper_models_dir = r'YTMKV_PH_4'

if common_lib not in sys.path:
    sys.path.insert(0, common_lib)
if target_lib not in sys.path:
    sys.path.insert(0, target_lib)

numpy_libs = os.path.join(common_lib, 'numpy.libs')
numpy_core = os.path.join(common_lib, 'numpy', 'core')
os.environ["PATH"] = numpy_libs + os.pathsep + numpy_core + os.pathsep + app_bin_dir + os.pathsep + app_py_dir + os.pathsep + os.environ['PATH']

if hasattr(os, 'add_dll_directory'):
    dll_dirs = [app_bin_dir, app_py_dir, common_lib, target_lib]
    ort_pkg = os.path.join(target_lib, 'onnxruntime')
    ort_capi = os.path.join(ort_pkg, 'capi')
    for p in [ort_pkg, ort_capi, numpy_libs, numpy_core]:
        if os.path.isdir(p):
            dll_dirs.append(p)
    seen = set()
    for p in dll_dirs:
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            os.add_dll_directory(p)
        except Exception:
            pass

try:
    import whisper
except ImportError:
    print("[ERROR] Whisper not found, installing...")
    import subprocess
    subprocess.run([r'YTMKV_PH_5', '-m', 'pip', 'install',
                    '--target', common_lib, 'openai-whisper', 'ffmpeg-python'],
                  capture_output=True)
    import whisper

# stable-ts 用于强制对齐，提升逐字时间轴精度；load_audio 等工具仍用原生 whisper
_USE_STABLE_TS_SETTING = YTMKV_PH_6  # 由设定签页的勾选写入 config.json
if _USE_STABLE_TS_SETTING:
    try:
        import stable_whisper as _stable_whisper
        _STABLE_TS = True
        print("[INFO] stable-ts loaded — word-level alignment enabled")
    except ImportError as _e:
        _stable_whisper = None
        _STABLE_TS = False
        print(f"[INFO] stable-ts not found ({{_e}}), using openai-whisper (lower timing accuracy)")
    except Exception as _e:
        _stable_whisper = None
        _STABLE_TS = False
        print(f"[WARN] stable-ts import failed ({{type(_e).__name__}}: {{_e}}), using openai-whisper (lower timing accuracy)")
else:
    _stable_whisper = None
    _STABLE_TS = False
    print("[INFO] stable-ts disabled by user setting — using openai-whisper")

os.environ['WHISPER_MODELS_DIR'] = whisper_models_dir

audio_file = r'YTMKV_PH_7'
output_dir = r'YTMKV_PH_8'
original_stem = r'YTMKV_PH_9'
_whisper_lang_raw = r'YTMKV_PH_10'.strip().lower()
# whisper_lang=None => Whisper 会自动侦测语言；这能保留混合语言歌曲的原文字（例如中文+韩文）
whisper_lang = None if _whisper_lang_raw in ("", "auto", "detect", "none") else _whisper_lang_raw
model_size = r'YTMKV_PH_11'
song_name = r'YTMKV_PH_12'

# Whisper 参数（由设定签页写入 config.json，于此注入）
_CFG_ZH = dict(
    no_speech_threshold=YTMKV_PH_13,
    compression_ratio_threshold=YTMKV_PH_14,
    temperature=YTMKV_PH_15,
    beam_size=YTMKV_PH_16,
    nsp_skip=YTMKV_PH_17,
    logprob_skip=YTMKV_PH_18,
    nsp_skip_score_threshold=3,
    nsp_score_85=3, nsp_score_70=2, nsp_score_55=1,
    logprob_score_neg15=2, logprob_score_neg10=1,
    short_text_chars=2, short_text_score=1,
)
_CFG_EN = dict(
    no_speech_threshold=YTMKV_PH_19,
    compression_ratio_threshold=YTMKV_PH_20,
    temperature=YTMKV_PH_21,
    beam_size=YTMKV_PH_22,
    nsp_skip=YTMKV_PH_23,
    logprob_skip=YTMKV_PH_24,
)

# 载入模型（stable-ts 可用时用它的 load_model，让 transcribe 自动做强制对齐）
print(f"[INFO] Loading Whisper model: {{model_size}}...")
if _STABLE_TS:
    model = _stable_whisper.load_model(model_size, download_root=whisper_models_dir)
else:
    model = whisper.load_model(model_size, download_root=whisper_models_dir)

print("[INFO] Transcribing audio (silence-based segmentation)...")
import numpy as np

# load audio（load_audio / pad_or_trim / SAMPLE_RATE / CHUNK_LENGTH 皆属原生 whisper）
audio = whisper.load_audio(audio_file)
audio = whisper.pad_or_trim(audio, length=len(audio))

RATE      = whisper.audio.SAMPLE_RATE   # 16000
MAX_CHUNK = whisper.audio.CHUNK_LENGTH  # 30s max

# ──────────────────────────────────────────────
# 音讯能量对齐校正（方案 C）
# 对每个 word 的 start/end 用 RMS 能量找更精准的边界
# ──────────────────────────────────────────────
def refine_words_by_energy(words, audio, rate,
                            search_before=0.25,
                            search_after=0.10,
                            frame_ms=10):
    # When stable-ts is active it already did sub-frame forced alignment,
    # so RMS onset search would only degrade the result — skip it.
    if _STABLE_TS:
        return words

    # Refine each word's start time using RMS energy onset detection.
    # Searches [start - search_before, start + search_after] for the
    # steepest energy rise and uses that as the corrected start.
    # words: list of dicts with 'start','end','word'/'text' keys
    # audio: full numpy array (global time, offset already added)
    # rate:  sample rate (16000)
    if not words:
        return words

    frame_size = max(1, int(rate * frame_ms / 1000))  # 10ms frame
    audio_len  = len(audio)
    refined    = []

    for w in words:
        orig_start = w.get('start', 0)
        orig_end   = w.get('end',   orig_start + 0.1)

        # 搜寻范围（秒 → 样本）
        lo_sec = max(0.0, orig_start - search_before)
        hi_sec = min(audio_len / rate, orig_start + search_after)
        lo = int(lo_sec * rate)
        hi = int(hi_sec * rate)

        best_start = orig_start
        best_rise  = -1.0

        # 每 frame 计算 RMS，找能量上升最大点
        prev_rms = None
        i = lo
        while i + frame_size <= hi:
            seg = audio[i: i + frame_size]
            rms = float(np.sqrt(np.mean(seg ** 2)))
            if prev_rms is not None:
                rise = rms - prev_rms
                if rise > best_rise:
                    best_rise  = rise
                    best_start = i / rate
            prev_rms = rms
            i += frame_size

        # 只有找到明显上升（rise > 静音底噪）才采用修正值
        NOISE_FLOOR = 0.002
        if best_rise < NOISE_FLOOR:
            best_start = orig_start  # 无明显上升，保留原值

        new_w = dict(w)
        new_w['start'] = best_start
        # end 暂时保留原值，下面再连锁修正
        new_w['end']   = orig_end
        refined.append(new_w)

    # 让 word[i].end = word[i+1].start，保持连续不重叠
    for i in range(len(refined) - 1):
        next_start = refined[i + 1]['start']
        cur_end    = refined[i]['end']
        # 只往前修，不往后拉（避免把 end 拉超过原来的 end）
        if next_start < cur_end:
            refined[i]['end'] = next_start
        # 确保 end > start
        if refined[i]['end'] <= refined[i]['start']:
            refined[i]['end'] = refined[i]['start'] + 0.05

    # 最后一个 word：end 保留原值但确保 > start
    if refined and refined[-1]['end'] <= refined[-1]['start']:
        refined[-1]['end'] = refined[-1]['start'] + 0.05

    return refined

def find_silence_boundaries(audio, rate,
                             silence_db=-38,
                             min_silence=0.4,
                             min_phrase=1.2,
                             max_phrase=25.0):
    # Split audio into phrase segments for Whisper transcription.
    # Key fixes to avoid cutting mid-lyric:
    # 1. Spike tolerance: up to 2 non-silent frames allowed inside a silence
    #    run before resetting, so brief resonance does not break real pauses.
    # 2. Force-cut uses 3-stage search: retro -> extend -> fallback minimum,
    #    rather than blindly cutting at the second-half energy minimum.
    # 3. Cut point is always the lowest-RMS frame inside the silence region.
    frame = int(rate * 0.02)          # 20ms per frame
    threshold = 10 ** (silence_db / 20)
    n_frames = len(audio) // frame

    rms = np.array([
        np.sqrt(np.mean(audio[i*frame:(i+1)*frame] ** 2))
        for i in range(n_frames)
    ])
    # 平滑：7 帧（140ms），比原本 5 帧更能吸收短暂残响
    smooth_len = 7
    rms_smooth = np.convolve(rms, np.ones(smooth_len) / smooth_len, mode='same')
    is_silence = rms_smooth < threshold

    min_sil_frames = int(min_silence / 0.02)
    min_phr_frames = int(min_phrase  / 0.02)
    max_phr_frames = int(max_phrase  / 0.02)

    # 允许静音区中最多连续几帧的短暂突波不中断静音计数
    SPIKE_TOLERANCE = 2

    def find_silence_cut(start_frame, end_frame):
        '''
        在 [start_frame, end_frame-1] 内找最佳静音切点。
        回传切点 frame index，找不到回传 None。
        使用突波容忍：连续静音中允许最多 SPIKE_TOLERANCE 帧的非静音突波。
        '''
        best_cut = None
        best_rms  = float('inf')
        sil_run   = 0   # 已连续侦测到的静音帧数（含容忍突波）
        spike_cnt = 0   # 当前突波连续帧数

        for f in range(start_frame, end_frame):
            if is_silence[f]:
                sil_run  += 1
                spike_cnt = 0
            else:
                if spike_cnt < SPIKE_TOLERANCE and sil_run > 0:
                    # 容忍短暂突波，不重置静音计数
                    spike_cnt += 1
                    sil_run   += 1
                else:
                    sil_run   = 0
                    spike_cnt = 0

            if sil_run >= min_sil_frames:
                # 这帧在有效静音区内，若能量更低则记录为候选切点
                if rms_smooth[f] < best_rms:
                    best_rms  = rms_smooth[f]
                    best_cut  = f

        return best_cut

    boundaries = [0]
    i = 0
    # 回溯/延伸搜寻的最大帧数（3 秒）
    SEARCH_FRAMES = int(3.0 / 0.02)

    while i < n_frames:
        phrase_end = i + max_phr_frames

        if phrase_end >= n_frames:
            # 剩余音讯不足一个 max_phrase，直接收尾
            break

        # 依序尝试三个搜寻范围，找到静音就切
        for s, e in [
            (i + min_phr_frames,                                   min(n_frames, phrase_end)),
            (max(i + min_phr_frames, phrase_end - SEARCH_FRAMES),  phrase_end),
            (phrase_end,                                            min(n_frames, phrase_end + SEARCH_FRAMES)),
        ]:
            if (cut := find_silence_cut(s, e)) is not None:
                boundaries.append(cut); i = cut; break
        else:
            # fallback：切在延伸范围能量最低点
            region = rms_smooth[phrase_end:min(n_frames, phrase_end + SEARCH_FRAMES)]
            cut = phrase_end + int(np.argmin(region)) if len(region) > 0 else phrase_end
            boundaries.append(cut); i = cut

    boundaries.append(n_frames)

    return [(boundaries[k] * frame, boundaries[k+1] * frame)
            for k in range(len(boundaries) - 1)
            if (boundaries[k+1] - boundaries[k]) >= min_phr_frames]

print("[INFO] Detecting silence boundaries...")
# ── 英文用稍宽松的切割（英文句子比中文长，让 Whisper 有更多上下文）
if whisper_lang == 'en':
    phrases = find_silence_boundaries(audio, RATE,
                                      silence_db=-40,
                                      min_silence=0.35,
                                      min_phrase=1.5,
                                      max_phrase=28.0)
else:
    phrases = find_silence_boundaries(audio, RATE)
print(f"[INFO] Found {{len(phrases)}} phrase segments")

all_segments = []
seg_id = 0
prev_text = ""

# ======================================================================
#  English transcription (semantic line-break + hallucination filter)
#  NOTE: Chinese path (transcribe_chinese) is completely untouched.
# ======================================================================
def transcribe_english(model, audio, phrases, RATE, MAX_CHUNK):
    global seg_id, prev_text, all_segments

    EN_HALLUCINATION_PATTERNS = [
        "music by", "lyrics by", "produced by", "written by",
        "copyright", "all rights reserved", "licensed",
        "thank you for watching", "subscribe", "like and share",
        "subtitles by", "transcribed by", "captions by",
        "www.", ".com", "http",
        # Whisper sometimes outputs the initial_prompt literally during silence
        "song lyrics", "song lyric",
    ]

    def _is_prompt_echo(text):
        # Detect when Whisper echoes the initial_prompt verbatim (happens during silence/music)
        t = text.strip().lower()
        # Very short segments with no real words are often prompt echoes
        if len(t) <= 15 and not any(c.isalpha() for c in t):
            return True
        return False

    # Cross-chunk dedup memory (prevent same line appearing in multiple chunks)
    recent_global_texts = []
    MAX_GLOBAL_RECENT = 6

    def _is_repeated_char_hallucination(text, min_len=8, threshold=0.85):
        # Detect repeated single char hallucination e.g. Hmmmmm... or aaaaaaa
        if len(text) < min_len:
            return False
        clean = text.strip().lower().replace(' ', '')
        if not clean:
            return False
        most_common_ratio = max(clean.count(c) for c in set(clean)) / len(clean)
        return most_common_ratio >= threshold

    def _is_cross_chunk_repetition(text, recent_texts, max_repeats=2):
        # Detect cross-chunk repetition hallucination
        t = text.lower().strip()
        count = sum(1 for rt in recent_texts[-max_repeats:] if rt.lower().strip() == t)
        return count >= max_repeats

    EN_SOFT_WORDS = 6
    EN_MAX_WORDS  = 9
    HARD_BREAK_AFTER  = {',', '.', '!', '?', ';', ':', '...', '-'}
    SOFT_BREAK_BEFORE = {
        'and','but','or','so','yet','nor','for',
        'because','although','though','while','never','till','until',
        'when','where','that','which','who',
        'i','you','we','they','he','she','it',
    }

    def _en_split(words):
        # Split word list into lines at semantic boundaries.
        if len(words) <= EN_SOFT_WORDS:
            return [words]
        groups, cur = [], []
        for wi, w in enumerate(words):
            cur.append(w)
            wtext     = w.get('word', w.get('text', '')).strip()
            next_word = (words[wi+1].get('word', words[wi+1].get('text', '')).strip().lower()
                        if wi + 1 < len(words) else '')
            is_hard = any(wtext.endswith(p) for p in HARD_BREAK_AFTER)
            is_soft = next_word in SOFT_BREAK_BEFORE
            if len(cur) >= EN_MAX_WORDS or (len(cur) >= EN_SOFT_WORDS and (is_hard or is_soft)):
                groups.append(cur); cur = []
        if cur:
            if groups and len(cur) <= 2:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        return groups

    def _is_repetition_hallucination(text, prev_texts, max_repeats=2):
        # Detect repetition hallucination: same text repeated >= max_repeats times.
        t = text.lower().strip()
        if not prev_texts:
            return False
        count = sum(1 for pt in prev_texts[-max_repeats:] if pt.lower().strip() == t)
        return count >= max_repeats

    def _is_zero_duration(seg, min_dur=0.05):
        # Skip near-zero-duration segments (start ~ end), usually hallucinations.
        return (seg["end"] - seg["start"]) < min_dur

    def _split_on_sentence_boundary(words):
        # Split at sentence boundaries: punctuation + next word capitalized.
        # Fixes Whisper gluing two sentences into one segment.
        import re
        result, cur = [], []
        for wi, w in enumerate(words):
            cur.append(w)
            if wi + 1 >= len(words):
                break
            wtext      = w.get('word', w.get('text', '')).strip()
            next_wtext = words[wi+1].get('word', words[wi+1].get('text', '')).strip()
            if re.search(r'[.!?]$', wtext) and next_wtext and next_wtext[0].isupper():
                result.append(cur); cur = []
        if cur:
            result.append(cur)
        return result if len(result) > 1 else [words]

    for phrase_idx, (phrase_start, phrase_end) in enumerate(phrases):
        chunk_audio = audio[phrase_start:phrase_end]
        if len(chunk_audio) < RATE // 2:
            continue

        offset_sec = phrase_start / RATE

        if len(chunk_audio) > MAX_CHUNK * RATE:
            chunk_audio = chunk_audio[:MAX_CHUNK * RATE]

        # initial_prompt 只做风格提示，不能用容易被 Whisper 当成辨识结果输出的字串
        # 用纯音乐符号，Whisper 几乎不会把它当成语音输出
        prompt = "♪ ♫"

        try:
            chunk_result_raw = model.transcribe(
                chunk_audio,
                language='en',
                word_timestamps=True,
                condition_on_previous_text=False,
                no_speech_threshold=_CFG_EN['no_speech_threshold'],
                compression_ratio_threshold=_CFG_EN['compression_ratio_threshold'],
                temperature=_CFG_EN['temperature'],
                beam_size=_CFG_EN['beam_size'],
                suppress_blank=True,
                suppress_tokens="-1",
                fp16=False,
                initial_prompt=prompt,
            )
            # stable-ts 回传 WhisperResult 物件；转成 dict 让后续过滤逻辑不用改
            if _STABLE_TS and hasattr(chunk_result_raw, 'to_dict'):
                chunk_result = chunk_result_raw.to_dict()
            else:
                chunk_result = chunk_result_raw

            chunk_texts = []
            for seg in chunk_result.get("segments", []):
                text = seg["text"].strip()
                if not text:
                    continue

                seg_time_str_en = "{:02d}:{:02d}".format(int((seg['start']+offset_sec)//60), int((seg['start']+offset_sec)%60))

                if _is_zero_duration(seg):
                    print(f"[SKIP] [{{seg_time_str_en}}] zero-duration segment, skip: {{text}}")
                    continue

                no_speech_prob = seg.get("no_speech_prob", 0)
                # 提高阈值：间奏/纯音乐段落的 no_speech_prob 通常偏高，积极过滤
                if no_speech_prob > _CFG_EN['nsp_skip']:
                    print(f"[SKIP] [{{seg_time_str_en}}] no_speech_prob={{no_speech_prob:.2f}} too high, skip: {{text}}")
                    continue

                avg_logprob = seg.get("avg_logprob", 0)
                # 提高阈值：纯音乐的 avg_logprob 通常很低（辨识信心差）
                if avg_logprob < _CFG_EN['logprob_skip']:
                    print(f"[SKIP] [{{seg_time_str_en}}] avg_logprob={{avg_logprob:.2f}} too low, skip: {{text}}")
                    continue

                text_lower = text.lower()
                if any(pat in text_lower for pat in EN_HALLUCINATION_PATTERNS):
                    print(f"[SKIP] [{{seg_time_str_en}}] hallucination pattern, skip: {{text}}")
                    continue

                if _is_prompt_echo(text):
                    print(f"[SKIP] [{{seg_time_str_en}}] prompt echo, skip: {{text}}")
                    continue

                if _is_repetition_hallucination(text, chunk_texts):
                    print(f"[SKIP] [{{seg_time_str_en}}] repetition hallucination, skip: {{text}}")
                    continue

                # 侦测单一字元大量重复（Hmmmm... / aaaa...）
                if _is_repeated_char_hallucination(text):
                    print(f"[SKIP] [{{seg_time_str_en}}] repeated-char hallucination, skip: {{text[:40]}}")
                    continue

                # 侦测跨 chunk 的重复（同一句出现在不同 chunk）
                if _is_cross_chunk_repetition(text, recent_global_texts):
                    print(f"[SKIP] [{{seg_time_str_en}}] cross-chunk repetition, skip: {{text}}")
                    continue

                seg_words = [
                    {**w, "start": w["start"] + offset_sec,
                           "end":   w["end"]   + offset_sec}
                    for w in seg.get("words", [])
                ]

                # ── 音讯能量校正：修正每个 word 的 start 时间
                if seg_words:
                    seg_words = refine_words_by_energy(seg_words, audio, RATE)

                sentence_groups = _split_on_sentence_boundary(seg_words) if seg_words else [seg_words]
                for sent_words in sentence_groups:
                    if not sent_words:
                        continue
                    for grp in _en_split(sent_words):
                        grp_text = " ".join(
                            w.get('word', w.get('text', '')).strip() for w in grp
                        ).strip()
                        if not grp_text:
                            continue
                        # 过滤零时长或时间倒退的 segment
                        grp_start = grp[0]["start"]
                        grp_end   = grp[-1]["end"]
                        if grp_end <= grp_start:
                            print(f"[SKIP] zero/negative duration grp, skip: {{grp_text}}")
                            continue
                        seg_id += 1
                        all_segments.append({
                            "id":    seg_id,
                            "start": grp_start,
                            "end":   grp_end,
                            "text":  grp_text,
                            "words": grp,
                        })
                chunk_texts.append(text)
                recent_global_texts.append(text)
                if len(recent_global_texts) > MAX_GLOBAL_RECENT:
                    recent_global_texts.pop(0)

            if chunk_texts:
                prev_text = chunk_texts[-1]

        except Exception as e:
            print(f"[WARN] phrase at {{offset_sec:.1f}}s failed: {{e}}")

# ══════════════════════════════════════════════════════════════
#  中文辨识逻辑（保留原有逻辑，完全不动）
# ══════════════════════════════════════════════════════════════
def transcribe_chinese(model, audio, phrases, RATE, MAX_CHUNK, whisper_lang):
    global seg_id, prev_text, all_segments

    HALLUCINATION_PATTERNS = [
        "词曲", "作词", "作曲", "编曲", "监制", "出品", "版权所有",
        "制作人", "发行", "唱片", "music by", "lyrics by",
        "produced by", "written by",
        # initial_prompt 回声
        "一首中文歌曲", "以下是", "歌词如下", "以下歌词",
        # YouTube 平台推广字串
        "点赞", "订阅", "转发", "打赏", "明镜", "字幕组", "字幕 by", "字幕by",
    ]

    recent_global_texts_zh = []
    MAX_GLOBAL_RECENT_ZH = 8

    def _is_cross_chunk_repetition_zh(text, recent_texts, max_repeats=2):
        t = text.lower().strip()
        count = sum(1 for rt in recent_texts[-max_repeats:] if rt.lower().strip() == t)
        return count >= max_repeats

    def _fmt_time(seconds):
        # 格式化秒数为 mm:ss 供 SKIP log 显示
        m = int(seconds // 60)
        s = int(seconds % 60)
        return "{:02d}:{:02d}".format(m, s)

    def _is_hallucination_by_composite(text, no_speech_prob, avg_logprob):
        # Composite scoring - replaces single threshold.
        # Heavy-instrumented songs have high no_speech_prob even on real vocals.
        # Only skip when multiple indicators are bad simultaneously.
        #
        # Scoring (higher = more likely hallucination):
        #   no_speech_prob >= 0.85  -> +3 (triggers skip alone)
        #   no_speech_prob >= 0.70  -> +2
        #   no_speech_prob >= 0.55  -> +1
        #   avg_logprob    < -1.5   -> +2
        #   avg_logprob    < -1.0   -> +1
        #   text length <= 2 chars  -> +1
        # Skip threshold: score >= 3
        score = 0
        if no_speech_prob >= 0.85:
            score += _CFG_ZH['nsp_score_85']
        elif no_speech_prob >= 0.70:
            score += _CFG_ZH['nsp_score_70']
        elif no_speech_prob >= 0.55:
            score += _CFG_ZH['nsp_score_55']

        if avg_logprob < -1.5:
            score += _CFG_ZH['logprob_score_neg15']
        elif avg_logprob < -1.0:
            score += _CFG_ZH['logprob_score_neg10']

        char_count = len(text.replace(' ', ''))
        if char_count <= _CFG_ZH['short_text_chars']:
            score += _CFG_ZH['short_text_score']

        return score >= _CFG_ZH['nsp_skip_score_threshold'], score

    for phrase_idx, (phrase_start, phrase_end) in enumerate(phrases):
        chunk_audio = audio[phrase_start:phrase_end]
        if len(chunk_audio) < RATE // 2:
            continue

        offset_sec = phrase_start / RATE

        if len(chunk_audio) > MAX_CHUNK * RATE:
            chunk_audio = chunk_audio[:MAX_CHUNK * RATE]

        if prev_text:
            prompt = prev_text
        else:
            prompt = "♪ ♫"

        try:
            chunk_result_raw = model.transcribe(
                chunk_audio,
                language=whisper_lang,
                word_timestamps=True,
                condition_on_previous_text=False,
                no_speech_threshold=_CFG_ZH['no_speech_threshold'],
                compression_ratio_threshold=_CFG_ZH['compression_ratio_threshold'],
                temperature=_CFG_ZH['temperature'],
                beam_size=_CFG_ZH['beam_size'],
                suppress_blank=True,
                suppress_tokens="-1",
                fp16=False,
                initial_prompt=prompt,
            )
            # stable-ts 回传 WhisperResult 物件；转成 dict 让后续过滤逻辑不用改
            if _STABLE_TS and hasattr(chunk_result_raw, 'to_dict'):
                chunk_result = chunk_result_raw.to_dict()
            else:
                chunk_result = chunk_result_raw

            chunk_texts = []
            for seg in chunk_result.get("segments", []):
                text = seg["text"].strip()
                if not text:
                    continue

                no_speech_prob = seg.get("no_speech_prob", 0)
                avg_logprob    = seg.get("avg_logprob", 0)
                seg_time_str   = _fmt_time(seg["start"] + offset_sec)

                # ── 直接门槛过滤（与英文对称）
                if no_speech_prob > _CFG_ZH['nsp_skip']:
                    print(f"[SKIP] [{{seg_time_str}}] no_speech_prob={{no_speech_prob:.2f}} 超过门槛，跳过: {{text}}")
                    continue
                if avg_logprob < _CFG_ZH['logprob_skip']:
                    print(f"[SKIP] [{{seg_time_str}}] avg_logprob={{avg_logprob:.2f}} 低于门槛，跳过: {{text}}")
                    continue

                # ── 硬性黑名单过滤（优先于复合评分）
                text_lower = text.lower()
                if any(pat in text_lower for pat in HALLUCINATION_PATTERNS):
                    print(f"[SKIP] [{{seg_time_str}}] 黑名单幻觉，跳过: {{text}}")
                    continue

                # ── 跨 chunk 重复
                if _is_cross_chunk_repetition_zh(text, recent_global_texts_zh):
                    print(f"[SKIP] [{{seg_time_str}}] 跨chunk重复幻觉，跳过: {{text}}")
                    continue

                # ── 复合评分（取代旧版单一阈值）
                is_hallucination, hal_score = _is_hallucination_by_composite(
                    text, no_speech_prob, avg_logprob)
                if is_hallucination:
                    print(f"[SKIP] [{{seg_time_str}}] 复合评分={{hal_score}} "
                          f"(nsp={{no_speech_prob:.2f}} lp={{avg_logprob:.2f}})，跳过: {{text}}")
                    continue

                seg_words = [
                    {**w, "start": w["start"] + offset_sec,
                           "end":   w["end"]   + offset_sec}
                    for w in seg.get("words", [])
                ]

                # ── 音讯能量校正：修正每个 word 的 start 时间
                if seg_words:
                    seg_words = refine_words_by_energy(seg_words, audio, RATE)

                text_norm = text.replace('\\n', ' ')
                sub_lines = [l.strip() for l in text_norm.split(' ') if l.strip()]

                if len(sub_lines) <= 1 or not seg_words:
                    seg_id += 1
                    all_segments.append({
                        "id":    seg_id,
                        "start": seg["start"] + offset_sec,
                        "end":   seg["end"]   + offset_sec,
                        "text":  text,
                        "words": seg_words,
                    })
                    chunk_texts.append(text)
                else:
                    def _group_words(sub_lines, words):
                        groups = [[] for _ in sub_lines]
                        w_idx = 0
                        for li, line in enumerate(sub_lines):
                            accumulated = ''
                            while w_idx < len(words):
                                wt = words[w_idx].get('word', words[w_idx].get('text', '')).strip()
                                if not wt:
                                    w_idx += 1
                                    continue
                                candidate = (accumulated + wt).replace(' ', '')
                                target = line.replace(' ', '')
                                if target.startswith(candidate):
                                    groups[li].append(words[w_idx])
                                    accumulated += wt
                                    w_idx += 1
                                    if accumulated.replace(' ', '') == target:
                                        break
                                else:
                                    break
                        while w_idx < len(words):
                            groups[-1].append(words[w_idx])
                            w_idx += 1
                        return groups

                    word_groups = _group_words(sub_lines, seg_words)
                    seg_dur = (seg["end"] - seg["start"]) / len(sub_lines)

                    for li, line in enumerate(sub_lines):
                        line_words = word_groups[li]
                        if line_words:
                            line_start = line_words[0].get('start', seg["start"] + offset_sec + li * seg_dur)
                            line_end   = line_words[-1].get('end',   seg["start"] + offset_sec + (li + 1) * seg_dur)
                        else:
                            line_start = seg["start"] + offset_sec + li * seg_dur
                            line_end   = seg["start"] + offset_sec + (li + 1) * seg_dur
                        seg_id += 1
                        all_segments.append({
                            "id":    seg_id,
                            "start": line_start,
                            "end":   line_end,
                            "text":  line,
                            "words": line_words,
                        })
                    chunk_texts.append(text)

            if chunk_texts:
                prev_text = " ".join(chunk_texts[-2:])
                # ── Bug 修正：更新跨 chunk 重复侦测记忆
                for ct in chunk_texts:
                    recent_global_texts_zh.append(ct)
                if len(recent_global_texts_zh) > MAX_GLOBAL_RECENT_ZH:
                    recent_global_texts_zh = recent_global_texts_zh[-MAX_GLOBAL_RECENT_ZH:]

        except Exception as e:
            print(f"[WARN] phrase at {{offset_sec:.1f}}s failed: {{e}}")

# ══════════════════════════════════════════════════════════════
#  分流入口：依语言选择辨识路径
# ══════════════════════════════════════════════════════════════
if whisper_lang == 'en':
    print("[INFO] Using English recognition pipeline...")
    transcribe_english(model, audio, phrases, RATE, MAX_CHUNK)
else:
    print("[INFO] Using CJK recognition pipeline...")
    transcribe_chinese(model, audio, phrases, RATE, MAX_CHUNK, whisper_lang)

# ══════════════════════════════════════════════════════════════
#  短句合并后处理
#  字数 < MIN_CHARS 的 segment 往后合并，直到够长或没有下一句
#  合并条件：下一句与本句间隔 <= MAX_GAP_SEC（避免跨越明显停顿）
# ══════════════════════════════════════════════════════════════
MIN_CHARS   = 5      # 少于几个字（CJK 字元）才触发合并，可调 4～6
MAX_GAP_SEC = 1.5    # 两句间隔超过这个秒数就不合并，可调

def _char_count(text):
    # 只计 CJK 字元数；英文则计 word 数
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or
              '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7a3')
    if cjk > 0:
        return cjk
    return len(text.split())

def merge_short_segments(segs, min_chars, max_gap_sec):
    if not segs:
        return segs
    merged = []
    i = 0
    while i < len(segs):
        cur = dict(segs[i])
        cur['words'] = list(cur.get('words', []))
        # 只要目前句子够短，就尝试往后吸
        while _char_count(cur['text']) < min_chars and i + 1 < len(segs):
            nxt = segs[i + 1]
            gap = nxt['start'] - cur['end']
            if gap > max_gap_sec:
                break   # 间隔太大，不合并
            # 合并文字（中间加空格供英文，CJK 不影响）
            cur['text'] = cur['text'].rstrip() + ' ' + nxt['text'].lstrip()
            cur['end']  = nxt['end']
            cur['words'] = cur['words'] + list(nxt.get('words', []))
            i += 1   # 跳过被吸掉的那句
        merged.append(cur)
        i += 1
    # 重新编号
    for idx, s in enumerate(merged, 1):
        s['id'] = idx
    return merged

all_segments = merge_short_segments(all_segments, MIN_CHARS, MAX_GAP_SEC)
print(f"[INFO] After short-segment merge: {{len(all_segments)}} segments")

result = {"segments": all_segments, "language": whisper_lang}

# 保存结果
output_json = os.path.join(output_dir, original_stem + '.json')
with open(output_json, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

# 生成 SRT
srt_file = os.path.join(output_dir, original_stem + '.srt')

def format_timestamp(s):
    h,m,sc,ms = int(s//3600),int((s%3600)//60),int(s%60),int((s-int(s))*1000)
    return f"{h:02d}:{m:02d}:{sc:02d},{ms:03d}"

srt_index = 1
NL = chr(10)
with open(srt_file, 'w', encoding='utf-8-sig') as f:
    for segment in result['segments']:
        text = segment['text'].strip()
        if not text:
            continue
        f.write("{}".format(srt_index) + NL +
                "{} --> {}".format(format_timestamp(segment['start']), format_timestamp(segment['end'])) + NL +
                "{}".format(text) + NL + NL)
        srt_index += 1

print(f"[SUCCESS] {{srt_file}}")
print(f"[JSON] {{output_json}}")
"""

            self.log("  > 正在载入 Whisper 模型（首次使用会自动下载）...")
            start_time = time.time()

            # 把 script 寫成暫存 .py 檔再執行，避免 Windows CreateProcess

            # 將 Python 端變數注入 script 模板（相容 Python 3.11 以下及 3.12+）
            _script_vars = {
                'YTMKV_PH_0': runtime_lib_dir_posix,
                'YTMKV_PH_1': common_lib_dir_posix,
                'YTMKV_PH_2': app_bin_dir_posix,
                'YTMKV_PH_3': app_py_dir_posix,
                'YTMKV_PH_4': whisper_models_dir_posix,
                'YTMKV_PH_5': str(self.local_python),
                'YTMKV_PH_6': _use_stable_ts,
                'YTMKV_PH_7': temp_audio_posix,
                'YTMKV_PH_8': output_dir_posix,
                'YTMKV_PH_9': output_stem,
                'YTMKV_PH_10': whisper_lang,
                'YTMKV_PH_11': whisper_model_size,
                'YTMKV_PH_12': _song_name,
                'YTMKV_PH_13': str(_wz_nst),
                'YTMKV_PH_14': str(_wz_crt),
                'YTMKV_PH_15': str(_wz_temp),
                'YTMKV_PH_16': str(_wz_beam),
                'YTMKV_PH_17': str(_wz_nsp),
                'YTMKV_PH_18': str(_wz_lp),
                'YTMKV_PH_19': str(_we_nst),
                'YTMKV_PH_20': str(_we_crt),
                'YTMKV_PH_21': str(_we_temp),
                'YTMKV_PH_22': str(_we_beam),
                'YTMKV_PH_23': str(_we_nsp),
                'YTMKV_PH_24': str(_we_lp),
            }
            for _ph, _val in sorted(_script_vars.items(), key=lambda x: int(x[0].replace('YTMKV_PH_', '')), reverse=True):
                script = script.replace(_ph, str(_val))
            # 命令列長度上限（~32767 字元）造成 WinError 206
            temp_script_path = os.path.join(temp_dir, "temp_whisper_script.py")
            with open(temp_script_path, 'w', encoding='utf-8') as _sf:
                _sf.write(script)

            process = subprocess.Popen([str(self.local_python), temp_script_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                encoding='utf-8', errors='replace', creationflags=self.subp_flags, env=env)
            self._current_process = process

            srt_output_file = None
            json_output_file = None

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    with suppress(Exception): os.remove(temp_audio_path)
                    with suppress(Exception): os.remove(temp_script_path)
                    self.log("🛑 歌词识别已取消。")
                    return None

                line = line.strip()
                if line:
                    self.log(f"    {line}")
                    if line.startswith("[SUCCESS]"):
                        srt_output_file = line[len("[SUCCESS] "):].strip()
                    elif line.startswith("[JSON]"):
                        json_output_file = line[len("[JSON] "):].strip()
                    elif line.startswith("[INFO]"):
                        if "Loading Whisper model" in line:
                            self.update_progress(60, "正在识别歌词", step_text="步骤 4/5：载入 Whisper 模型中...")
                        elif "Transcribing audio" in line or "Detecting silence" in line:
                            self.update_progress(65, "正在识别歌词", step_text="步骤 4/5：侦测句子边界中...")
                        elif "Found" in line and "phrase segments" in line:
                            self.update_progress(68, "正在识别歌词", step_text=f"步骤 4/5：{line.split(chr(93)+chr(32))[-1].strip()}")
                        else:
                            m = re.search(r"phrase\s+(\d+)\s*/\s*(\d+)", line)
                            if m:
                                cur, total = int(m.group(1)), int(m.group(2))
                                pct = 70 + int(cur / total * 9)  # 映射到 70–79%
                                self.update_progress(pct, "正在识别歌词", step_text=f"步骤 4/5：AI 歌词辨识中 {cur}/{total}")

            process.wait()
            self._current_process = None

            with suppress(Exception): os.remove(temp_audio_path)
            self.log("  > 已清除暂存档案")
            with suppress(Exception): os.remove(temp_script_path)
            if process.returncode == 0 and srt_output_file:
                target_lang = override_language if override_language is not None else self.lyrics_language_var.get()
                if target_lang != "original" and json_output_file and os.path.exists(json_output_file):
                    try:
                        self.log(f"  > 正在转换歌词语言为: {'繁体中文' if target_lang == 'traditional' else '简体中文'}")

                        with open(json_output_file, 'r', encoding='utf-8') as f:
                            lyrics_data = json.load(f)

                        if 'segments' in lyrics_data:
                            for segment in lyrics_data['segments']:
                                if 'text' in segment:
                                    segment['text'] = self.convert_lyrics_language(segment['text'], target_lang)
                                if 'words' in segment:
                                    for word in segment['words']:
                                        if 'word' in word:
                                            word['word'] = self.convert_lyrics_language(word['word'], target_lang)

                        with open(json_output_file, 'w', encoding='utf-8') as f:
                            json.dump(lyrics_data, f, ensure_ascii=False, indent=2)

                        with open(srt_output_file, 'w', encoding='utf-8-sig') as f:
                            srt_idx = 1
                            for segment in lyrics_data['segments']:
                                text = segment['text'].replace('\n', ' ').strip()
                                if not text:
                                    continue
                                ts = self._format_srt_timestamp(segment['start'])
                                te = self._format_srt_timestamp(segment['end'])
                                f.write(f"{srt_idx}\n{ts} --> {te}\n{text}\n\n")
                                srt_idx += 1

                        self.log("  ✅ 歌词语言转换完成")
                    except Exception as e:
                        self._log_exception("  ⚠️ 歌词语言转换失败: ", e)

                elapsed_time = time.time() - start_time
                self.log(f"✅ 歌词识别完成！花费 {elapsed_time:.2f} 秒")
                self.log(f"   SRT 字幕档: {Path(srt_output_file).name}")
                return srt_output_file, json_output_file, None
            else:
                self.log("❌ 歌词识别失败。")
                return None

        except Exception as e:
            self._log_exception("❌ 歌词识别过程中出错", e)
            return None

if __name__ == "__main__":
    root = tk.Tk()
    app = MyKTVApp(root)
    root.mainloop()
