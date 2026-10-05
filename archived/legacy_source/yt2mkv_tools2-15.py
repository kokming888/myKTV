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

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and not any(arg.startswith('--multiprocessing') for arg in sys.argv):
        sys.exit(0)

class AudioSeparatorApp:
    def __init__(self, root):
        self.root = root
        self.version = "v2.15"
        self.root.title(f"MP3 人聲分離 & YouTube 下載 & KTV 字幕製作工具 {self.version}")
        self.root.geometry("1100x650")
        self.root.minsize(900, 560)  # 設定最小尺寸

        if getattr(sys, 'frozen', False):
            exe_path = Path(sys.executable)
            if 'Temp' in str(exe_path) or 'temp' in str(exe_path):
                import os as _os
                alt = _os.environ.get('_MEIPASS2', '') or _os.environ.get('PYINSTALLER_ORIG_EXEC', '')
                if alt:
                    self.app_dir = Path(alt).parent
                else:
                    self.app_dir = exe_path.parent
            else:
                self.app_dir = exe_path.parent
        else:
            self.app_dir = Path(__file__).parent

        migrations = {  # 自動遷移舊資料夾名稱
            "bin": "engine_ffmpeg",
            "python_env": "runtime_python",
            "packages": "ai_libraries_cpu",
            "packages_gpu": "ai_libraries_gpu",
            "models": "ai_models"
        }
        for old_name, new_name in migrations.items():
            old_p = self.app_dir / old_name
            new_p = self.app_dir / new_name
            if old_p.exists() and not new_p.exists():
                try:
                    old_p.rename(new_p)
                except Exception:
                    pass

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
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

        self.local_python = self.py_dir / "python.exe"

        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{self.py_dir}{os.pathsep}{os.environ['PATH']}"
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(str(self.bin_dir))
            except Exception:
                pass  # Windows 版本過舊或路徑無效時忽略

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

        self.config_file = self.app_dir / "config.json"
        self.load_config()

        self.setup_ui_style()
        self.setup_ui()

        def _startup_check():
            self.update_status("正在檢查環境...", "gray")
            self.check_components(prompt=False, show_list=False)
            self.update_status("準備就緒", "green")
        self.root.after(500, _startup_check)

    def _get_system_fonts(self):
        """讀取電腦上已安裝的字體清單，優先顯示本地化（中文）名稱"""
        fallback = ["Arial", "微軟正黑體", "新細明體", "標楷體", "DFKai-SB", "Microsoft JhengHei", "Microsoft YaHei"]

        def _get_localized_name_from_file(font_path):
            """從字體檔案內讀取本地化名稱（支援 TTF / OTF / TTC）
            nameID=4 Full name，優先取繁中 → 簡中 → 英文"""
            try:
                with open(font_path, 'rb') as f:
                    data = f.read()
                if len(data) < 12:
                    return None

                def _read_name_table(data, sfnt_offset):
                    """從指定的 sfnt offset 讀取 name table，回傳 (zh_tw, zh_cn, en_us)"""
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
                        if zh_tw and best_zh_tw is None:
                            best_zh_tw = zh_tw
                        if zh_cn and best_zh_cn is None:
                            best_zh_cn = zh_cn
                        if en_us and best_en is None:
                            best_en = en_us
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
            font_dirs = [
                r"C:\Windows\Fonts",
            ]
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
            result = set()
            for reg_name, fpath in name_to_file.items():
                if fpath:
                    loc = _get_localized_name_from_file(fpath)
                    result.add(loc if loc else reg_name)
                else:
                    result.add(reg_name)
            return result

        try:
            if os.name == "nt":
                fonts = _collect_windows_fonts()
                if fonts:
                    return sorted(fonts, key=lambda x: x.lower())
            else:
                import tkinter.font as tkfont
                fonts = sorted(set(tkfont.families()), key=lambda x: x.lower())
                if fonts:
                    return fonts
        except Exception:
            pass
        return fallback

    def _get_1080p_scale_pad_filter(self) -> str:
        """回傳「等比縮放到 1920x1080 + 不足補黑邊」的 FFmpeg filter 字串"""
        return (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )

    def _pick_color(self, title, var, preview_attr):
        """通用顏色選擇器"""
        color = colorchooser.askcolor(title=title, initialcolor=var.get())
        if color[1]:
            var.set(color[1])
            self.update_ktv_color_previews()

    def _make_color_row(self, parent, label_text, var, label_attr=None, preview_attr=None, padx_left=20):
        """通用顏色選擇器列：Label + Canvas預覽 + Entry + 選色按鈕"""
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
        btn = tk.Button(row, text="選色", bg="#f0f0f0", command=lambda: self._pick_color(label_text, var, None))
        btn.pack(side=tk.LEFT, padx=5)
        return row, canvas, btn

    def _run_pip(self, packages, target_dir, log_all=False):
        """通用 pip 安裝，回傳是否成功"""
        cmd = [str(self.local_python), "-m", "pip", "install",
               "--target", str(target_dir),
               "--retries", "10", "--timeout", "100",
               "--no-warn-script-location"] + (packages if isinstance(packages, list) else [packages])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, creationflags=self.subp_flags,
                                encoding='utf-8', errors='replace')
        for line in proc.stdout:
            clean = line.strip()
            if log_all:
                if clean:
                    self.log(f"  > {clean}")
            elif any(x in clean for x in ["Downloading", "Installing", "Collecting", "ERROR", "Exception", "Traceback", "Requirement already satisfied"]):
                self.log(f"  > {clean[:100]}{'...' if len(clean) > 100 else ''}")
        proc.wait()
        return proc.returncode == 0

    def setup_ui_style(self):
        """設定 UI 風格，讓介面更現代美觀"""
        style = ttk.Style()

        available_themes = style.theme_names()
        preferred_themes = ['clam', 'alt', 'default', 'classic']
        for theme in preferred_themes:
            if theme in available_themes:
                style.theme_use(theme)
                break

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
                       background=colors['primary'],
                       troughcolor=colors['border'],
                       bordercolor=colors['border'],
                       lightcolor=colors['primary'],
                       darkcolor=colors['primary'])

        style.configure('TCheckbutton', background=colors['bg'], foreground=colors['fg'])
        style.configure('TRadiobutton', background=colors['bg'], foreground=colors['fg'])

        self.ui_colors = colors
        self._bg = colors['bg']; self._fg = colors['fg']
        self._rbkw = {'bg': colors['bg'], 'fg': colors['fg'], 'selectcolor': colors['bg']}

    def _make_listbox_panel(self, parent, btn_specs):
        """通用：建立 Listbox+Scrollbar（左）+ 按鈕欄（右）"""
        lf = tk.Frame(parent, bg=self._bg)
        lf.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=5, pady=5)
        lb = tk.Listbox(lf, height=6, selectmode=tk.EXTENDED)
        lb.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        sb = tk.Scrollbar(lf)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)
        sb.config(command=lb.yview)
        bf = tk.Frame(parent, bg=self._bg)
        bf.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        C = self.ui_colors
        for text, cmd, color in btn_specs:
            tk.Button(bf, text=text, command=cmd, width=10, bg=C.get(color, color), fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        return lb, bf

    def setup_ui(self):
        tk.Label(self.root, text=f"MP3 人聲分離 & YouTube 下載/KTV 製作工具 {self.version}", font=("Arial", 13, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(pady=(3, 2))

        main_split = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=self._bg)
        main_split.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        left_panel = tk.Frame(main_split, bg=self._bg, width=200)
        left_panel.pack_propagate(False)
        main_split.add(left_panel, minsize=180)

        tk.Label(left_panel, text="功能選單", font=("Arial", 12, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(pady=(3, 5))

        self.tab_buttons = []
        self.tabs = []
        self.current_tab_index = 0

        tab_info = [
            ("📺", "YouTube 一鍵轉 KTV"),
            ("📥", "YouTube 下載 (MP3/MP4)"),
            ("🎬", "本地影片轉 KTV"),
            ("🎵", "本地音檔批量分離"),
            ("🎤", "本地影片辨識歌詞"),
            ("📝", "合併字幕與影片"),
            ("🔧", "環境修復"),
            ("📋", "執行日誌"),
            ("⚙️", "Whisper 辨識設定"),
            ("📧", "聯絡作者")
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
            self.tabs.append(content_frame)

        for i, frame in enumerate(self.content_frames):
            sticky = "nsew" if i == 7 else "new"
            frame.grid(row=0, column=0, sticky=sticky, padx=3, pady=2)
            if i != 0:
                frame.grid_remove()

        self.update_tab_buttons(0)

        yt_tab = self.content_frames[0]

        yt_url_frame = tk.Frame(yt_tab, bg=self._bg)
        yt_url_frame.pack(fill=tk.X, pady=2)

        tk.Label(yt_url_frame, text="YouTube 網址:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_entry = tk.Entry(yt_url_frame, textvariable=self.yt_url_var)
        self.yt_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_entry.bind("<Button-1>", self.quick_paste_url)

        tk.Label(yt_tab, text="(點擊輸入框自動貼上剪貼簿網址)", fg="gray", font=("Arial", 8), bg=self._bg).pack(anchor=tk.W, padx=(85, 0), pady=(1, 0))

        yt_dl_tab = self.content_frames[1]

        dl_url_row = tk.Frame(yt_dl_tab, bg=self._bg)
        dl_url_row.pack(fill=tk.X, pady=2)
        tk.Label(dl_url_row, text="YouTube 網址:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_dl_url_var = tk.StringVar()
        self.yt_dl_entry = tk.Entry(dl_url_row, textvariable=self.yt_dl_url_var)
        self.yt_dl_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_dl_entry.bind("<Button-1>", self.quick_paste_dl_url)
        tk.Label(dl_url_row, text="(點擊自動貼上)", fg="gray", font=("Arial", 8), bg=self._bg).pack(side=tk.LEFT)

        dl_opt_row = tk.Frame(yt_dl_tab, bg=self._bg)
        dl_opt_row.pack(fill=tk.X, pady=2)

        tk.Label(dl_opt_row, text="下載格式:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.dl_type_var = tk.StringVar(value="both")
        for _t, _v in [("MP3 + MP4","both"),("僅 MP3","mp3"),("僅 MP4","mp4")]:
            tk.Radiobutton(dl_opt_row, text=_t, variable=self.dl_type_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=4)

        tk.Label(dl_opt_row, text="  |  MP4 畫質:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(10, 0))
        self.dl_quality_var = tk.StringVar(value="1080")
        self._dl_quality_rbs = {}
        for _t, _v in [("最佳","best"),("1080p","1080"),("720p","720"),("480p","480")]:
            _rb = tk.Radiobutton(dl_opt_row, text=_t, variable=self.dl_quality_var, value=_v, **self._rbkw)
            _rb.pack(side=tk.LEFT, padx=4)
            self._dl_quality_rbs[_v] = _rb

        # ── 共用 BooleanVar：強制等比輸出 1080p（第一頁/第二頁同步）──
        # 第一頁（YouTube 轉 MKV / KTV）會在合成時套用 scale+pad
        # 第二頁（純下載）會在下載完成後再用 ffmpeg 輸出一份 1080p（_1080p.mp4）
        if not hasattr(self, "force_1080p_var"):
            self.force_1080p_var = tk.BooleanVar(value=False)

        # 第二頁：FFmpeg 強制輸出 1080p（等比放大 + 不足補黑邊）
        dl_force_row = tk.Frame(yt_dl_tab, bg=self._bg)
        dl_force_row.pack(fill=tk.X, pady=(2, 0))

        tk.Checkbutton(
            dl_force_row,
            text="強制等比輸出 1080p（不足自動補黑邊）",
            variable=self.force_1080p_var,
            bg=self._bg,
            fg=self._fg,
            selectcolor=self._bg,
            font=("Arial", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 0))

        local_v_tab = self.content_frames[2]

        self.v_list = []
        self.v_listbox, _ = self._make_listbox_panel(local_v_tab, [
            ('加入影片',   self.browse_local_video,   'info'),
            ('加入資料夾', self.browse_local_v_folder,'info'),
            ('移除選取',   self.remove_selected_v,   'warning'),
            ('清除清單',   self.clear_v_list,         'danger'),
        ])

        file_tab = self.content_frames[3]

        self.file_listbox, _ = self._make_listbox_panel(file_tab, [
            ('加入檔案', self.browse_file,        'info'),
            ('移除選取', self.remove_selected_file,'warning'),
            ('清除清單', self.clear_files,         'danger'),
        ])

        # ── stable-ts 共用 BooleanVar（lyrics_options_row 與 Tab4 同步）──
        self.use_stable_ts_var = tk.BooleanVar(value=self.config.get("use_stable_ts", True))

        def _on_stable_ts_toggle():
            """任一勾選框改變時，寫入 config 並儲存"""
            self.config["use_stable_ts"] = self.use_stable_ts_var.get()
            self.save_config()

        recognize_tab = self.content_frames[4]

        rec_video_frame = tk.LabelFrame(recognize_tab, text="影片檔案", padx=10, pady=10, bg=self._bg)
        rec_video_frame.pack(fill=tk.X, pady=5)

        self.rec_video_path_var = tk.StringVar()
        tk.Entry(rec_video_frame, textvariable=self.rec_video_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(rec_video_frame, text="選擇影片", command=self.browse_rec_video, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.LEFT)

        rec_options_frame = tk.LabelFrame(recognize_tab, text="辨識選項", padx=10, pady=10, bg=self._bg)
        rec_options_frame.pack(fill=tk.X, pady=5)

        rec_lang_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_lang_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(rec_lang_row, text="語言:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        # 預設不強制指定語言：讓 Whisper 自動偵測，避免混合語言歌曲被「硬轉」成單一語言（例如韓文被轉成中文）
        self.rec_language_var = tk.StringVar(value="auto")
        rec_lang_menu = ttk.Combobox(
            rec_lang_row,
            textvariable=self.rec_language_var,
            values=["auto (自動偵測)", "zh (中文)", "en (英文)", "ja (日文)", "ko (韓文)"],
            state="readonly",
            width=18
        )
        rec_lang_menu.pack(side=tk.LEFT, padx=5)

        tk.Label(rec_lang_row, text="模型大小:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(20, 5))
        self.whisper_model_var = tk.StringVar(value="medium")
        rec_model_menu = ttk.Combobox(rec_lang_row, textvariable=self.whisper_model_var,
                                      values=["tiny", "base", "small", "medium", "large"],
                                      state="readonly", width=8)
        rec_model_menu.pack(side=tk.LEFT, padx=5)
        tk.Label(rec_lang_row, text="（medium 準確度高，large 最準但較慢）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

        rec_sep_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_sep_row.pack(fill=tk.X)
        self.rec_separate_first_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            rec_sep_row,
            text="先分離人聲再辨識（準確度較高，需要較長時間）",
            variable=self.rec_separate_first_var,
            bg=self._bg,
            fg=self._fg,
            selectcolor=self._bg,
            font=("Arial", 9)
        ).pack(side=tk.LEFT, padx=5)

        rec_zh_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_zh_row.pack(fill=tk.X, pady=(4, 0))
        tk.Label(rec_zh_row, text="輸出文字:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.rec_lyrics_language_var = tk.StringVar(value="traditional")
        for _t, _v in [("繁體中文（預設）","traditional"),("簡體中文","simplified"),("原文字（不轉換）","original")]:
            tk.Radiobutton(rec_zh_row, text=_t, variable=self.rec_lyrics_language_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=4)

        rec_stable_ts_row = tk.Frame(rec_options_frame, bg=self._bg)
        rec_stable_ts_row.pack(fill=tk.X, pady=(4, 0))
        tk.Checkbutton(
            rec_stable_ts_row,
            text="啟用 stable-ts 精準時間軸對齊（推薦，需先安裝 stable-ts 套件）",
            variable=self.use_stable_ts_var,
            command=_on_stable_ts_toggle,
            bg=self._bg, fg=self._fg, selectcolor=self._bg,
            font=("Arial", 9)
        ).pack(side=tk.LEFT, padx=5)
        tk.Label(
            rec_stable_ts_row,
            text="可大幅提升逐字時間軸精度",
            bg=self._bg, fg="gray", font=("Arial", 8)
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Label(recognize_tab, text="💡 提示：此功能需要使用語音辨識模型", bg=self._bg, fg=self._fg, font=("Arial", 9)).pack(anchor=tk.W, padx=10, pady=5)

        merge_tab = self.content_frames[5]

        video_frame = tk.LabelFrame(merge_tab, text="影片檔案", padx=10, pady=10, bg=self._bg)
        video_frame.pack(fill=tk.X, pady=5)

        self.merge_video_path_var = tk.StringVar()
        tk.Entry(video_frame, textvariable=self.merge_video_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(video_frame, text="選擇影片", command=self.browse_merge_video, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.LEFT)

        subtitle_frame = tk.LabelFrame(merge_tab, text="字幕檔案", padx=10, pady=10, bg=self._bg)
        subtitle_frame.pack(fill=tk.X, pady=5)

        self.merge_subtitle_path_var = tk.StringVar()
        tk.Entry(subtitle_frame, textvariable=self.merge_subtitle_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(subtitle_frame, text="選擇字幕", command=self.browse_merge_subtitle, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.LEFT)

        warning_label = tk.Label(
            merge_tab,
            text="⚠️ 提醒：AI 辨識的歌詞可能有錯字，建議您先手動修正，或使用其他 AI 工具修復錯字後再進行字幕合併。",
            font=("Arial", 9),
            fg="#E65100",
            bg=self._bg,
            wraplength=780,
            justify=tk.LEFT
        )
        warning_label.pack(fill=tk.X, padx=10, pady=(5, 5))

        self.ktv_color_frame = tk.LabelFrame(merge_tab, text="字幕樣式設定", padx=10, pady=10, bg=self._bg)
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
            tk.Entry(fr, textvariable=color_var, width=8).pack(side=tk.LEFT, padx=2)
            tk.Button(fr, text="選色", bg="#f0f0f0", padx=3,
                      command=lambda: self._pick_color(label_text, color_var, None)).pack(side=tk.LEFT, padx=2)
            return fr, lbl, preview

        # --- 未唱顏色 ---
        self.ktv_unplayed_color_var = tk.StringVar()
        _uc_row, self.unplayed_label, self.unplayed_color_preview = _mk_color_row(
            colors_row, "未唱顏色:", self.ktv_unplayed_color_var, "#FFFFFF")

        # --- 已唱顏色 ---
        self.ktv_played_color_var = tk.StringVar()
        self.played_row, _, self.played_color_preview = _mk_color_row(
            colors_row, "已唱顏色:", self.ktv_played_color_var, "#0000FF")

        # --- 未唱邊框 ---
        self.ktv_border_color_var = tk.StringVar()
        _bc_row, _, self.border_color_preview = _mk_color_row(
            colors_row, "未唱邊框:", self.ktv_border_color_var, "#000000")

        # --- 已唱邊框（含勾選）---
        self.ktv_use_played_border_var = tk.BooleanVar(value=True)
        self.ktv_played_border_color_var = tk.StringVar(value="#ffffff")

        def _toggle_played_border():
            enabled = self.ktv_use_played_border_var.get()
            state = tk.NORMAL if enabled else tk.DISABLED
            self._played_border_entry.config(state=state)
            self._played_border_btn.config(state=state)
            try:
                self._map_expand_scale.config(state=state)
                self._map_expand_entry.config(state=state)
            except Exception:
                pass
            if enabled:
                self.played_border_color_preview.config(bg=self.ktv_played_border_color_var.get())
            else:
                self.played_border_color_preview.config(bg="#cccccc")

        _pb_row = tk.Frame(colors_row, bg=self._bg)
        _pb_row.pack(side=tk.LEFT, padx=(0, 0))
        self._played_border_chk = tk.Checkbutton(
            _pb_row, text="已唱邊框:",
            variable=self.ktv_use_played_border_var,
            bg=self._bg, fg=self._fg, selectcolor=self._bg,
            command=_toggle_played_border
        )
        self._played_border_chk.pack(side=tk.LEFT)
        self.played_border_color_preview = tk.Canvas(_pb_row, width=22, height=18, bg=self.ktv_played_border_color_var.get(), relief="solid", bd=1)
        self.played_border_color_preview.pack(side=tk.LEFT, padx=3)
        self._played_border_entry = tk.Entry(_pb_row, textvariable=self.ktv_played_border_color_var, width=8)
        self._played_border_entry.pack(side=tk.LEFT, padx=2)
        self._played_border_btn = tk.Button(_pb_row, text="選色", bg="#f0f0f0", padx=3,
                  command=lambda: self._pick_color("已唱邊框", self.ktv_played_border_color_var, None))
        self._played_border_btn.pack(side=tk.LEFT, padx=2)

        # border_row 保留為 _pb_row 的別名，供其他地方 pack/pack_forget 使用
        border_row = _pb_row

        # 已唱邊框遮罩「推進映射外擴」：用來微調邊框 clip 的前慢後快問題
        # 數值為「字體大小的倍數」，只影響 map_expand（映射端點），不影響 clip 範圍與速度時間軸
        map_expand_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        map_expand_row.pack(fill=tk.X, pady=5)
        tk.Label(map_expand_row, text="邊框粗細:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)

        self.ktv_border_map_expand_factor_var = tk.DoubleVar(value=4)
        self._map_expand_scale = tk.Scale(
            map_expand_row,
            from_=0, to=20, resolution=1,
            orient=tk.HORIZONTAL,
            variable=self.ktv_border_map_expand_factor_var,
            length=220,
            showvalue=False,
            bg=self._bg, fg=self._fg,
            highlightthickness=0
        )
        self._map_expand_scale.pack(side=tk.LEFT, padx=(0, 8))
        self._map_expand_entry = tk.Entry(map_expand_row, textvariable=self.ktv_border_map_expand_factor_var, width=5, justify='center')
        self._map_expand_entry.pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(map_expand_row, text="（像素；預設 4）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

        # 初始化一次，確保控制項狀態與預覽正確
        _toggle_played_border()

        font_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        font_row.pack(fill=tk.X, pady=5)
        tk.Label(font_row, text="字幕字體:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)

        available_fonts = self._get_system_fonts()
        default_font = "微軟正黑體" if "微軟正黑體" in available_fonts else (available_fonts[0] if available_fonts else "Arial")
        self.ktv_font_var = tk.StringVar(value=default_font)
        font_menu = ttk.Combobox(font_row, textvariable=self.ktv_font_var, values=available_fonts, state="normal", width=20)
        font_menu.pack(side=tk.LEFT, padx=5)
        font_menu.bind("<<ComboboxSelected>>", lambda e: self.update_ktv_color_previews())

        tk.Label(font_row, text="字體大小:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(20, 5))
        self.ktv_font_size_var = tk.StringVar(value="60")
        font_size_spin = tk.Spinbox(font_row, from_=10, to=200, textvariable=self.ktv_font_size_var, width=5, justify='center')
        font_size_spin.pack(side=tk.LEFT, padx=2)
        tk.Label(font_row, text="pt　（預設 60）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=2)

        margin_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        margin_row.pack(fill=tk.X, pady=5)
        tk.Label(margin_row, text="字幕高度:", bg=self._bg, fg=self._fg, width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.subtitle_margin_var = tk.StringVar(value="0")
        tk.Entry(margin_row, textvariable=self.subtitle_margin_var, width=6, justify='center').pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(margin_row, text="（0 = 預設位置，正數往上，負數往下）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

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
                self.ktv_two_line_advance_entry.config(state=st)
                self.ktv_two_line_gap_entry.config(state=st)
                for _e in ('ktv_two_line_top_x_entry', 'ktv_two_line_bottom_x_entry'):
                    if hasattr(self, _e):
                        getattr(self, _e).config(state=st)
            except Exception:
                pass

        tk.Checkbutton(
            self.two_line_row,
            text="雙行字幕（下一句提前顯示）",
            variable=self.ktv_two_line_var,
            bg=self._bg,
            fg=self._fg,
            selectcolor=self._bg,
            command=_toggle_two_line_advance_entry
        ).pack(side=tk.LEFT, padx=5)
        tk.Label(self.two_line_row, text="提前:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(10, 3))
        self.ktv_two_line_advance_entry = tk.Entry(self.two_line_row, textvariable=self.ktv_two_line_advance_var, width=5, justify='center')
        self.ktv_two_line_advance_entry.pack(side=tk.LEFT, padx=(0, 3))
        tk.Label(self.two_line_row, text="秒　", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)
        tk.Label(self.two_line_row, text="行距:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(10, 3))
        self.ktv_two_line_gap_entry = tk.Entry(self.two_line_row, textvariable=self.ktv_two_line_gap_var, width=5, justify='center')
        self.ktv_two_line_gap_entry.pack(side=tk.LEFT, padx=(0, 3))
        tk.Label(self.two_line_row, text="px（留空 = 自動）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)
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
        tk.Label( self.two_line_x_row, text="（正數往右、負數往左；0 = 置中）", bg=self._bg, fg="gray", font=("Arial", 8) ).pack(side=tk.LEFT)
        _toggle_two_line_advance_entry()

        self.ktv_color_mode_var = tk.StringVar(value="slide")
        self.mode_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        self.mode_hint_row = tk.Frame(self.ktv_color_frame, bg=self._bg)
        self.ktv_mode_hint_label = tk.Label(
            self.mode_hint_row,
            text="  💡 滑動漸變：使用 \\kf tag，顏色由左至右平滑掃過",
            font=("Arial", 8), fg="gray", bg=self._bg, anchor=tk.W
        )

        # 漸變微調固定值（UI 已移除，以固定預設值運行）
        self.ktv_pre_show_var         = tk.StringVar(value="0.0")
        self.ktv_hold_sec_var         = tk.StringVar(value="0")
        self.ktv_speed_factor_var     = tk.StringVar(value="1.0")
        self.ktv_singing_end_ratio_var = tk.StringVar(value="1.0")

        output_frame = tk.LabelFrame(merge_tab, text="輸出設定", padx=10, pady=10, bg=self._bg)
        output_frame.pack(fill=tk.X, pady=5)

        fmt_row = tk.Frame(output_frame, bg=self._bg)
        fmt_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(fmt_row, text="輸出格式:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT)
        self.merge_video_format_var = tk.StringVar(value="mp4")
        for _t, _v in [("MP4","mp4"),("MKV","mkv")]:
            tk.Radiobutton(fmt_row, text=_t, variable=self.merge_video_format_var, value=_v, **self._rbkw).pack(side=tk.LEFT, padx=10)

        # 合併按鈕區（同一行，json_to_ass_btn 選到 JSON 才顯示）
        merge_btn_frame = tk.Frame(merge_tab, bg=self._bg)
        merge_btn_frame.pack(fill=tk.X, pady=8, padx=5)

        self.merge_start_btn = tk.Button(
            merge_btn_frame, text="\u25b6 開始字幕合併",
            command=self.start_merge_subtitle_video,
            bg=self.ui_colors['warning'], fg='white',
            font=("Arial", 10, "bold"),
            relief='flat', padx=10, pady=8, cursor='hand2'
        )
        self.merge_start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        self.json_to_ass_btn = tk.Button(
            merge_btn_frame, text="\U0001f4dd 將 JSON 轉 ASS 字幕",
            command=self.start_json_to_ass_only,
            bg='#5D4037', fg='white',
            font=("Arial", 10, "bold"),
            relief='flat', padx=10, pady=8, cursor='hand2'
        )
        # 預設隱藏，選到 JSON 才顯示
        self.json_to_ass_btn.pack_forget()

        repair_tab = self.content_frames[6]

        self.repair_components_frame = tk.Frame(repair_tab, bg=self._bg)
        self.repair_components_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        repair_btn_frame = tk.Frame(repair_tab, bg=self._bg)
        repair_btn_frame.pack(fill=tk.X, padx=10, pady=10)

        self.repair_select_all_btn = tk.Button(repair_btn_frame, text="全選", width=10, bg="#f0f0f0", cursor='hand2')
        self.repair_select_all_btn.pack(side=tk.LEFT, padx=5)

        self.repair_select_none_btn = tk.Button(repair_btn_frame, text="全不選", width=10, bg="#f0f0f0", cursor='hand2')
        self.repair_select_none_btn.pack(side=tk.LEFT, padx=5)

        self.repair_start_btn = tk.Button(repair_btn_frame, text="🔧 開始修復",
                                          bg=self.ui_colors['warning'], fg="white",
                                          font=("Arial", 10, "bold"),
                                          padx=20, pady=8, cursor='hand2')
        self.repair_start_btn.pack(side=tk.RIGHT, padx=5)

        log_tab = self.content_frames[7]
        log_tab.rowconfigure(1, weight=1)  # 讓 log_area 列可以垂直擴展

        log_header_frame = tk.Frame(log_tab, bg=self._bg)
        log_header_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(log_header_frame, text="執行日誌", fg=self._fg, bg=self._bg, font=("Arial", 12, "bold")).pack(side=tk.LEFT)

        def copy_log_to_clipboard():
            log_content = self.log_area.get("1.0", tk.END).strip()
            if log_content:
                self.root.clipboard_clear()
                self.root.clipboard_append(log_content)
                copy_log_btn.config(text="✅ 已複製！")
            else:
                copy_log_btn.config(text="（日誌為空）")
            self.root.after(2000, lambda: copy_log_btn.config(text="📋 複製到剪貼簿"))

        copy_log_btn = tk.Button(log_header_frame,
                                 text="📋 複製到剪貼簿",
                                 command=copy_log_to_clipboard,
                                 bg=self.ui_colors['info'],
                                 fg='white',
                                 font=("Arial", 9),
                                 relief='flat',
                                 padx=8,
                                 pady=4,
                                 cursor='hand2')
        copy_log_btn.pack(side=tk.RIGHT)

        self.log_area = scrolledtext.ScrolledText(log_tab, height=20, font=("Consolas", 9), bg='white', fg=self._fg, relief='flat', bd=1, padx=8, pady=8)
        self.log_area.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

        self.setup_whisper_settings_tab(self.content_frames[8])

        contact_tab = self.content_frames[9]

        tk.Label(contact_tab, text="【開發者資訊】", font=("Arial", 14, "bold"), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=20, pady=(20, 10))
        tk.Label(contact_tab, text="作者：張書維", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=40)
        tk.Label(contact_tab, text="Line ID：game76420", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=40)

        fb_frame = tk.Frame(contact_tab, bg=self._bg)
        fb_frame.pack(anchor=tk.W, padx=40, pady=5)
        tk.Label(fb_frame, text="Facebook：", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(side=tk.LEFT)

        fb_link = tk.Label(fb_frame, text="www.facebook.com/changshuwei/", fg="blue", cursor="hand2", font=("Arial", 11, "underline"), bg=self._bg)
        fb_link.pack(side=tk.LEFT)
        fb_link.bind("<Button-1>", lambda e: webbrowser.open("https://www.facebook.com/changshuwei/"))

        tk.Label(contact_tab, text="", bg=self._bg).pack(pady=15)

        tk.Label(contact_tab, text="【捐款贊助】", font=("Arial", 14, "bold"), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=20, pady=(0, 10))
        tk.Label(contact_tab, text="若您覺得此工具好用，歡迎贊助支持開發者！", wraplength=600, justify=tk.LEFT, font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W, padx=40)

        bank_frame = tk.Frame(contact_tab, bg=self._bg)
        bank_frame.pack(anchor=tk.W, padx=40, pady=15)
        tk.Label(bank_frame, text="銀行代碼：822 (中國信託)", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W)
        tk.Label(bank_frame, text="帳號：159540291165", font=("Arial", 11, "bold"), fg="#D32F2F", bg=self._bg).pack(anchor=tk.W)
        tk.Label(bank_frame, text="戶名：張書維", font=("Arial", 11), bg=self._bg, fg=self._fg).pack(anchor=tk.W)

        self.settings_frame = tk.LabelFrame(self.right_main_container, text="核心設定", padx=10, pady=5, bg=self._bg)
        self.settings_frame.pack(fill=tk.X, pady=(0, 5))

        self.btn_frame = tk.Frame(self.right_main_container, bg=self._bg)
        self.btn_frame.pack(pady=(0, 3))

        self.left_btn_frame = tk.Frame(self.btn_frame, bg=self._bg)
        self.left_btn_frame.pack(side=tk.LEFT)

        self.start_btn = tk.Button(self.left_btn_frame, text="開始分離任務", command=self.on_start_click,
                                  bg=self.ui_colors['success'],
                                  fg="white",
                                  font=("Arial", 11, "bold"),
                                  width=20,
                                  relief='flat',
                                  padx=12,
                                  pady=5,
                                  cursor='hand2')
        self.start_btn.pack(side=tk.LEFT, padx=8)
        self.cancel_btn = tk.Button(self.left_btn_frame, text="取消任務", command=self.cancel_processing,
                                   bg=self.ui_colors['danger'],
                                   fg="white",
                                   font=("Arial", 10, "bold"),
                                   width=12,
                                   state=tk.DISABLED,
                                   relief='flat',
                                   padx=8,
                                   pady=4,
                                   cursor='hand2')
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        out_row = tk.Frame(self.settings_frame)
        out_row.pack(fill=tk.X, pady=2)
        self.output_dir_var = tk.StringVar(value=self.config.get("output_dir", str(self.app_dir / "output")))
        tk.Label(out_row, text="輸出目錄:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        tk.Entry(out_row, textvariable=self.output_dir_var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        tk.Button(out_row, text="瀏覽", command=self.browse_output_dir, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.RIGHT)

        self.opt_row = tk.Frame(self.settings_frame)
        self.opt_row.pack(fill=tk.X, pady=2)

        tk.Label(self.opt_row, text="運算裝置:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.device_var = tk.StringVar(value="cpu")
        tk.Radiobutton(self.opt_row, text="CPU", variable=self.device_var, value="cpu").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(self.opt_row, text="GPU (NVIDIA)", variable=self.device_var, value="gpu").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(self.opt_row, text="GPU (DirectML)", variable=self.device_var, value="directml").pack(side=tk.LEFT, padx=5)

        tk.Button(self.opt_row, text="🔍 檢測 GPU 環境", command=self.check_gpu_env, font=("Arial", 9), bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=10)

        self.denoise_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.opt_row, text="啟用 AI 去噪 (推薦)", variable=self.denoise_var).pack(side=tk.RIGHT, padx=10)

        self.overlap_var = tk.DoubleVar(value=0.5)
        self.vocal_mix_var = tk.DoubleVar(value=50)
        self.vocal_mix_label_var = tk.StringVar(value="")

        self.video_format_var = tk.StringVar(value="mkv")
        self.audio_track_mode_var = tk.StringVar(value="dual")

        self.model_row = tk.Frame(self.settings_frame)
        self.model_row.pack(fill=tk.X, pady=2)

        tk.Label(self.model_row, text="AI 模型:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        model_options =[
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏優化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品質綜合)",
            "Kim_Vocal_2.onnx (MDX - 極致人聲提取)",
            "htdemucs.yaml (Demucs - 4音軌高品質分離)",
            "htdemucs_ft.yaml (Demucs - 流行樂優化)",
            "htdemucs_6s.yaml (Demucs - 6音軌擴充版)"
        ]
        self.model_menu = ttk.Combobox(self.model_row, textvariable=self.model_var, values=model_options, state="readonly", width=45)
        self.model_menu.pack(side=tk.LEFT, padx=5)
        self.model_menu.current(0)

        self.output_format_row = tk.Frame(self.settings_frame)
        self.output_format_row.pack(fill=tk.X, pady=2)

        tk.Label(self.output_format_row, text="輸出格式:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        self.output_format_var = tk.StringVar(value="mp3")
        for fmt in ["mp3", "wav", "flac"]:
            tk.Radiobutton(self.output_format_row, text=fmt.upper(), variable=self.output_format_var, value=fmt).pack(side=tk.LEFT, padx=10)

        self.ktv_row = tk.Frame(self.settings_frame)
        self.ktv_row.pack(fill=tk.X, pady=2)

        tk.Label(self.ktv_row, text="KTV 影片格式:").pack(side=tk.LEFT)
        for _t, _v in [("MKV（預設，相容性最佳）","mkv"),("MP4","mp4")]:
            tk.Radiobutton(self.ktv_row, text=_t, variable=self.video_format_var, value=_v).pack(side=tk.LEFT, padx=5)

        self.track_row = tk.Frame(self.settings_frame)
        self.track_row.pack(fill=tk.X, pady=2)

        tk.Label(self.track_row, text="伴唱帶音軌:").pack(side=tk.LEFT)
        for _t, _v in [("雙音軌（伴唱＋人聲，預設）","dual"),
                        ("左伴唱／右人聲+伴奏（單音軌立體聲）","lr"),
                        ("純伴唱（僅伴奏，無人聲音軌）","inst")]:
            tk.Radiobutton(self.track_row, text=_t, variable=self.audio_track_mode_var, value=_v).pack(side=tk.LEFT, padx=5)

        self.mix_row = tk.Frame(self.settings_frame)
        self.mix_row.pack(fill=tk.X, pady=2)
        tk.Label(self.mix_row, text="導唱混合比例:").pack(side=tk.LEFT)
        tk.Scale(
            self.mix_row,
            from_=0, to=100,
            orient=tk.HORIZONTAL,
            showvalue=False,
            resolution=5,
            length=180,
            variable=self.vocal_mix_var,
            command=lambda _value: self.update_vocal_mix_label()
        ).pack(side=tk.LEFT, padx=5)
        tk.Label(self.mix_row, textvariable=self.vocal_mix_label_var, width=28, anchor="w").pack(side=tk.LEFT, padx=5)
        tk.Label(self.mix_row, text="人聲越高，越適合跟唱練習", fg="#666").pack(side=tk.LEFT, padx=5)
        self.update_vocal_mix_label()

        self.lyrics_row = tk.Frame(self.settings_frame)
        self.lyrics_row.pack(fill=tk.X, pady=2)

        self.enable_lyrics_recognition_var = tk.BooleanVar(value=False)
        tk.Checkbutton( self.lyrics_row, text="啟用 Whisper AI 歌詞識別（產生 SRT 字幕檔）", variable=self.enable_lyrics_recognition_var, font=("Arial", 9, "bold") ).pack(side=tk.LEFT)

        tk.Label(self.lyrics_row, text="輸出文字:").pack(side=tk.LEFT, padx=(20, 5))
        self.lyrics_language_var = tk.StringVar(value="traditional")
        for _t, _v in [("繁體中文","traditional"),("簡體中文","simplified"),("原文字","original")]:
            tk.Radiobutton(self.lyrics_row, text=_t, variable=self.lyrics_language_var, value=_v).pack(side=tk.LEFT, padx=4)

        self.lyrics_options_row = tk.Frame(self.settings_frame)
        self.lyrics_options_row.pack(fill=tk.X, pady=2)

        tk.Label(self.lyrics_options_row, text="辨識語言:", bg=self._bg, fg=self._fg, width=12, anchor='w').pack(side=tk.LEFT)
        # 預設不強制指定語言：讓 Whisper 自動偵測，避免混合語言歌曲被「硬轉」成單一語言（例如韓文被轉成中文）
        self.yt_whisper_language_var = tk.StringVar(value="auto")
        yt_lang_menu = ttk.Combobox(self.lyrics_options_row, textvariable=self.yt_whisper_language_var,
                                    values=["auto (自動偵測)", "zh (中文)", "en (英文)", "ja (日文)", "ko (韓文)"],
                                    state="readonly", width=12)
        yt_lang_menu.pack(side=tk.LEFT, padx=5)

        tk.Label(self.lyrics_options_row, text="模型大小:", bg=self._bg, fg=self._fg).pack(side=tk.LEFT, padx=(20, 5))
        self.yt_whisper_model_var = tk.StringVar(value="medium")
        yt_model_menu = ttk.Combobox(self.lyrics_options_row, textvariable=self.yt_whisper_model_var,
                                     values=["tiny", "base", "small", "medium", "large"],
                                     state="readonly", width=8)
        yt_model_menu.pack(side=tk.LEFT, padx=5)
        tk.Label(self.lyrics_options_row, text="（medium 準確度高，large 最準但較慢）", bg=self._bg, fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

        self.lyrics_stable_ts_row = tk.Frame(self.settings_frame)
        self.lyrics_stable_ts_row.pack(fill=tk.X, pady=2)
        tk.Checkbutton(
            self.lyrics_stable_ts_row,
            text="啟用 stable-ts 精準時間軸對齊（推薦，需先安裝 stable-ts 套件）",
            variable=self.use_stable_ts_var,
            command=_on_stable_ts_toggle,
            font=("Arial", 9),
        ).pack(side=tk.LEFT)
        tk.Label(
            self.lyrics_stable_ts_row,
            text="可大幅提升逐字時間軸精度（±50ms），KTV 歌詞對齊必備",
            bg=self._bg, fg="gray", font=("Arial", 8)
        ).pack(side=tk.LEFT, padx=(6, 0))


        self.extra_video_row = tk.Frame(self.settings_frame)
        self.extra_video_row.pack(fill=tk.X, pady=2)

        # force_1080p_var 可能已在第二頁初始化（用於同步勾選），這裡不要重建，避免兩頁不同步
        if not hasattr(self, "force_1080p_var"):
            self.force_1080p_var = tk.BooleanVar(value=False)
        self.force_1080p_chk = tk.Checkbutton( self.extra_video_row, text="強制等比輸出 1080p（不足自動補黑邊）", variable=self.force_1080p_var )
        self.force_1080p_chk.pack(side=tk.LEFT)

        self.yt_cc_var = tk.BooleanVar(value=False)
        self.yt_cc_chk = tk.Checkbutton( self.extra_video_row, text="啟用 YouTube CC 字幕處理", variable=self.yt_cc_var, command=self.refresh_yt_subtitle_mode_ui )
        self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))

        self.yt_subtitle_mode_var = tk.StringVar(value="mux")
        self.yt_subtitle_mode_row = tk.Frame(self.settings_frame)
        tk.Label(self.yt_subtitle_mode_row, text="字幕模式:").pack(side=tk.LEFT)
        for _t, _v in [("下載SRT字幕","srt_only"),("下載srt字幕並合成","mux")]:
            tk.Radiobutton(self.yt_subtitle_mode_row, text=_t, variable=self.yt_subtitle_mode_var, value=_v, command=self.refresh_yt_subtitle_mode_ui).pack(side=tk.LEFT, padx=5)

        self.status_frame = tk.Frame(self.right_scrollable_frame, bg=self._bg)
        self.status_frame.pack(fill=tk.X, padx=20, pady=(4, 0))
        self.status_var = tk.StringVar(value="狀態: 就緒")
        self.status_label = tk.Label(self.status_frame, textvariable=self.status_var, fg=self.ui_colors['primary'], bg=self._bg, font=("Arial", 10))
        self.status_label.pack(side=tk.LEFT)

        self.progress_text = tk.Label(self.status_frame, text="0%", font=("Arial", 9, "bold"), fg=self._fg, bg=self._bg)
        self.progress_text.pack(side=tk.RIGHT)

        progress_header = tk.Frame(self.right_scrollable_frame, bg=self._bg)
        progress_header.pack(fill=tk.X, padx=20, pady=(3, 0))
        tk.Label(progress_header, text="進度:", fg=self._fg, bg=self._bg, font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.step_label = tk.Label(progress_header, text="", fg=self.ui_colors['info'], bg=self._bg, font=("Arial", 9))
        self.step_label.pack(side=tk.LEFT, padx=(8, 0))
        self.item_progress_bar = ttk.Progressbar(self.right_scrollable_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.item_progress_bar.pack(fill=tk.X, padx=20, pady=(2, 5))

        self.refresh_yt_subtitle_mode_ui()
        self.root.update_idletasks()
        self.show_welcome_message()

    def convert_lyrics_language(self, lyrics_text, target_language):
        """
        轉換歌詞語言（簡繁轉換）
        target_language: "original", "traditional", "simplified"
        """
        if target_language == "original":
            return lyrics_text

        if hasattr(self, 'common_lib_dir'):
            self._ensure_common_lib_in_path()

        zhconv_available = False
        try:
            import zhconv
            zhconv_available = True
        except ImportError:
            try:
                self.log("  📦 正在安裝 zhconv 中文簡繁轉換套件...")
                self.log("     （這可能需要幾秒鐘，請稍候...）")
                self.update_status("正在安裝 zhconv 套件...", "orange")

                if hasattr(self, 'local_python') and hasattr(self, 'common_lib_dir'):
                    result = subprocess.run(
                        [str(self.local_python), '-m', 'pip', 'install',
                         '--target', str(self.common_lib_dir), 'zhconv'],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        self.log("  ✅ zhconv 安裝成功！")
                        try:
                            if 'zhconv' in sys.modules:
                                del sys.modules['zhconv']
                            if str(self.common_lib_dir) not in sys.path:
                                sys.path.insert(0, str(self.common_lib_dir))
                            import zhconv
                            zhconv_available = True
                        except:
                            zhconv_available = False
                            self.log("  ⚠️ 安裝後仍無法匯入 zhconv")
                    else:
                        self.log(f"  ⚠️ zhconv 安裝失敗: {result.stderr}")
                else:
                    self.log("  ⚠️ 無法自動安裝 zhconv")
            except Exception as e:
                self.log(f"  ⚠️ 安裝 zhconv 時出錯: {str(e)}")

        if zhconv_available:
            try:
                if target_language == "traditional":
                    return zhconv.convert(lyrics_text, 'zh-tw')
                else:
                    return zhconv.convert(lyrics_text, 'zh-cn')
            except Exception as e:
                self.log(f"  ⚠️ zhconv 轉換失敗: {str(e)}")

        return lyrics_text

    def _get_cookie_opts(self, force_no_cookie=False):
        """根據使用者選擇的瀏覽器，回傳 yt-dlp 的 cookie 參數列表。"""
        if force_no_cookie:
            return []
        if not hasattr(self, 'cookie_browser_var'):
            return []
        browser = self.cookie_browser_var.get()
        if browser == "none":
            return []
        return ["--cookies-from-browser", browser]

    def _build_ytdlp_common_opts(self, js_runtime_opts=None, force_no_cookie=False):
        """建立 yt-dlp 通用參數（重試、FFmpeg、Cookie 等）"""
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
            # mweb + android 是目前仍可靠的 player_client
            # tv_embedded / web 已被 YouTube 近期更新限制
            "--extractor-args", "youtube:player_client=mweb,android",
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
        """解析 yt-dlp 錯誤訊息並附上中文說明"""
        self.log(f"  ❌ {line}")
        error_lower = line.lower()
        if "this video is not available" in error_lower:
            self.log("  💡 中文說明：此影片無法存取！可能原因：")
            self.log("     1. 影片是私人影片")
            self.log("     2. 影片已被刪除")
            self.log("     3. 影片有地區鎖定（Geo-block）")
            self.log("     4. 請確認您使用的是「單一影片」連結，不是播放列表或電台連結！")
        elif "video unavailable" in error_lower:
            self.log("  💡 中文說明：影片無法使用！")
        elif "age restricted" in error_lower:
            self.log("  💡 中文說明：此影片有年齡限制，建議使用 Cookie 選項！")
        elif "sign in to confirm" in error_lower:
            self.log("  💡 中文說明：需要登入確認年齡，請在設定中選擇瀏覽器 Cookie！")
        elif "cookie database" in error_lower or "could not copy chrome" in error_lower:
            self.log("  💡 中文說明：無法讀取瀏覽器 Cookie，正在自動重試而不用 Cookie...")
        elif "429" in error_lower or "too many requests" in error_lower:
            self.log("  💡 中文說明：YouTube 封鎖太多請求！")
            self.log("     已自動開啟重試和延遲機制，請稍候...")
            self.log("     如果還是失敗，請稍後再試或選擇瀏覽器 Cookie！")

    def _get_ytdlp_js_runtime_opts(self):
        """自動偵測 yt-dlp 可用的 JS runtime，提升 YouTube 資訊與字幕擷取成功率。"""
        if self._yt_js_runtime_cache is not None:
            return list(self._yt_js_runtime_cache)

        runtime_candidates = [ ("deno", shutil.which("deno")), ("node", shutil.which("node")), ("bun", shutil.which("bun")), ]

        quickjs_path = shutil.which("quickjs") or shutil.which("qjs")
        if quickjs_path:
            runtime_candidates.append(("quickjs", quickjs_path))

        for runtime_name, runtime_path in runtime_candidates:
            if runtime_path:
                self._yt_js_runtime_cache = ["--js-runtimes", f"{runtime_name}:{runtime_path}"]
                if not self._yt_js_runtime_notice_shown:
                    self.log(f"  ℹ️ 已啟用 yt-dlp JavaScript runtime：{runtime_name}")
                    self._yt_js_runtime_notice_shown = True
                return list(self._yt_js_runtime_cache)

        self._yt_js_runtime_cache = []
        if not self._yt_js_runtime_notice_shown:
            self.log("  ⚠️ 未偵測到 deno/node/bun/quickjs；YouTube 字幕或格式清單可能不完整。")
            self._yt_js_runtime_notice_shown = True
        return []

    def _get_ytdlp_command_base(self):
        """回傳可實際啟動 yt-dlp 的命令前綴"""
        for p in [self.ytdlp_dir/"yt-dlp.exe", self.py_dir/"Scripts"/"yt-dlp.exe", self.lib_dir/"bin"/"yt-dlp.exe"]:
            if p.exists(): return [str(p)]
        for p in [self.ytdlp_dir/"yt_dlp"/"__main__.py", self.lib_dir/"yt_dlp"/"__main__.py"]:
            if p.exists(): return [str(self.local_python), str(p)]
        return [str(self.local_python), "-m", "yt_dlp"]

    def refresh_start_button_text(self):
        """依當前分頁與字幕模式更新主按鈕文字。"""
        current_tab = self.current_tab_index
        vfmt = self.video_format_var.get().upper() if hasattr(self, 'video_format_var') else "MKV"
        if current_tab == 5 or current_tab == 6 or current_tab == 7 or current_tab == 8:
            self.btn_frame.pack_forget()
        else:
            self.btn_frame.pack(pady=(0, 3))
            if current_tab == 0:
                self.start_btn.config(text=f"一鍵製作 {vfmt} 伴唱帶", bg=self.ui_colors['primary'])
            elif current_tab == 1:
                self.start_btn.config(text="立即下載 YouTube 檔案", bg=self.ui_colors['info'])
            elif current_tab == 2:
                self.start_btn.config(text=f"製作本地影片 KTV ({vfmt})", bg=self.ui_colors['primary'])
            elif current_tab == 3:
                self.start_btn.config(text="開始批量分離音檔", bg=self.ui_colors['success'])
            elif current_tab == 4:
                self.start_btn.config(text="開始辨識歌詞", bg=self.ui_colors['info'])

    def refresh_yt_subtitle_mode_ui(self):
        """依分頁與勾選狀態顯示字幕模式列。"""
        current_tab = self.current_tab_index
        if current_tab == 0 and self.yt_cc_var.get():
            self.yt_subtitle_mode_row.pack(fill=tk.X, pady=2)
        else:
            self.yt_subtitle_mode_row.pack_forget()
        if hasattr(self, "start_btn"):
            self.refresh_start_button_text()

    @staticmethod
    def extract_youtube_video_id(url):
        """從常見 YouTube 網址格式中擷取影片 ID。"""
        match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([0-9A-Za-z_-]{11})", url)
        return match.group(1) if match else None

    def _rename_subtitle_to(self, subtitle_file, desired_path):
        """將字幕檔移動到 desired_path（共用核心），回傳最終路徑字串。"""
        try:
            subtitle_path = Path(subtitle_file)
            if not subtitle_path.exists():
                return subtitle_file
            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)
            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass
            shutil.move(str(subtitle_path), str(desired_path))
            self.log(f"  📝 字幕檔已對齊命名：{desired_path.name}")
            return str(desired_path)
        except Exception as e:
            self.log(f"  ⚠️ 字幕檔重新命名失敗，保留原檔名：{str(e)}")
            return subtitle_file

    def align_subtitle_filename(self, subtitle_file, target_media_file):
        """將字幕檔改名成與目標媒體檔完全同主檔名，副檔名固定為 .srt。"""
        if not Path(target_media_file).exists():
            return subtitle_file
        desired = Path(target_media_file).parent / f"{Path(target_media_file).stem}.srt"
        return self._rename_subtitle_to(subtitle_file, desired)

    def normalize_subtitle_filename(self, subtitle_file, desired_stem):
        """將字幕檔改為固定主檔名，副檔名統一為 .srt。"""
        desired = Path(subtitle_file).parent / f"{desired_stem}.srt"
        return self._rename_subtitle_to(subtitle_file, desired)

    def switch_tab(self, index):
        """切換到指定的分頁"""
        if self.current_tab_index == index:
            return

        self.content_frames[self.current_tab_index].grid_remove()

        self.current_tab_index = index
        is_log = (index == 7)
        if is_log:
            self.tab_container.pack_configure(fill=tk.BOTH, expand=True)
        else:
            self.tab_container.pack_configure(fill=tk.X, expand=False)
        sticky = "nsew" if is_log else "new"
        self.content_frames[index].grid(row=0, column=0, sticky=sticky, padx=3, pady=2)

        if self.current_tab_index == 6:
            self.check_components(prompt=False)

        self.root.update_idletasks()

        self.update_tab_buttons(index)

        self._on_tab_changed_logic(index)

    def update_tab_buttons(self, active_index):
        """更新按鈕的視覺樣式"""
        for i, btn in enumerate(self.tab_buttons):
            if i == active_index:
                btn.configure(bg=self.ui_colors['primary'], fg='white', relief='sunken')
            else:
                btn.configure(bg=self._bg, fg=self._fg, relief='flat')

    def _on_tab_changed_logic(self, current_tab):
        """原來的 on_tab_changed 邏輯，用於處理核心設定顯示/隱藏等"""
        is_download_only = (current_tab == 1)
        is_merge_tab = (current_tab == 5)
        is_repair_tab = (current_tab == 6)
        is_log_tab = (current_tab == 7)
        is_whisper_settings_tab = (current_tab == 8)
        is_contact_tab = (current_tab == 9)

        if is_merge_tab or is_repair_tab or is_log_tab or is_whisper_settings_tab or is_contact_tab:
            self.settings_frame.pack_forget()
        else:
            self.settings_frame.pack(fill=tk.X, pady=(0, 3))

            is_recognize_tab = (current_tab == 4)

            rows_for_ai_except_opt_model = [self.output_format_row, self.ktv_row, self.track_row, self.mix_row]
            for row in rows_for_ai_except_opt_model:
                if is_download_only or is_recognize_tab:
                    row.pack_forget()
                else:
                    row.pack(fill=tk.X, pady=2)

            if is_download_only or is_recognize_tab:
                self.lyrics_row.pack_forget()
                self.lyrics_options_row.pack_forget()
                self.lyrics_stable_ts_row.pack_forget()
            else:
                self.lyrics_row.pack(fill=tk.X, pady=2)
                self.lyrics_options_row.pack(fill=tk.X, pady=2)
                self.lyrics_stable_ts_row.pack(fill=tk.X, pady=2)

            if current_tab == 1:
                self.opt_row.pack_forget()
                self.model_row.pack_forget()
            else:
                self.opt_row.pack(fill=tk.X, pady=2)
                self.model_row.pack(fill=tk.X, pady=2)

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
                if current_tab != 0:
                    self.yt_cc_var.set(False)
            self.refresh_yt_subtitle_mode_ui()

        self.refresh_start_button_text()

    def update_vocal_mix_label(self):
        """更新導唱混合比例顯示文字。"""
        vocal_pct = int(round(float(self.vocal_mix_var.get())))
        inst_pct = max(0, 100 - vocal_pct)
        self.vocal_mix_label_var.set(f"人聲 {vocal_pct}% / 伴奏 {inst_pct}%")

    def on_start_click(self):
        """智能啟動按鈕：根據當前分頁決定執行對應功能"""
        current_tab = self.current_tab_index
        if current_tab == 0:
            self.start_yt_process()
        elif current_tab == 1:
            self.start_pure_download()
        elif current_tab == 2:
            self.start_local_v_process()
        elif current_tab == 3:
            self.start_separation()
        elif current_tab == 4:
            self.start_recognize_lyrics()
        elif current_tab == 5:
            self.start_merge_subtitle_video()

    def browse_local_video(self):
        file_paths = filedialog.askopenfilenames(title="選擇影片檔案", filetypes=[("影片檔案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有檔案", "*.*")])
        if file_paths:
            self._add_files_to_listbox(file_paths, self.v_list, self.v_listbox)

    def _add_files_to_listbox(self, file_paths, file_list, listbox):
        """通用：將檔案路徑加入 file_list 與 listbox（去重）"""
        for fp in file_paths:
            fp_abs = str(Path(fp).absolute())
            if fp_abs not in file_list:
                file_list.append(fp_abs)
                listbox.insert(tk.END, os.path.basename(fp_abs))

    def browse_local_v_folder(self):
        folder_path = filedialog.askdirectory(title="選擇影片資料夾")
        if folder_path:
            for ext in ["mp4", "mkv", "avi", "mov", "wmv", "webm"]:
                self._add_files_to_listbox( Path(folder_path).glob(f"*.{ext}"), self.v_list, self.v_listbox )

    def _remove_selected_from_list(self, listbox, file_list):
        """通用：移除 listbox 選取項目"""
        for idx in reversed(listbox.curselection()):
            file_list.pop(idx)
            listbox.delete(idx)

    def _clear_list(self, listbox, file_list):
        """通用：清空 listbox 與 file_list"""
        file_list.clear()
        listbox.delete(0, tk.END)

    def remove_selected_v(self): self._remove_selected_from_list(self.v_listbox, self.v_list)
    def clear_v_list(self): self._clear_list(self.v_listbox, self.v_list)

    def _rename_output_file(self, src_path_str, dst_path, label="檔案"):
        """通用：安全重新命名輸出檔案，回傳最終路徑（字串）。"""
        try:
            src = Path(src_path_str)
            self.log(f"  📝 處理 {label}...")
            self.log(f"  📝 原始檔案: {src.name}")
            self.log(f"  📝 目標檔案: {dst_path.name}")
            if src.exists():
                if dst_path.exists():
                    self.log(f"  📝 刪除舊的目標檔案")
                    dst_path.unlink()
                self.log(f"  📝 執行重新命名...")
                shutil.move(str(src), str(dst_path))
                self.log(f"  ✅ {label} 已重新命名為: {dst_path.name}")
                return str(dst_path)
        except Exception as e:
            self.log(f"  ❌ {label} 重新命名失敗: {str(e)}")
            self.log(f"     {traceback.format_exc()}")
        return src_path_str

    def start_local_v_process(self):
        if not self.v_list:
            messagebox.showwarning("警告", "請先加入影片檔案！")
            return
        self._begin_processing("正在進行批次影片處理...", self.local_v_batch_process)

    def local_v_batch_process(self):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            enable_lyrics = self.enable_lyrics_recognition_var.get()

            total = len(self.v_list)
            for i, video_path in enumerate(self.v_list):
                if not self.is_processing or self.cancel_event.is_set():
                    self.log("🛑 批次處理已中止。")
                    break

                video_stem = Path(video_path).stem
                self.log(f"\n--- 正在處理 ({i+1}/{total}): {os.path.basename(video_path)} ---")

                self.v_listbox.selection_clear(0, tk.END)
                self.v_listbox.selection_set(i)
                self.v_listbox.see(i)

                temp_audio = Path(output_dir) / f"{video_stem}_temp_audio.mp3"

                progress_base = int((i / total) * 100)
                progress_step = int(100 / total)

                self.update_progress(progress_base + int(progress_step * 0.1), f"正在擷取音訊 ({i+1}/{total})")
                self.log("  > 正在從影片擷取音訊...")
                ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
                extract_cmd =[ str(ffmpeg_exe), "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-ab", "320k", str(temp_audio) ]
                subprocess.run(extract_cmd, check=True, creationflags=self.subp_flags)

                self.update_progress(progress_base + int(progress_step * 0.3), f"正在 AI 分離 ({i+1}/{total})")
                success = self.run_audio_separator(str(temp_audio), output_dir)

                if success:
                    srt_subtitle = None
                    json_subtitle = None
                    if enable_lyrics:
                        result = self.recognize_lyrics_and_generate_srt(str(temp_audio), output_dir)
                        if result:
                            srt_subtitle, json_subtitle, _ = result
                            final_srt_path = Path(output_dir) / f"{video_stem}_KTV.srt"
                            final_json_path = Path(output_dir) / f"{video_stem}_KTV.json"

                            srt_subtitle = self._rename_output_file(srt_subtitle, final_srt_path, "SRT 字幕檔")
                            if json_subtitle:
                                self._rename_output_file(json_subtitle, final_json_path, "JSON 歌詞檔")

                    self.log("  > 正在整理產出檔案...")
                    voc_file, inst_file = self.consolidate_stems(str(temp_audio), video_path, output_dir)

                    if voc_file and inst_file:
                        vfmt = self.video_format_var.get()
                        self.update_progress(progress_base + int(progress_step * 0.8), f"正在合成 {vfmt.upper()} ({i+1}/{total})")
                        output_file = Path(output_dir) / f"{video_stem}_KTV.{vfmt}"
                        mkv_success = self.synthesize_mkv(video_path, voc_file, inst_file, str(output_file), subtitle_file=srt_subtitle)

                        if mkv_success:
                            self.log(f"✅ 成功生成 {vfmt.upper()}: {output_file.name}")
                        else:
                            self.log(f"❌ {video_stem} MKV 合成失敗。")
                    else:
                        self.log(f"❌ {video_stem} 找不到分離後的必要檔案。")
                else:
                    self.log(f"❌ {video_stem} 音訊分離失敗。")

            self.update_progress(100, "批次處理完成")
            self.log("\n✨ 所有影片批次處理任務已結束！")
            self._show_done_and_open("完成", f"已完成 {total} 個影片的處理！\n檔案已儲存至: {output_dir}", output_dir)

        except Exception as e:
            self.log(f"❌ 批次處理中出錯: {str(e)}")
        finally:
            self.finish_processing()

    def start_pure_download(self):
        """純下載邏輯：不進行 AI 分離與合成"""
        url = self.yt_dl_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "請輸入 YouTube 網址！")
            return
        self._begin_processing("正在下載 YouTube 檔案...", self.pure_download_process, url)

    def _download_youtube_from_ui(self, url, output_dir, mode, *, quality="1080", download_subtitles=False, log_quality_text=None):
        """共用：第一頁/第二頁都透過這裡呼叫下載核心（方便只維護一次）"""
        if log_quality_text is None:
            log_quality_text = str(quality)
        self.log(f"🚀 開始下載任務 (格式: {str(mode).upper()}，畫質: {log_quality_text})...")
        return self.download_youtube(url, output_dir, mode=mode, download_subtitles=download_subtitles, quality=quality)

    def pure_download_process(self, url):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            dl_type     = self.dl_type_var.get()
            quality     = self.dl_quality_var.get()
            force_upscale_output_1080p = bool(getattr(self, "force_1080p_var", tk.BooleanVar(value=False)).get())

            quality_text = f"{quality}"

            # 直接呼叫第一分頁的下載核心，傳入畫質參數
            result = self._download_youtube_from_ui(
                url, output_dir, dl_type,
                quality=quality,
                download_subtitles=False,
                log_quality_text=quality_text
            )

            # 解析回傳值
            if dl_type == "both":
                video_file, audio_file = result if result else (None, None)
            elif dl_type == "mp4":
                video_file = result
                audio_file = None
            else:  # mp3
                video_file = None
                audio_file = result

            # 「強制等比輸出 1080p」：純下載分頁也套用（把下載到的 MP4 另外輸出一份 1080p）
            if force_upscale_output_1080p:
                if dl_type == "mp3":
                    self.log("⚠️ 你目前選的是「僅 MP3」，沒有下載影片，因此無法執行 1080p 放大。請改選「僅 MP4」或「MP3 + MP4」。")
                elif video_file and os.path.exists(str(video_file)):
                    self.log("🖼️ 已勾選「強制等比輸出 1080p」：將對下載的 MP4 進行等比縮放 + 補黑邊，並覆蓋原檔。")
                    video_file = self.upscale_video_to_1080p(str(video_file), replace_original=True)

            mp4_ok = bool(video_file)
            mp3_ok = bool(audio_file)

            if dl_type in ["both", "mp4"] and not mp4_ok:
                self.log("  ❌ MP4 下載失敗，請查看上方日誌。")
            if dl_type in ["both", "mp3"] and not mp3_ok:
                self.log("  ❌ MP3 下載失敗，請查看上方日誌。")

            self.log("\n✅ 所有任務已全部完成！")
            self._show_done_and_open("完成", "YouTube 下載成功！", output_dir if os.path.exists(output_dir) else None)
        except Exception as e:
            self.log(f"❌ 下載過程中出錯: {str(e)}")
        finally:
            self.finish_processing()

    def _quick_paste_yt_url(self, url_var, log_prefix=""):
        """通用：點擊輸入框時自動貼上剪貼簿中的 YouTube 網址"""
        try:
            clipboard = self.root.clipboard_get().strip()
            if clipboard and clipboard != url_var.get().strip():
                if "youtube.com/" in clipboard or "youtu.be/" in clipboard:
                    url_var.set(clipboard)
                    self.log(f"📋 {log_prefix}已從剪貼簿貼上網址: {clipboard}")
                    if "list=" in clipboard and "watch?v=" not in clipboard and "/shorts/" not in clipboard:
                        self.log("⚠️ 偵測到播放清單連結，本工具僅會下載第一支影片（已加入 --no-playlist）。")
        except Exception:
            pass

    def quick_paste_url(self, event):  self._quick_paste_yt_url(self.yt_url_var)

    def quick_paste_dl_url(self, event):  self._quick_paste_yt_url(self.yt_dl_url_var, "[下載分頁] ")

    def show_welcome_message(self):
        welcome_text = (
            "==================================================\n"
            " 🎵 歡迎使用 MP3 人聲分離 & YouTube 下載/KTV 製作工具\n"
            "==================================================\n"
            "【快速入門】\n"
            "1. YouTube 轉 MKV：貼上網址，點擊「一鍵製作」即可自動完成。\n"
            "2. 本地分離：切換至分頁，加入 MP3 檔案，點擊「開始分離」。\n"
            "--------------------------------------------------\n"
            "💡 提示：點擊 YouTube 網址框可自動貼上剪貼簿內容。\n"
            "💡 建議：初次使用請確保環境已「初始化/修復」完成。\n"
            "==================================================\n"
            "🚀 系統就緒，請選擇功能分頁開始使用。\n"
        )
        self.log_area.insert(tk.END, welcome_text + "\n")
        self.log_area.see(tk.END)

    def show_contact_info(self):
        """顯示自定義開發者資訊視窗，支援點擊連結"""
        contact_window = tk.Toplevel(self.root)
        contact_window.title("聯絡作者 / 贊助支援")
        contact_window.geometry("420x380")
        contact_window.resizable(False, False)

        contact_window.transient(self.root)
        contact_window.grab_set()

        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (420 // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (380 // 2)
        contact_window.geometry(f"+{x}+{y}")

        main_frame = tk.Frame(contact_window, padx=30, pady=25)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="【開發者資訊】", font=("Arial", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        tk.Label(main_frame, text="作者：張書維", font=("Arial", 10)).pack(anchor=tk.W, padx=15)
        tk.Label(main_frame, text="Line ID：game76420", font=("Arial", 10)).pack(anchor=tk.W, padx=15)

        fb_frame = tk.Frame(main_frame)
        fb_frame.pack(anchor=tk.W, padx=15, pady=2)
        tk.Label(fb_frame, text="Facebook：", font=("Arial", 10)).pack(side=tk.LEFT)

        fb_link = tk.Label(fb_frame, text="www.facebook.com/changshuwei/", fg="blue", cursor="hand2", font=("Arial", 10, "underline"))
        fb_link.pack(side=tk.LEFT)

        fb_link.bind("<Button-1>", lambda e: webbrowser.open("https://www.facebook.com/changshuwei/"))

        tk.Label(main_frame, text="").pack(pady=5) # 間隔

        tk.Label(main_frame, text="【捐款贊助】", font=("Arial", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        tk.Label(main_frame, text="若您覺得此工具好用，歡迎贊助支持開發者！", wraplength=350, justify=tk.LEFT, font=("Arial", 10)).pack(anchor=tk.W, padx=15)

        bank_frame = tk.Frame(main_frame)
        bank_frame.pack(anchor=tk.W, padx=15, pady=10)
        tk.Label(bank_frame, text="銀行代碼：822 (中國信託)", font=("Arial", 10)).pack(anchor=tk.W)
        tk.Label(bank_frame, text="帳號：159540291165", font=("Arial", 10, "bold"), fg="#D32F2F").pack(anchor=tk.W)
        tk.Label(bank_frame, text="戶名：張書維", font=("Arial", 10)).pack(anchor=tk.W)

        tk.Button(main_frame, text="我知道了", command=contact_window.destroy, width=15, bg="#f0f0f0").pack(pady=(20, 0))

    def log(self, message):  self.root.after(0, lambda: self._safe_log(message))

    def _safe_log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        if self.is_processing:
            clean_msg = message.strip()
            self.status_var.set(f"狀態: {clean_msg}")

    def update_progress(self, item_percent=None, overall_percent=None, text=None, step_text=None):
        self.root.after(0, lambda: self._safe_update_progress(item_percent, overall_percent, text, step_text))

    def _safe_update_progress(self, item_percent, overall_percent, text, step_text=None):
        if item_percent is not None:
            try:
                self.item_progress_bar["value"] = float(item_percent)
                self.progress_text.config(text=f"{float(item_percent):.0f}%")
            except (ValueError, TypeError):
                pass
        if text:
            self.status_var.set(f"狀態: {text}")
        if step_text is not None:
            self.step_label.config(text=step_text)
        elif item_percent is not None and float(item_percent) >= 100:
            self.step_label.config(text="")

    def browse_file(self):
        filenames = filedialog.askopenfilenames(filetypes=[("Audio files", "*.mp3 *.wav *.flac *.m4a"), ("All files", "*.*")])
        if filenames:
            self._add_files_to_listbox(filenames, self.file_list, self.file_listbox)

    def remove_selected_file(self):
        self._remove_selected_from_list(self.file_listbox, self.file_list)

    def clear_files(self):  self._clear_list(self.file_listbox, self.file_list)

    def load_config(self):
        """載入設定檔，若不存在則建立預設設定"""
        default_config = {
            "output_dir": str(self.app_dir / "output"),
            "open_folder_after_complete": False,
            "use_stable_ts": True,
            "whisper_zh": {
                "no_speech_threshold": 0.6,
                "compression_ratio_threshold": 1.8,
                "temperature": [0.0, 0.2, 0.4],
                "beam_size": 5,
                "nsp_skip": 0.85,
                "logprob_skip": -1.5,
            },
            "whisper_en": {
                "no_speech_threshold": 0.55,
                "compression_ratio_threshold": 1.35,
                "temperature": [0.0, 0.2],
                "beam_size": 5,
                "nsp_skip": 0.35,
                "logprob_skip": -0.7
            }
        }

        def _deep_merge(base, override):
            """將 override 合併進 base；dict 類型遞迴補齊缺少的 key，其餘直接用 override 的值"""
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
                # 若合併後與存檔不同（代表有新 key 被補進來），立即回寫讓 json 保持最新
                if self.config != saved:
                    self.save_config()
            except Exception as e:
                self.log(f"⚠️ 讀取設定檔失敗，使用預設設定: {str(e)}")
                self.config = default_config
                self.save_config()
        else:
            self.config = default_config
            self.save_config()

    def update_ktv_color_previews(self):
        """更新 KTV 字幕顏色預覽"""
        try:
            if hasattr(self, 'ktv_mode_hint_label') and hasattr(self, 'ktv_color_mode_var'):
                mode = self.ktv_color_mode_var.get()
                if mode == "slide":
                    self.ktv_mode_hint_label.config(text="  💡 滑動漸變：使用 \\kf tag，顏色由左至右平滑掃過，視覺效果更流暢")
                else:
                    self.ktv_mode_hint_label.config(text="  💡 逐字變色：使用 \\k tag，每個字唱完後瞬間切換顏色")

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

                lyrics_text = "哥哥爸爸真偉大"

                selected_font = getattr(self, 'ktv_font_var', tk.StringVar(value="微軟正黑體")).get()
                font = (selected_font, 28, "bold")

                full_unsung_id = canvas.create_text( 380, 40, text=lyrics_text, font=font, fill=self.ktv_unplayed_color_var.get(), anchor=tk.CENTER )
                full_bbox = canvas.bbox(full_unsung_id)

                if full_bbox:
                    canvas.create_rectangle(
                        full_bbox[0]-3, full_bbox[1]-3,
                        full_bbox[2]+3, full_bbox[3]+3,
                        outline=self.ktv_border_color_var.get(),
                        width=3,
                        fill=""
                    )

                    mid_x = full_bbox[0] + (full_bbox[2] - full_bbox[0]) // 2

                    clip_rect = canvas.create_rectangle( full_bbox[0], full_bbox[1], mid_x, full_bbox[3], outline="", tags="clip" )

                    sung_id = canvas.create_text( 380, 40, text=lyrics_text, font=font, fill=self.ktv_played_color_var.get(), anchor=tk.CENTER, tags=("sung",) )

                    canvas.create_rectangle( mid_x, full_bbox[1], full_bbox[2], full_bbox[3], fill="#333333", outline="" )

                    right_unsung_id = canvas.create_text( 380, 40, text=lyrics_text, font=font, fill=self.ktv_unplayed_color_var.get(), anchor=tk.CENTER )

                    canvas.create_rectangle( full_bbox[0], full_bbox[1], mid_x, full_bbox[3], fill="#333333", outline="" )

                    unplayed_border = self.ktv_border_color_var.get()
                    played_border = unplayed_border
                    if hasattr(self, 'ktv_played_border_color_var'):
                        played_border = self.ktv_played_border_color_var.get()

                    x1, y1 = full_bbox[0]-3, full_bbox[1]-3
                    x2, y2 = full_bbox[2]+3, full_bbox[3]+3
                    mid = x1 + (x2 - x1) / 2

                    canvas.create_rectangle(x1, y1, x2, y2, outline=unplayed_border, width=3, fill="")

                    if played_border and played_border != unplayed_border:
                        canvas.create_line(x1, y1, mid, y1, fill=played_border, width=3)
                        canvas.create_line(x1, y2, mid, y2, fill=played_border, width=3)
                        canvas.create_line(x1, y1, x1, y2, fill=played_border, width=3)

        except Exception as e:
            pass

    def format_time(self, seconds):
        """格式化時間顯示為 MM:SS"""
        try:
            s = float(seconds); return f"{int(s//60):02d}:{int(s%60):02d}"
        except: return "00:00"

    def get_video_duration(self, video_path):
        """使用 FFmpeg 取得影片長度"""
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            if not ffmpeg_exe.exists(): return 0
            result = subprocess.run([str(ffmpeg_exe), "-i", video_path, "-f", "null", "-"],
                                    capture_output=True, creationflags=self.subp_flags)
            m = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})",
                          result.stderr.decode('utf-8', errors='replace'))
            if m:
                return int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/100
        except Exception:
            pass
        return 0

    def _get_video_size(self, video_path):
        """使用 FFmpeg 取得影片解析度，回傳 (width, height)；失敗回傳 (None, None)"""
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            if not ffmpeg_exe.exists():
                return None, None
            result = subprocess.run(
                [str(ffmpeg_exe), "-i", str(video_path)],
                capture_output=True, creationflags=self.subp_flags
            )
            stderr = result.stderr.decode('utf-8', errors='replace')
            # 解析 "Video: ... 1920x1080" 或 "Video: ... 1080x1080"
            m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", stderr)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return None, None

    def parse_lyrics(self, subtitle_path):
        """解析歌詞檔案（支援 SRT 和 JSON）"""
        lyrics = []
        try:
            if not subtitle_path or not os.path.exists(subtitle_path):
                return lyrics

            if subtitle_path.lower().endswith('.json'):
                with open(subtitle_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    lyrics = data
                elif isinstance(data, dict):
                    if 'lyrics' in data:
                        lyrics = data['lyrics']
                    elif 'segments' in data:
                        lyrics = data['segments']
            elif subtitle_path.lower().endswith('.srt'):
                with open(subtitle_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                srt_pattern = re.compile( r'(\d+)\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\n(.*?)(?=\n\n|\Z)', re.DOTALL )

                for match in srt_pattern.finditer(content):
                    start_h = int(match.group(2))
                    start_m = int(match.group(3))
                    start_s = int(match.group(4))
                    start_ms = int(match.group(5))
                    start_time = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000

                    end_h = int(match.group(6))
                    end_m = int(match.group(7))
                    end_s = int(match.group(8))
                    end_ms = int(match.group(9))
                    end_time = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000

                    text = match.group(10).strip()
                    if text:
                        lyrics.append({ 'start': start_time, 'end': end_time, 'text': text })
        except Exception as e:
            pass
        return lyrics

    def reload_player(self):
        """重新載入影片和歌詞"""
        try:
            if self.is_playing:
                self.toggle_play()

            if self.ffplay_process:
                try:
                    self.ffplay_process.terminate()
                except:
                    pass
                self.ffplay_process = None

            self.current_time = 0
            self.lyrics_data = []

            video_path = self.merge_video_path_var.get().strip()
            subtitle_path = self.merge_subtitle_path_var.get().strip()

            self.video_canvas.delete("all")
            self.video_canvas.create_text(320, 140, text="請選擇影片檔案", fill="#666666", font=("Arial", 14))
            self.video_canvas.create_text(320, 170, text="影片將在獨立視窗播放", fill="#888888", font=("Arial", 10))

            if video_path and os.path.exists(video_path):
                self.player_video_path = video_path
                self.video_duration = self.get_video_duration(video_path)
                if self.video_duration > 0:
                    self.timeline_scale.config(to=self.video_duration)
                    self.video_canvas.delete("all")
                    self.video_canvas.create_rectangle(0, 0, 640, 320, fill="#000000", outline="")
                    self.video_canvas.create_text(320, 130, text=os.path.basename(video_path), fill="#FFFFFF", font=("Arial", 11))
                    self.video_canvas.create_text(320, 160, text="(點擊播放開始預覽)", fill="#888888", font=("Arial", 9))
                    self.video_canvas.create_text(320, 190, text="影片將在獨立視窗播放", fill="#666666", font=("Arial", 9))

            if subtitle_path and os.path.exists(subtitle_path):
                self.player_subtitle_path = subtitle_path
                self.lyrics_data = self.parse_lyrics(subtitle_path)
                if hasattr(self, 'log') and self.lyrics_data:
                    self.log(f"✅ 已載入 {len(self.lyrics_data)} 句歌詞")
            else:
                self.lyrics_data = [
                    {'start': 0, 'end': 5, 'text': '哥哥爸爸真偉大'},
                    {'start': 5, 'end': 10, 'text': '榮譽都屬於他'},
                    {'start': 10, 'end': 15, 'text': '為國家去打仗'},
                    {'start': 15, 'end': 20, 'text': '我們都愛他'}
                ]
                if self.video_duration <= 0:
                    self.video_duration = 20
                    self.timeline_scale.config(to=self.video_duration)

            self.update_time_display()

            self.display_lyrics_at_time(0)

        except Exception as e:
            pass

    def toggle_play(self):
        """切換播放/暫停"""
        if not self.player_video_path or not os.path.exists(self.player_video_path):
            messagebox.showwarning("提示", "請先選擇影片檔案！")
            return

        if self.is_playing:
            self.is_playing = False
            self.play_btn.config(text="▶ 播放", bg=self.ui_colors['success'])
            self.stop_playback()
        else:
            self.is_playing = True
            self.play_btn.config(text="⏸ 暫停", bg=self.ui_colors['warning'])
            self.start_playback()

    def start_playback(self):
        """開始播放 - 使用 ffplay"""
        ffplay_exe = self.bin_dir / "ffplay.exe"
        if self.player_after_id:
            self.root.after_cancel(self.player_after_id)
        if not ffplay_exe.exists():
            messagebox.showwarning("提示", "找不到 ffplay.exe，請確認 FFmpeg 已正確安裝！\n(我們會繼續顯示歌詞預覽)")
        else:
            try:
                self._kill_ffplay()
                self.ffplay_process = subprocess.Popen(
                    [str(ffplay_exe), "-ss", str(self.current_time), "-autoexit", "-window_title", "KTV 影片預覽", str(self.player_video_path)],
                    creationflags=self.subp_flags)
            except Exception as e:
                messagebox.showerror("錯誤", f"無法啟動播放器：{str(e)}\n(我們會繼續顯示歌詞預覽)")
        self.update_playback()

    def _kill_ffplay(self):
        if self.ffplay_process:
            try: self.ffplay_process.terminate()
            except: pass
            self.ffplay_process = None

    def stop_playback(self):
        self._kill_ffplay()
        if self.player_after_id:
            self.root.after_cancel(self.player_after_id)
            self.player_after_id = None

    def update_playback(self):
        if not self.is_playing: return
        self.current_time += 0.1
        if self.current_time >= self.video_duration:
            self.current_time = 0; self.toggle_play(); return
        self.update_time_display()
        self.timeline_scale.set(self.current_time)
        self.display_lyrics_at_time(self.current_time)
        self.player_after_id = self.root.after(100, self.update_playback)

    def update_time_display(self):
        self.time_label_var.set(f"{self.format_time(self.current_time)} / {self.format_time(self.video_duration)}")

    def on_timeline_seek(self, event):
        self.current_time = self.timeline_scale.get()
        self.update_time_display(); self.display_lyrics_at_time(self.current_time)
        if self.is_playing: self.restart_player_at_time(self.current_time)

    def on_timeline_drag(self, event):
        self.current_time = self.timeline_scale.get()
        self.update_time_display(); self.display_lyrics_at_time(self.current_time)

    def restart_player_at_time(self, new_time):
        try:
            ffplay_exe = self.bin_dir / "ffplay.exe"
            if not ffplay_exe.exists(): return
            self._kill_ffplay()
            self.ffplay_process = subprocess.Popen(
                [str(ffplay_exe), "-ss", str(new_time), "-autoexit", "-window_title", "KTV 影片預覽", str(self.player_video_path)],
                creationflags=self.subp_flags)
        except Exception:
            pass

    def display_lyrics_at_time(self, current_time):
        """根據時間顯示對應歌詞"""
        try:
            canvas = self.lyrics_display_canvas
            canvas.delete("all")

            canvas.create_rectangle(0, 0, 640, 70, fill="#333333", outline="")

            current_lyric = None
            next_lyric = None

            for i, lyric in enumerate(self.lyrics_data):
                start = float(lyric.get('start', 0))
                end = float(lyric.get('end', 0))

                if start <= current_time <= end:
                    current_lyric = lyric
                    if i + 1 < len(self.lyrics_data):
                        next_lyric = self.lyrics_data[i + 1]
                    break

            if not current_lyric and self.lyrics_data:
                for lyric in self.lyrics_data:
                    if float(lyric.get('start', 0)) <= current_time:
                        current_lyric = lyric

            if current_lyric:
                text = current_lyric.get('text', '')

                def _gv(attr, default):
                    v = getattr(self, attr, None)
                    return v.get() if v else default
                selected_font  = _gv('ktv_font_var',          '微軟正黑體')
                unplayed_color = _gv('ktv_unplayed_color_var', '#FFFFFF')
                played_color   = _gv('ktv_played_color_var',   '#0000FF')
                border_color   = _gv('ktv_border_color_var',   '#000000')

                font = (selected_font, 24, "bold")

                start_time = float(current_lyric.get('start', 0))
                end_time = float(current_lyric.get('end', 0))
                duration = end_time - start_time if end_time > start_time else 1
                progress = min(max((current_time - start_time) / duration, 0), 1)

                display_color = unplayed_color

                p_squared = progress * progress
                threshold = 0.25
                is_over = False

                check_val = p_squared - threshold
                abs_val = abs(check_val)
                if abs_val == check_val:
                    is_over = True

                if is_over:
                    display_color = played_color

                canvas.create_text( 320, 35, text=text, font=font, fill=display_color, anchor=tk.CENTER )
            else:
                canvas.create_text(320, 35, text="請選擇歌詞檔案", fill="#666666", font=("Arial", 12))

        except Exception as e:
            if hasattr(self, 'log'):
                self.log(f"⚠️ 顯示歌詞時錯誤: {str(e)}")

    def save_config(self):
        """儲存設定到 JSON 檔案"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.log(f"⚠️ 儲存設定檔失敗: {str(e)}")

    def setup_whisper_settings_tab(self, parent):
        """建立 Whisper 辨識設定籤頁"""

        C = self.ui_colors

        DEFAULTS_ZH = {
            "no_speech_threshold": 0.6,
            "compression_ratio_threshold": 1.8,
            "temperature": [0.0, 0.2, 0.4],
            "beam_size": 5,
            "nsp_skip": 0.85,
            "logprob_skip": -1.5,
        }
        DEFAULTS_EN = {
            "no_speech_threshold": 0.55,
            "compression_ratio_threshold": 1.35,
            "temperature": [0.0, 0.2],
            "beam_size": 5,
            "nsp_skip": 0.35,
            "logprob_skip": -0.7,
        }

        cur_zh = self.config.get("whisper_zh", DEFAULTS_ZH)
        cur_en = self.config.get("whisper_en", DEFAULTS_EN)

        _COMMON_PARAMS = [
            ("no_speech_threshold",        "靜音略過靈敏度",       "↑ 調高：輕聲演唱常被跳過\n↓ 調低：靜音段落有雜音殘留",  0.1, 0.95, 0.05, True),
            ("compression_ratio_threshold","重複幻覺過濾強度",      "↑ 調高：副歌歌詞常被漏掉\n↓ 調低：出現大量重複幻覺歌詞",  1.0, 3.0,  0.1,  True),
            ("beam_size",                  "辨識精準度（候選數量）", "↑ 調高：辨識準確但速度慢\n↓ 調低：電腦慢或辨識很久",       1,   10,   1,    False),
            ("nsp_skip",                   "靜音略過門檻",          "↑ 調高：輕聲段落常被跳過\n↓ 調低：靜音段落有殘留",        0.1, 0.9,  0.05, True),
        ]
        ZH_PARAMS = _COMMON_PARAMS + [("logprob_skip", "辨識信心下限", "↓ 調低（如 -2.0）：正常歌詞常被誤刪\n↑ 調高（如 -1.0）：很多不確定歌詞殘留", -2.0, -0.1, 0.1, True)]
        EN_PARAMS = _COMMON_PARAMS + [("logprob_skip", "辨識信心下限", "↓ 調低（如 -1.0）：正常歌詞常被誤刪\n↑ 調高（如 -0.5）：很多不確定歌詞殘留", -2.0, -0.1, 0.1, True)]

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

        tk.Label(scroll_frame, text="⚙️  Whisper 辨識參數設定", font=("Arial", 13, "bold"), fg=C['primary'], bg=C['bg'] ).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(scroll_frame, text="調整後點「儲存設定」即生效，下次辨識自動套用。", font=("Arial", 9), fg="gray", bg=C['bg'] ).pack(anchor="w", padx=16, pady=(0, 6))

        # ── 小白提示框 ──────────────────────────────────────────
        tip_box = tk.Frame(scroll_frame, bg="#EFF6FF", relief="solid", bd=1)
        tip_box.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(tip_box,
                 text="💡  一般使用者：直接點右下角「還原預設值」即可，不需要修改任何設定。",
                 font=("Arial", 9, "bold"), fg=C['primary'], bg="#EFF6FF", anchor="w"
                 ).pack(anchor="w", padx=10, pady=(6, 2))
        tk.Label(tip_box,
                 text="如果辨識結果有問題，才需要參考下方的「症狀對照表」微調。",
                 font=("Arial", 8), fg="#555", bg="#EFF6FF", anchor="w"
                 ).pack(anchor="w", padx=10, pady=(0, 6))

        # ── 症狀對照表 ───────────────────────────────────────────
        sym_box = tk.Frame(scroll_frame, bg="#F8FAFC", relief="solid", bd=1)
        sym_box.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(sym_box, text="🔍  遇到問題時才看這裡", font=("Arial", 9, "bold"), fg=C['fg'], bg="#F8FAFC"
                 ).pack(anchor="w", padx=10, pady=(7, 4))

        SYMPTOMS = [
            ("副歌重複的歌詞常被漏掉",   "重複幻覺過濾強度",  "調高，例如 1.8 → 2.2（中）/ 1.35 → 1.8（英）"),
            ("輕聲演唱的段落常被跳過",   "靜音略過靈敏度",    "調高，例如 0.6 → 0.75"),
            ("電腦很慢、辨識很久",       "辨識精準度",        "調低，例如 5 → 3"),
            ("正常歌詞一直被誤刪",       "辨識信心下限",      "調低，例如 -0.7 → -1.0"),
        ]

        # 表頭
        hdr = tk.Frame(sym_box, bg="#E2E8F0")
        hdr.pack(fill="x", padx=8, pady=(0, 2))
        for col_text, col_w in [("症狀", 22), ("調整哪個設定", 22), ("怎麼調", 20)]:
            tk.Label(hdr, text=col_text, font=("Arial", 8, "bold"), fg=C['fg'],
                     bg="#E2E8F0", width=col_w, anchor="w"
                     ).pack(side="left", padx=(4, 0), pady=3)

        # 表格列
        for i, (symptom, setting, action) in enumerate(SYMPTOMS):
            row_bg = "#F8FAFC" if i % 2 == 0 else "#F1F5F9"
            r = tk.Frame(sym_box, bg=row_bg)
            r.pack(fill="x", padx=8)
            for col_text, col_w in [(symptom, 22), (setting, 22), (action, 20)]:
                tk.Label(r, text=col_text, font=("Arial", 8), fg=C['fg'],
                         bg=row_bg, width=col_w, anchor="w"
                         ).pack(side="left", padx=(4, 0), pady=2)

        def _make_section(title, params, cfg_dict, var_dict, defaults):
            sep = tk.Frame(scroll_frame, bg=C['border'], height=1)
            sep.pack(fill="x", padx=16, pady=(8, 0))
            tk.Label(scroll_frame, text=title, font=("Arial", 11, "bold"), fg=C['primary'], bg=C['bg'] ).pack(anchor="w", padx=16, pady=(6, 4))

            for key, label, desc, vmin, vmax, step, is_float in params:
                cur_val = cfg_dict.get(key, defaults[key])

                row = tk.Frame(scroll_frame, bg=C['bg'])
                row.pack(fill="x", **PAD)

                left = tk.Frame(row, bg=C['bg'])
                left.pack(side="left", fill="x", expand=True)

                tk.Label(left, text=label, font=("Arial", 10, "bold"), fg=C['fg'], bg=C['bg'], anchor="w", width=20 ).pack(side="left")

                var = tk.StringVar(value=str(cur_val))
                var_dict[key] = var

                entry = tk.Entry(left, textvariable=var, width=8, font=("Arial", 10), relief="solid", bd=1)
                entry.pack(side="left", padx=(4, 8))

                suggest = defaults[key]
                tk.Label(left, text=f"建議：{suggest}", font=("Arial", 8), fg="gray", bg=C['bg'] ).pack(side="left")

                tk.Label(row, text=desc, font=("Arial", 8), fg="#555", bg=C['bg'], justify="left", anchor="nw", wraplength=220 ).pack(side="left", padx=(8, 0))

        _make_section("🈳  中文辨識參數", ZH_PARAMS, cur_zh, self._wsp_vars_zh, DEFAULTS_ZH)
        _make_section("🔤  英文辨識參數", EN_PARAMS, cur_en, self._wsp_vars_en, DEFAULTS_EN)

        sep2 = tk.Frame(scroll_frame, bg=C['border'], height=1)
        sep2.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(scroll_frame, text="🌡️  重試創意程度（溫度序列）", font=("Arial", 11, "bold"), fg=C['primary'], bg=C['bg'] ).pack(anchor="w", padx=16, pady=(6, 2))
        tk.Label(scroll_frame, text=("辨識失敗時自動重試的「創意程度」序列，用逗號分隔。\n"
                                     "↑ 加大數值：辨識結果太保守、常辨識失敗時\n"
                                     "↓ 減少數值：辨識結果亂跳、出現奇怪歌詞時"),
                 font=("Arial", 8), fg="#555", bg=C['bg'], justify="left" ).pack(anchor="w", padx=16, pady=(0, 4))

        temp_row = tk.Frame(scroll_frame, bg=C['bg'])
        temp_row.pack(fill="x", padx=16, pady=3)

        tk.Label(temp_row, text="中文溫度：", font=("Arial", 10), fg=C['fg'], bg=C['bg'] ).pack(side="left")
        zh_temp_init = ", ".join(str(v) for v in cur_zh.get("temperature", DEFAULTS_ZH["temperature"]))
        self._wsp_temp_zh = tk.StringVar(value=zh_temp_init)
        tk.Entry(temp_row, textvariable=self._wsp_temp_zh, width=18, font=("Arial", 10), relief="solid", bd=1 ).pack(side="left", padx=(4, 20))

        tk.Label(temp_row, text="英文溫度：", font=("Arial", 10), fg=C['fg'], bg=C['bg'] ).pack(side="left")
        en_temp_init = ", ".join(str(v) for v in cur_en.get("temperature", DEFAULTS_EN["temperature"]))
        self._wsp_temp_en = tk.StringVar(value=en_temp_init)
        tk.Entry(temp_row, textvariable=self._wsp_temp_en, width=14, font=("Arial", 10), relief="solid", bd=1 ).pack(side="left", padx=4)

        sep3 = tk.Frame(scroll_frame, bg=C['border'], height=1)
        sep3.pack(fill="x", padx=16, pady=(12, 0))

        btn_row = tk.Frame(scroll_frame, bg=C['bg'])
        btn_row.pack(anchor="e", padx=16, pady=10)

        def _parse_temp(s):
            return [float(x.strip()) for x in s.split(",") if x.strip()]

        def _save_whisper_settings():
            try:
                zh = {}
                for key, var in self._wsp_vars_zh.items():
                    dv = DEFAULTS_ZH[key]
                    zh[key] = float(var.get()) if isinstance(dv, float) else int(var.get())
                zh["temperature"] = _parse_temp(self._wsp_temp_zh.get())

                en = {}
                for key, var in self._wsp_vars_en.items():
                    dv = DEFAULTS_EN[key]
                    en[key] = float(var.get()) if isinstance(dv, float) else int(var.get())
                en["temperature"] = _parse_temp(self._wsp_temp_en.get())

                self.config["whisper_zh"] = zh
                self.config["whisper_en"] = en
                self.config["use_stable_ts"] = self.use_stable_ts_var.get()
                self.save_config()
                save_btn.config(text="✅ 已儲存！")
                self.root.after(2000, lambda: save_btn.config(text="💾  儲存設定"))
            except ValueError as e:
                messagebox.showerror("格式錯誤", f"請確認所有欄位為數字。\n{e}")

        def _reset_defaults():
            if not messagebox.askyesno("還原預設", "確定要還原所有 Whisper 參數為預設值嗎？"):
                return
            for key, var in self._wsp_vars_zh.items():
                var.set(str(DEFAULTS_ZH[key]))
            self._wsp_temp_zh.set(", ".join(str(v) for v in DEFAULTS_ZH["temperature"]))
            for key, var in self._wsp_vars_en.items():
                var.set(str(DEFAULTS_EN[key]))
            self._wsp_temp_en.set(", ".join(str(v) for v in DEFAULTS_EN["temperature"]))
            self.config["whisper_zh"] = dict(DEFAULTS_ZH)
            self.config["whisper_en"] = dict(DEFAULTS_EN)
            self.config["use_stable_ts"] = True
            self.use_stable_ts_var.set(True)
            self.save_config()
            save_btn.config(text="✅ 已還原並儲存！")
            self.root.after(2000, lambda: save_btn.config(text="💾  儲存設定"))

        tk.Button(btn_row, text="🔄  還原預設值", command=_reset_defaults, bg=C['warning'], fg='white', font=("Arial", 10, "bold"), relief='flat', padx=14, pady=6, cursor='hand2' ).pack(side="left", padx=(0, 8))

        save_btn = tk.Button(btn_row, text="💾  儲存設定",
                             command=_save_whisper_settings,
                             bg=C['success'], fg='white',
                             font=("Arial", 10, "bold"),
                             relief='flat', padx=14, pady=6, cursor='hand2')
        save_btn.pack(side="left")

    def find_separated_files(self, input_file, output_dir, fmt):
        """尋找分離後的人聲和伴奏檔案"""
        input_stem = Path(input_file).stem
        out_path = Path(output_dir)
        vocal_file = None
        instrumental_file = None

        try:
            for fname in os.listdir(str(out_path)):
                if fname.startswith(input_stem) and fname.endswith(f".{fmt}"):
                    if "(Vocals)" in fname:
                        vocal_file = str(out_path / fname)
                    elif "(Instrumental)" in fname or "(No Vocals)" in fname:
                        instrumental_file = str(out_path / fname)
        except Exception as e:
            self.log(f"  ⚠️ 尋找分離檔案時出錯: {str(e)}")

        return vocal_file, instrumental_file

    def create_lr_stereo(self, instrumental_file, vocal_file, output_file):
        """創建左伴奏/右（人聲+伴奏）立體聲 MP3"""
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            cmd = [
                str(ffmpeg_exe), "-y",
                "-i", str(instrumental_file),
                "-i", str(vocal_file),
                "-filter_complex",
                "[0:a]pan=mono|c0=0.5*c0+0.5*c1[inst_mono];"
                "[inst_mono]asplit=2[inst_a][inst_b];"
                "[1:a]pan=mono|c0=0.5*c0+0.5*c1[voc_mono];"
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
            self.log(f"  ❌ 創建左伴奏/右（人聲+伴奏）立體聲失敗: {str(e)}")
            return False

    def browse_output_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.output_dir_var.set(directory)
            self.config["output_dir"] = directory
            self.save_config()

    def update_status(self, text, color="blue"):
        self.root.after(0, lambda: (self.status_var.set(f"狀態: {text}"), self.status_label.config(fg=color)))

    def _get_target_ai_dir(self, install_mode="auto"):
        """根據安裝模式決定主要 AI 套件目錄。"""
        if install_mode == "cpu": return self.lib_dir
        if install_mode == "gpu": return self.gpu_lib_dir
        return self.gpu_lib_dir if self._is_nvidia_gpu_present() else self.lib_dir

    def _resolve_device(self, device_val=None):
        """將 UI device 字串 ('gpu'/'directml'/'cpu') 轉為 AI 推理裝置名稱"""
        val = device_val or self.device_var.get()
        if val == "gpu":
            return "cuda"
        if val == "directml":
            return "directml"
        return "cpu"

    def _get_runtime_ai_dir(self, device=None):
        target = device or self.device_var.get()
        return self.gpu_lib_dir if target in ("gpu","cuda") else (self.directml_lib_dir if target == "directml" else self.lib_dir)

    def _has_onnxruntime_package(self, target_dir):
        """檢查指定 AI 套件目錄是否已有 onnxruntime 核心包（非僅 metadata）。"""
        try:
            pkg_dir = target_dir / "onnxruntime"
            return pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
        except Exception:
            return False

    @staticmethod
    def _p(path):
        """將 Path/str 轉成 Python 嵌入腳本用的正斜線路徑字串"""
        return str(path).replace("\\", "/")

    def _build_python_env(self, lib_dir, include_gpu_runtime=False):
        """依據 CPU / GPU 模式建立隔離的 Python 執行環境。"""
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

        deduped = []
        seen = set()
        for p in search_paths:
            if p and p not in seen:
                deduped.append(p)
                seen.add(p)

        env["PATH"] = os.pathsep.join(deduped) + os.pathsep + env.get("PATH", "")
        return env

    def _probe_onnxruntime_stack(self, lib_dir, expect_gpu=False):
        """快速檢查指定套件目錄內的 ONNX Runtime 是否可正常使用。"""
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

# numpy 的 DLL 搜尋：numpy.libs 和 numpy/core 必須在 PATH 最前面，讓 Windows loader 能找到
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
        # 增加診斷資訊：若找不到模組，輸出當前 sys.path 的前幾個項目
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
                err = (res.stderr or "").strip().replace("\n", " ")
                err = err[:240]
                return f"ORT_PROC_FAIL:rc={res.returncode}:stderr={err}"
            return "ORT_NO_OUTPUT"
        except subprocess.TimeoutExpired:
            return "ORT_TIMEOUT"
        except Exception as e:
            return f"ORT_ERR:{str(e)}"

    def _ensure_runtime_stack_ready(self, device):
        """
        確保執行前的 AI 核心可用。
        - GPU/DirectML 不可用時，先詢問是否要安裝，才決定是否回退到 CPU
        - CPU 缺件或損壞時，自動嘗試補裝 CPU 核心並重試一次
        回傳: (is_ready, actual_device, runtime_lib_dir)
        """
        is_gpu = (device == "cuda")
        is_directml = (device == "directml")
        runtime_lib_dir = self._get_runtime_ai_dir(device)
        if is_gpu:
            expected = "ORT_OK_GPU"
            diag_out = self._probe_onnxruntime_stack(runtime_lib_dir, expect_gpu=is_gpu)
        else:
            expected = "ORT_OK_CPU"
            diag_out = self._probe_onnxruntime_stack(runtime_lib_dir, expect_gpu=False)
        self.log(f"🔍 運算環境診斷: {diag_out}")

        if diag_out == expected:
            return True, device, runtime_lib_dir

        if is_gpu or is_directml:
            mode_name = "GPU" if is_gpu else "DirectML"
            msg = f"偵測到 {mode_name} AI 核心尚未就緒！\n\n是否要現在安裝 {mode_name} 元件？\n（按「否」會自動切換至 CPU 模式）"
            install_now = messagebox.askyesno(f"安裝 {mode_name} 元件", msg)

            if install_now:
                self.log(f"🛠️ 正在安裝 {mode_name} AI 核心...")
                self.update_status(f"正在安裝 {mode_name} AI 核心...", "orange")

                install_mode = "gpu" if is_gpu else "directml"
                if self.install_packages_locally(install_mode=install_mode):
                    retry_lib_dir = self.gpu_lib_dir if is_gpu else self.directml_lib_dir
                    retry_expect_gpu = is_gpu
                    retry_out = self._probe_onnxruntime_stack(retry_lib_dir, expect_gpu=retry_expect_gpu)
                    self.log(f"🔁 {mode_name} 安裝後再次診斷: {retry_out}")

                    if (is_gpu and retry_out == "ORT_OK_GPU") or (not is_gpu and retry_out == "ORT_OK_CPU"):
                        self.log(f"✅ {mode_name} AI 核心已安裝完成，繼續執行音訊分離。")
                        return True, device, retry_lib_dir
                    else:
                        self.log(f"❌ {mode_name} 核心安裝後仍無法正常載入：{retry_out}")
                else:
                    self.log(f"❌ {mode_name} AI 核心安裝失敗。")

                fallback_msg = f"{mode_name} 核心安裝失敗！\n\n是否要切換至 CPU 模式繼續？"
                if messagebox.askyesno(f"{mode_name} 安裝失敗", fallback_msg):
                    self.log(f"⚠️ 已切換至獨立 CPU 核心繼續執行。")
                    self.root.after(0, lambda: self.device_var.set("cpu"))
                    self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
                    return self._ensure_runtime_stack_ready("cpu")
                else:
                    return False, device, runtime_lib_dir
            else:
                self.log(f"⚠️ 使用者取消安裝 {mode_name} 元件，已切換至獨立 CPU 核心繼續執行。")
                self.root.after(0, lambda: self.device_var.set("cpu"))
                self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
                return self._ensure_runtime_stack_ready("cpu")

        repairable_tokens = ["ORT_ERR", "STACK_MISSING", "ORT_DLL_FAIL", "ORT_NO_OUTPUT", "ORT_TIMEOUT"]
        if any(token in diag_out for token in repairable_tokens):
            self.log("🛠️ 偵測到 CPU AI 核心缺失或損壞，正在自動補齊必要組件...")
            self.update_status("正在修復 CPU AI 核心...", "orange")

            if self.install_packages_locally(install_mode="cpu"):
                retry_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
                self.log(f"🔁 CPU 修復後再次診斷: {retry_out}")
                if retry_out == "ORT_OK_CPU":
                    self.log("✅ CPU AI 核心已自動修復完成，繼續執行音訊分離。")
                    return True, "cpu", self.lib_dir

                self.log(f"❌ CPU 核心修復後仍無法正常載入：{retry_out}")
            else:
                self.log("❌ 自動修復 CPU AI 核心失敗。")

        self.log("❌ CPU 核心無法正常載入，請執行「一鍵修復/初始化環境」。")
        return False, "cpu", self.lib_dir

    def _schedule_ort_fix_prompt(self, issue_key="gpu_runtime_fallback", delay_ms=0):
        """統一排程修復提示，避免重複排入事件佇列。"""
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            self.log(f"ℹ️ [PROMPT] 已略過修復提示（本次已拒絕）: {issue_key}")
            return
        if self._ort_fix_prompt_active:
            self.log(f"ℹ️ [PROMPT] 修復提示顯示中，略過重複請求: {issue_key}")
            return
        if self._ort_fix_prompt_pending:
            self.log(f"ℹ️ [PROMPT] 修復提示已排程，略過重複請求: {issue_key}")
            return
        if issue_key in self._ort_fix_prompt_shown_keys:
            self.log(f"ℹ️ [PROMPT] 修復提示本次已顯示過，略過: {issue_key}")
            return

        self._ort_fix_prompt_pending = True
        self.log(f"ℹ️ [PROMPT] 已排程修復提示: {issue_key} ({delay_ms}ms)")

        def _fire():
            self._ort_fix_prompt_after_id = None
            self._ort_fix_prompt_pending = False
            self._prompt_ort_fix(issue_key=issue_key)

        self._ort_fix_prompt_after_id = self.root.after(delay_ms, _fire)

    def _reset_ort_fix_prompt_state(self, clear_history=False):
        """清理修復提示的排程與顯示狀態。"""
        if self._ort_fix_prompt_after_id is not None:
            try:
                self.root.after_cancel(self._ort_fix_prompt_after_id)
            except Exception:
                pass
            self._ort_fix_prompt_after_id = None

        self._ort_fix_prompt_pending = False
        self._ort_fix_prompt_active = False

        if clear_history:
            self._ort_fix_prompt_shown_keys.clear()
            self._ort_fix_prompt_suppressed_keys.clear()

        self.log(f"ℹ️ [PROMPT] 已重置修復提示狀態 clear_history={clear_history}")

    def check_gpu_env(self):
        self.log("\n---[開始 GPU 環境深度檢測] ---")

        if not self._is_nvidia_gpu_present():
            self.log("ℹ️ 系統目前未偵測到啟用的 NVIDIA 顯示卡。")
            self.log("💡 筆電使用者：請確認已插上電源，且系統已切換至獨立顯示卡（NVIDIA GPU）。")
            self.log("💡 若您的電腦沒有 NVIDIA 顯示卡，請使用 CPU 模式，這是正常狀態，無需修復。")
            messagebox.showinfo( "未偵測到 NVIDIA GPU", "目前系統未偵測到啟用的 NVIDIA 顯示卡。\n\n" "• 若您是筆電使用者，請插上電源後再試。\n" "• 若電腦沒有 NVIDIA 顯示卡，請直接使用 CPU 模式即可，不需要下載 GPU 組件。" )
            return

        if not self.local_python.exists():
            self.log("[ERROR] 內建 Python 核心尚未安裝，無法進行檢測。")
            if messagebox.askyesno("初始化環境", "偵測到環境尚未初始化，是否要現在開始下載並配置基礎環境？"):
                self.check_components(prompt=True)
            return

        gpu_lib_dir = self.gpu_lib_dir
        env = self._build_python_env(gpu_lib_dir, include_gpu_runtime=True)
        gpu_lib_dir_posix    = self._p(gpu_lib_dir)
        common_lib_dir_posix = self._p(self.common_lib_dir)

        check_script = f"""
import sys, os
# 使用正斜線避免 Windows 轉義問題
target_lib = r'{gpu_lib_dir_posix}'
common_lib = r'{common_lib_dir_posix}'
if common_lib not in sys.path:
    sys.path.insert(0, common_lib)
if target_lib not in sys.path:
    sys.path.insert(0, target_lib)

# 動態加入所有 NVIDIA 相關 DLL 目錄
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
    print(f'[OK] 可用運算提供者 (Providers): {{providers}}')

    if 'CUDAExecutionProvider' in providers:
        print('[SUCCESS] ONNX CUDA 提供者已就緒')
    else:
        print('[INFO] ONNX 找不到 CUDA 提供者')
except ImportError:
    print(f'[ERROR] 尚未安裝 onnxruntime 套件 (搜尋路徑: {{target_lib}})')
except Exception as e:
    err_str = str(e)
    if 'DLL' in err_str or 'dll' in err_str or '初始化' in err_str or 'initialization routine' in err_str:
        print('[ERROR] onnxruntime DLL 載入失敗：安裝的是 GPU 版本但缺少 CUDA 環境')
        print('[HINT] 請點擊「一鍵修復/初始化環境」重新安裝正確版本')
    else:
        print(f'[ERROR] ONNX 檢測出錯: {{err_str}}')

try:
    import torch
    libs_found.append('torch')
    print(f'[OK] PyTorch 版本: {{torch.__version__}}')
    print(f'[DEBUG] PyTorch 路徑: {{torch.__file__}}')
    if torch.cuda.is_available():
        try:
            # 嘗試進行一個簡單的運算以確保算力相容
            test_tensor = torch.zeros(1).cuda()
            print(f'[OK] PyTorch CUDA 是否可用: True')
            print(f'[OK] 偵測到 GPU: {{torch.cuda.get_device_name(0)}}')
        except Exception as e:
            print(f'[ERROR] PyTorch 雖然偵測到 CUDA，但運算失敗 (可能是算力不相容): {{str(e)}}')
    else:
        if "+cpu" in torch.__version__:
            print('[INFO] 當前安裝的是 PyTorch CPU 版本，無法使用 GPU 加速')
        else:
            print('[INFO] PyTorch 偵測不到 CUDA，請檢查驅動程式')
except ImportError:
    print('[ERROR] 尚未安裝 torch 套件')
except Exception as e:
    print(f'[ERROR] PyTorch 檢測出錯: {{str(e)}}')

if not libs_found:
    print('[STATUS] 核心 AI 套件尚未安裝')
"""
        try:
            res = subprocess.run([str(self.local_python), "-c", check_script],
                                 capture_output=True, text=True, env=env,
                                 encoding='utf-8', errors='replace',
                                 creationflags=self.subp_flags)
            stdout_str = res.stdout.strip() if res.stdout else ""
            self.log(stdout_str)
            if res.stderr: self.log(f"[DEBUG] 錯誤資訊: {res.stderr.strip()}")

            libs_installed = "onnxruntime" in stdout_str and "torch" in stdout_str

            is_sm120_incompatible = "sm_120 is not compatible" in res.stderr

            cuda_ready = ("ONNX CUDA 提供者已就緒" in stdout_str) and \
                         ("PyTorch CUDA 是否可用: True" in stdout_str) and \
                         (not is_sm120_incompatible)

            if not cuda_ready:
                if not libs_installed:
                    self.log("\n💡 偵測到核心組件缺失 (Torch 或 ONNX)。")
                    msg = ("偵測到程式尚未安裝「AI 加速組件」或組件損壞。\n\n" "程式需要下載約 1.7GB 的加速庫才能發揮 GPU 效能。\n\n" "是否立即執行「一鍵全自動修復」？")
                    if messagebox.askyesno("一鍵修復", msg):
                        self._start_async_setup()
                    return
                else:
                    self.log("\n💡 偵測到 CUDA 加速環境配置不完全或不相容。")
                    if is_sm120_incompatible:
                        msg = ("偵測到您的 GPU (RTX 50 系列) 與當前 PyTorch 版本不相容。\n\n" "程式需要重新下載支援 Blackwell 架構的運算核心 (CUDA 12.6+)。\n\n" "是否立即執行「一鍵修復」？")
                    elif "PyTorch CUDA 是否可用: True" in stdout_str:
                        msg = ("您的 PyTorch 運作正常，但 ONNX 引擎尚未完全對接。\n\n" "是否讓程式自動嘗試修復 DLL 補丁？")
                    else:
                        if "運算失敗" in stdout_str:
                            msg = ("偵測到您的 GPU 與當前 AI 組件版本不相容。\n\n" "這通常是因為您的顯示卡太新，需要更新版本的運算核心。\n\n" "是否立即執行「一鍵修復」以下載最新的相容版本？")
                        else:
                            msg = ("偵測到您的系統 PyTorch 無法使用 GPU (當前可能是 CPU 版本)。\n\n" "是否立即執行「一鍵修復」以下載正確的 GPU 版本？")

                    if messagebox.askyesno("配置 CUDA 加速", msg):
                        self._start_async_setup()

        except Exception as e:
            self.log(f"[ERROR] 執行檢測失敗: {str(e)}")

        self.log("--- [檢測結束] ---\n")

    def _start_async_setup(self):
        if not self.is_processing:
            self.is_processing = True
            self.update_status("正在執行一鍵修復...", "orange")
            threading.Thread(target=self._async_setup_environment, daemon=True).start()

    def _render_component_row(self, comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, parent):
        """渲染單個元件列（緊湊版）"""
        pre_checked = is_essential and is_missing
        var = tk.BooleanVar(value=pre_checked)
        self.repair_component_vars[comp_id] = var

        row_frame = tk.Frame(parent, bg=self._bg)
        row_frame.pack(fill=tk.X, pady=2)

        left_frame = tk.Frame(row_frame, bg=self._bg)
        left_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        cb = tk.Checkbutton(left_frame, text=comp_name, variable=var, font=("Arial", 11, "bold"), bg=self._bg, selectcolor=self._bg)
        cb.pack(anchor="w")

        desc_label = tk.Label(left_frame, text=comp_desc, font=("Arial", 10), fg="#444", bg=self._bg)
        desc_label.pack(anchor="w", padx=(22, 0))

        right_frame = tk.Frame(row_frame, bg=self._bg)
        right_frame.pack(side=tk.RIGHT)

        ver = component_versions[comp_id]
        if ver:
            ver_label = tk.Label(right_frame, text=f"v{ver}", font=("Arial", 10), fg="#555", bg=self._bg)
            ver_label.pack(side=tk.RIGHT, padx=3)

        if is_missing:
            status_label = tk.Label(right_frame, text="⚠️ 缺少", fg="red", font=("Arial", 10, "bold"), bg=self._bg)
        else:
            status_label = tk.Label(right_frame, text="✅ 正常", fg="green", font=("Arial", 10, "bold"), bg=self._bg)
        status_label.pack(side=tk.RIGHT)

    def _detect_gpu_vendor(self):
        """偵測系統顯示卡廠商，回傳 ('nvidia', 'amd_intel', 'none') 之一"""
        # 先試 wmic，失敗再用 PowerShell（Windows 11 部分版本已移除 wmic）
        for cmd, parser in [
            (["wmic", "path", "win32_VideoController", "get", "Name"],
             lambda out: out.upper()),
            (["powershell", "-NoProfile", "-Command",
              "Get-PnpDevice -Class Display | Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName"],
             lambda out: out.upper()),
        ]:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                   creationflags=self.subp_flags,
                                   encoding="utf-8", errors="replace")
                if r.returncode == 0:
                    out = parser(r.stdout)
                    if "NVIDIA" in out:
                        return "nvidia"
                    if "AMD" in out or "RADEON" in out or "INTEL" in out:
                        return "amd_intel"
                    return "none"
            except Exception:
                pass
        return "none"

    def check_components(self, prompt=True, show_list=True):
        """更新環境修復標籤頁的元件列表"""
        if self.is_processing: return

        if show_list:
            for widget in self.repair_components_frame.winfo_children():
                widget.destroy()

            self.repair_component_vars = {}

        has_gpu = self._is_nvidia_gpu_present()

        has_nvidia_gpu_stack   = (self.gpu_lib_dir / "torch").exists() and self._has_onnxruntime_package(self.gpu_lib_dir)
        has_directml_stack     = self._has_onnxruntime_package(self.directml_lib_dir)
        has_cpu_stack          = (self.lib_dir / "torch").exists() and self._has_onnxruntime_package(self.lib_dir)

        has_amd_or_intel_gpu = False
        try:
            vendor = self._detect_gpu_vendor()
            if vendor == "amd_intel":
                has_amd_or_intel_gpu = True
        except Exception:
            pass

        ai_cpu_essential       = False
        ai_gpu_essential       = False
        ai_directml_essential  = False

        if has_gpu and not has_nvidia_gpu_stack:
            ai_gpu_essential = True
        elif not has_gpu and has_amd_or_intel_gpu and not has_directml_stack:
            ai_directml_essential = True
        elif not has_gpu and not has_amd_or_intel_gpu and not has_cpu_stack:
            ai_cpu_essential = True

        ai_common_essential = ai_cpu_essential or ai_gpu_essential or ai_directml_essential

        all_components = [
            ("python", "內建 Python 核心", "程式運行的基礎環境，所有功能都需要它", not self.local_python.exists(), True),
            ("ffmpeg", "音訊引擎 FFmpeg", "處理音訊和影片的轉檔、分離等核心功能", not (self.bin_dir / "ffmpeg.exe").exists(), True),
            ("ytdlp", "YouTube 下載器 yt-dlp", "用於從 YouTube 下載影片和音訊", not self._is_ytdlp_installed(), True),
            ("ai_common", "AI 共用函式庫 (audio-separator)", "AI 人聲分離的核心函式庫，負責分離人聲和伴奏", not (self.common_lib_dir / "audio_separator").exists(), ai_common_essential),
            ("ai_cpu", "AI CPU 運算核心 (PyTorch + ONNX Runtime)", "使用 CPU 進行 AI 運算，相容性最高但速度較慢", not has_cpu_stack, ai_cpu_essential),
            ("whisper", "Whisper AI 歌詞識別模型", "用於自動識別歌詞並產生 SRT 字幕檔案", not (self.whisper_models_dir.exists() and any(self.whisper_models_dir.iterdir())), False),
            ("stable_whisper", "stable-ts 時間軸精準對齊套件", "大幅提升 Whisper 逐字時間軸精度（±50ms），KTV 歌詞對齊必備", not self._is_stable_whisper_installed(), False),
            ("zhconv", "中文簡繁轉換庫 zhconv", "用於歌詞的簡體中文和繁體中文互相轉換", not self._is_zhconv_installed(), False),
        ]

        if has_gpu:
            all_components.extend([
                ("ai_gpu", "AI GPU 運算核心 (PyTorch + ONNX Runtime)", "使用 NVIDIA GPU 進行 AI 運算，速度最快", not has_nvidia_gpu_stack, ai_gpu_essential),
                ("ai_directml", "AI DirectML 運算核心", "使用 DirectML 進行 AI 運算，適用 AMD/Intel 顯卡", not has_directml_stack, ai_directml_essential),
            ])
        elif has_amd_or_intel_gpu:
            all_components.append( ("ai_directml", "AI DirectML 運算核心", "使用 DirectML 進行 AI 運算，適用 AMD/Intel 顯卡", not has_directml_stack, ai_directml_essential) )

        if not show_list:
            startup_ok = True
            if not self.local_python.exists() or not (self.bin_dir / "ffmpeg.exe").exists():
                startup_ok = False
            else:
                has_any_ai = False
                sep_exists = (self.common_lib_dir / "audio_separator").exists()
                if sep_exists:
                    if (self.lib_dir / "torch").exists() and self._has_onnxruntime_package(self.lib_dir):
                        has_any_ai = True
                    elif has_gpu and (self.gpu_lib_dir / "torch").exists() and self._has_onnxruntime_package(self.gpu_lib_dir):
                        has_any_ai = True
                    elif (self.directml_lib_dir / "onnxruntime").exists():
                        has_any_ai = True
                if not has_any_ai:
                    startup_ok = False

            if not prompt and startup_ok:
                self._check_ytdlp()
                threading.Thread(target=self._startup_ort_check, daemon=True).start()
                return

            if not prompt:
                if self._startup_component_prompt_shown:
                    self.log("ℹ️ 啟動修復提示本次已顯示過，略過重複彈窗。")
                    return
                self._startup_component_prompt_shown = True
                self.log("⚠️ 偵測到重要元件缺少，自動切換到「環境修復」頁籤...")
                self.root.after(0, lambda: self.switch_tab(6))
                return

        if not show_list:
            return

        loading_label = tk.Label( self.repair_components_frame, text="🔍 正在掃描元件狀態...", font=("Arial", 11), fg=self.ui_colors['primary'], bg=self._bg )
        loading_label.pack(pady=30)
        self.repair_components_frame.update_idletasks()

        def on_select_all():
            for var in self.repair_component_vars.values():
                var.set(True)

        def on_select_none():
            for var in self.repair_component_vars.values():
                var.set(False)

        def on_repair_click():
            selected = [cid for cid, var in self.repair_component_vars.items() if var.get()]
            if not selected:
                messagebox.showwarning("提示", "請至少選擇一個要修復的組件！")
                return
            self.is_processing = True
            self.update_status("正在修復選定的組件...", "orange")
            threading.Thread(target=self._async_repair_components, args=(selected,), daemon=True).start()

        self.repair_select_all_btn.config(command=on_select_all)
        self.repair_select_none_btn.config(command=on_select_none)
        self.repair_start_btn.config(command=on_repair_click)

        def _build_component_list_bg():
            """背景執行緒：做所有慢速 I/O 偵測，完成後切回主執行緒更新 UI"""
            component_versions = {}
            for comp_id, comp_name, comp_desc, is_missing, is_essential in all_components:
                version = None
                if comp_id == "ytdlp":
                    version = self._get_ytdlp_version()
                elif comp_id == "ai_common":
                    version = self._get_package_version(self.common_lib_dir, "audio-separator")
                elif comp_id == "ai_cpu":
                    version = self._get_package_version(self.lib_dir, "torch")
                elif comp_id == "ai_gpu":
                    version = self._get_package_version(self.gpu_lib_dir, "torch")
                elif comp_id == "whisper":
                    version = self._get_package_version(self.common_lib_dir, "openai-whisper")
                    if not version and self.whisper_models_dir.exists() and any(self.whisper_models_dir.iterdir()):
                        version = "模型已下載"
                elif comp_id == "stable_whisper":
                    version = self._get_package_version(self.common_lib_dir, "stable-ts")
                elif comp_id == "zhconv":
                    version = self._get_package_version(self.common_lib_dir, "zhconv")
                elif comp_id == "ai_directml":
                    version = self._get_package_version(self.directml_lib_dir, "onnxruntime-directml")
                component_versions[comp_id] = version

            self.root.after(0, lambda: _render_component_list(all_components, component_versions))

        def _render_component_list(all_components, component_versions):
            """主執行緒：清除 loading，繪製元件列表"""
            for widget in self.repair_components_frame.winfo_children():
                widget.destroy()
            self.repair_component_vars = {}

            has_versions = any(v is not None for v in component_versions.values())
            if has_versions:
                info_label = tk.Label(self.repair_components_frame, text="💡 部分元件已顯示目前版本，您可以勾選以重新安裝更新", font=("Arial", 11), fg="#1E88E5", bg=self._bg)
                info_label.pack(pady=2, anchor=tk.W)

            if ai_gpu_essential:
                detect_text = "🖥️ 已偵測到 NVIDIA 顯示卡，建議安裝「AI GPU 運算核心」（速度最快）"
                detect_color = "#1B5E20"
            elif ai_directml_essential:
                detect_text = "🖥️ 已偵測到 AMD / Intel 顯示卡，建議安裝「AI DirectML 運算核心」"
                detect_color = "#1B5E20"
            elif ai_cpu_essential:
                detect_text = "🖥️ 未偵測到獨立顯示卡，建議安裝「AI CPU 運算核心」"
                detect_color = "#E65100"
            else:
                detect_text = "✅ 已根據您的顯示卡自動選取建議的運算核心（已安裝）"
                detect_color = "#1B5E20"
            tk.Label(self.repair_components_frame, text=detect_text, font=("Arial", 11, "bold"), fg=detect_color, bg=self._bg).pack(pady=(0, 6), anchor=tk.W)

            columns_frame = tk.Frame(self.repair_components_frame, bg=self._bg)
            columns_frame.pack(fill=tk.BOTH, expand=True)

            left_col = tk.Frame(columns_frame, bg=self._bg)
            left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

            right_col = tk.Frame(columns_frame, bg=self._bg)
            right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

            core_components = []
            download_components = []
            ai_core_components = []
            ai_model_components = []

            for comp in all_components:
                comp_id = comp[0]
                if comp_id in ["python", "ffmpeg"]:
                    core_components.append(comp)
                elif comp_id in ["ytdlp", "zhconv"]:
                    download_components.append(comp)
                elif comp_id in ["ai_common", "ai_cpu", "ai_gpu", "ai_directml"]:
                    ai_core_components.append(comp)
                elif comp_id in ["whisper", "stable_whisper"]:
                    ai_model_components.append(comp)

            tk.Label(left_col, text="【 核心元件 】", font=("Arial", 12, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(anchor=tk.W, pady=(5, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in core_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, left_col)

            tk.Label(left_col, text="【 下載工具 】", font=("Arial", 12, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(anchor=tk.W, pady=(8, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in download_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, left_col)

            tk.Label(right_col, text="【 AI 運算核心 】", font=("Arial", 12, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(anchor=tk.W, pady=(5, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in ai_core_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, right_col)

            tk.Label(right_col, text="【 AI 模型 】", font=("Arial", 12, "bold"), fg=self.ui_colors['primary'], bg=self._bg).pack(anchor=tk.W, pady=(8, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in ai_model_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, right_col)

            self.repair_select_all_btn.config(command=on_select_all)
            self.repair_select_none_btn.config(command=on_select_none)
            self.repair_start_btn.config(command=on_repair_click)

        threading.Thread(target=_build_component_list_bg, daemon=True).start()

    def _check_component_update(self, comp_id, comp_name, parent_dialog):
        """檢查指定元件是否有更新"""
        self.log(f"🔍 正在檢查 {comp_name} 的更新...")

        update_info = {
            "python": {
                "current": None,  # 可擴充：未來可增加版本檢查
                "latest": None,
                "has_update": False
            },
            "ffmpeg": {
                "current": None,
                "latest": None,
                "has_update": False
            },
            "ytdlp": {
                "current": self._get_ytdlp_version(),
                "latest": None,
                "has_update": False
            },
            "ai_common": {
                "current": self._get_package_version(self.common_lib_dir, "audio-separator"),
                "latest": None,
                "has_update": False
            },
            "ai_cpu": {
                "current": self._get_package_version(self.lib_dir, "torch"),
                "latest": None,
                "has_update": False
            },
            "ai_gpu": {
                "current": self._get_package_version(self.gpu_lib_dir, "torch"),
                "latest": None,
                "has_update": False
            },
            "whisper": {
                "current": self._get_package_version(self.common_lib_dir, "openai-whisper"),
                "latest": None,
                "has_update": False
            }
        }

        has_update_available = True  # 模擬有更新
        current_ver = update_info.get(comp_id, {}).get("current", "未知版本")

        msg = f"元件: {comp_name}\n"
        if current_ver:
            msg += f"目前版本: {current_ver}\n"
        msg += "\n是否要重新安裝/更新此元件？"

        if messagebox.askyesno(f"檢查更新 - {comp_name}", msg):
            parent_dialog.destroy()
            self.is_processing = True
            self.update_status(f"正在更新 {comp_name}...", "orange")
            threading.Thread(target=self._async_repair_components, args=([comp_id],), daemon=True).start()

    def _get_ytdlp_version(self):
        """取得 yt-dlp 版本（優先讀 dist-info，避免啟動 subprocess）"""
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

    def _get_package_version(self, target_dir, package_name):
        """取得指定目錄中套件的版本（直接讀 dist-info，不跑 subprocess）"""
        try:
            norm = package_name.lower().replace("-", "_")
            norm_dash = package_name.lower().replace("_", "-")
            target = Path(target_dir)
            for dist_info in target.glob("*.dist-info"):
                folder_lower = dist_info.name.lower()
                if folder_lower.startswith(norm + "-") or folder_lower.startswith(norm_dash + "-"):
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
        """統一記錄安裝結果"""
        if ok:
            self.log(success_msg or f"✅ {name} 已安裝完成。")
        else:
            self.log(fail_msg or f"❌ {name} 安裝失敗。")
        return ok

    def _install_component_python(self):
        """安裝 Python 核心"""
        if not self.local_python.exists():
            self.log("🚀 正在下載內建 Python 核心 (約 10MB)...")
            if not self.download_portable_python():
                self.log("❌ Python 下載失敗，請檢查網路連線。")
                return False
        else:
            self.fix_python_pth()

        if self.local_python.exists():
            self.log("✅ 內建 Python 核心已就緒。")
            return True
        else:
            self.log("❌ Python 部署異常：路徑存在但找不到執行檔。")
            return False

    def _install_component_ytdlp(self):
        """安裝 YouTube 下載器 yt-dlp"""
        if not self._is_ytdlp_installed():
            self.log("🚀 正在安裝 YouTube 下載器 yt-dlp（約 10‑20MB）...")
            self._install_ytdlp_silent()
        else:
            self.log("✅ YouTube 下載器 yt-dlp 已就緒。")
        return True

    def _install_component_ffmpeg(self):
        """安裝 FFmpeg 音訊引擎"""
        if not (self.bin_dir / "ffmpeg.exe").is_file():
            self.log("🚀 正在下載音訊引擎 FFmpeg (約 100MB+)...")
            if not self.download_ffmpeg():
                self.log("❌ FFmpeg 下載失敗。")
                return False
        else:
            self.log("✅ 音訊引擎 FFmpeg 已就緒。")
        return True

    def _install_component_ai_common(self):
        """安裝 AI 共用函式庫 (audio-separator)"""
        self.common_lib_dir.mkdir(parents=True, exist_ok=True)
        self.log("📦 正在安裝 AI 共用函式庫 (audio-separator)...")
        ok = self._run_pip(["--upgrade", "audio-separator"], self.common_lib_dir)
        return self._log_install_result(ok, "AI 共用函式庫 (audio-separator)")

    def _install_component_ai_cpu(self):
        """安裝 AI CPU 運算核心"""
        is_rtx50 = self._is_rtx_50_series()
        return self._install_ai_stack(self.lib_dir, "cpu", is_rtx50, clean=False)

    def _install_component_ai_gpu(self):
        """安裝 AI GPU 運算核心"""
        if not self._is_nvidia_gpu_present():
            self.log("❌ 未偵測到 NVIDIA GPU，跳過 GPU 核心安裝。")
            return False
        is_rtx50 = self._is_rtx_50_series()
        return self._install_ai_stack(self.gpu_lib_dir, "gpu", is_rtx50, clean=False)

    def _install_component_whisper(self):
        """安裝 Whisper AI 歌詞識別模型"""
        self.log("📥 正在安裝 Whisper 相關套件...")
        try:
            if not self._run_pip(["openai-whisper", "ffmpeg-python"], self.common_lib_dir, log_all=True):
                self.log("❌ Whisper 套件安裝失敗。")
                return False
            self.log("✅ Whisper 套件安裝完成。")

            # 安裝 stable-ts：對 Whisper word_timestamps 做強制對齊後校正，
            # 大幅提升逐字時間軸精度（±50ms vs 原生 ±300ms）
            self.log("📥 正在安裝 stable-ts（時間軸精準對齊套件）...")
            if self._run_pip(["stable-ts"], self.common_lib_dir, log_all=False):
                self.log("✅ stable-ts 安裝完成。")
            else:
                self.log("⚠️ stable-ts 安裝失敗，將以原生 Whisper 時間軸繼續（精度較低）。")

            if not self.whisper_models_dir.exists():
                self.whisper_models_dir.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 模型目錄已建立: {self.whisper_models_dir}")

            self.log("📥 正在下載 Whisper small 模型...")
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
                text=True, bufsize=1, universal_newlines=True,
                encoding='utf-8', errors='replace',
                creationflags=self.subp_flags
            )
            self._current_process = process2

            model_loaded = False
            for line in process2.stdout:
                clean_line = line.strip()
                if clean_line:
                    self.log(f"  > {clean_line}")
                if "[SUCCESS]" in clean_line:
                    model_loaded = True

            process2.wait()  # 確保 returncode 已更新
            if process2.returncode == 0 or model_loaded:
                self.log("✅ Whisper small 模型下載完成。")
                return True
            else:
                self.log("❌ Whisper 模型下載失敗。")
                return False

        except Exception as e:
            self.log(f"❌ 安裝 Whisper 時發生錯誤: {str(e)}")
            return False

    def _ensure_common_lib_in_path(self):
        """確保 common_lib_dir 在 sys.path 中（供套件偵測使用）"""
        if str(self.common_lib_dir) not in sys.path:
            sys.path.insert(0, str(self.common_lib_dir))

    def _is_zhconv_installed(self):
        """檢查 zhconv 是否已安裝"""
        try:
            self._ensure_common_lib_in_path()
            import zhconv
            return True
        except ImportError:
            return False

    def _is_stable_whisper_installed(self):
        """檢查 stable-ts 是否已安裝（模組目錄名為 stable_whisper）"""
        # 方法1：直接 import（最可靠）
        try:
            self._ensure_common_lib_in_path()
            import stable_whisper  # noqa: F401
            return True
        except ImportError:
            pass
        # 方法2：掃描 dist-info 目錄（import 快取問題時的備用）
        try:
            for p in self.common_lib_dir.iterdir():
                if p.name.startswith("stable_ts-") and p.suffix in (".dist-info", ".egg-info"):
                    return True
        except Exception:
            pass
        return False

    def _install_pip_component(self, pkg_name, display_name, target_dir=None, log_all=False):
        """通用：pip 安裝單一套件並記錄結果"""
        if target_dir is None:
            target_dir = self.common_lib_dir
        self.log(f"🚀 正在安裝 {display_name}...")
        ok = self._run_pip([pkg_name], target_dir, log_all=log_all)
        return self._log_install_result(ok, display_name)

    def _install_component_zhconv(self):
        """安裝中文簡繁轉換庫 zhconv"""
        return self._install_pip_component("zhconv", "中文簡繁轉換庫 zhconv")

    def _install_component_stable_whisper(self):
        """安裝 stable-ts 時間軸精準對齊套件"""
        return self._install_pip_component("stable-ts", "stable-ts 時間軸精準對齊套件", log_all=True)

    def _install_component_ai_directml(self):
        """安裝 AI DirectML 運算核心"""
        self.log("🚀 正在安裝 AI DirectML 運算核心...")
        self.directml_lib_dir.mkdir(parents=True, exist_ok=True)
        ok = self._run_pip(["onnxruntime-directml"], self.directml_lib_dir)
        return self._log_install_result(ok, "AI DirectML 運算核心")

    def _ensure_setup_dirs(self, show_progress=False):
        """確保所有工作目錄存在，失敗時記錄錯誤並回傳 False。"""
        setup_dirs = [
            ("音訊引擎", self.bin_dir),
            ("Python環境", self.py_dir),
            ("yt-dlp目錄", self.ytdlp_dir),
            ("共用AI函式庫", self.common_lib_dir),
            ("CPU AI函式庫", self.lib_dir),
            ("GPU AI函式庫", self.gpu_lib_dir),
            ("DirectML AI函式庫", self.directml_lib_dir),
            ("模型目錄", self.models_dir),
        ]
        for i, (name, d) in enumerate(setup_dirs):
            try:
                if not d.parent.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                d.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 目錄已就緒: {d.name}")
                if show_progress:
                    self.update_status(f"正在準備目錄... ({i+1}/{len(setup_dirs)})", "orange")
            except Exception as e:
                self.log(f"❌ 無法建立 {name} 目錄: {d}")
                self.log(f"   錯誤訊息: {str(e)}")
                return False
        return True

    # ── 環境修復：混合版（集中 registry + 單一 loop），兼顧可維護與精簡 ──
    def _components(self):
        """
        所有「可修復/可更新」元件集中在這裡。
        新增/調整元件時，只要改這一份清單即可（不用到處複製安裝流程）。
        """
        return {
            "python":         ("內建 Python 核心",                         self._install_component_python),
            "ffmpeg":         ("音訊引擎 FFmpeg",                           self._install_component_ffmpeg),
            "ytdlp":          ("YouTube 下載器 yt-dlp",                      self._install_component_ytdlp),
            "ai_common":      ("AI 共用函式庫 (audio-separator)",            self._install_component_ai_common),
            "ai_cpu":         ("AI CPU 運算核心 (PyTorch + ONNX Runtime)",   self._install_component_ai_cpu),
            "ai_gpu":         ("AI GPU 運算核心 (PyTorch + ONNX Runtime)",   self._install_component_ai_gpu),
            "ai_directml":    ("AI DirectML 運算核心",                       self._install_component_ai_directml),
            "whisper":        ("Whisper AI 歌詞識別模型",                    self._install_component_whisper),
            "stable_whisper": ("stable-ts 時間軸精準對齊套件",               self._install_component_stable_whisper),
            "zhconv":         ("中文簡繁轉換庫 zhconv",                      self._install_component_zhconv),
        }

    def _run_components(self, ids, *, start=0, end=90, status_color="orange", overrides=None):
        """
        用同一套 loop 跑所有元件安裝/更新（避免重複寫）。
        - ids: 元件 id 列表
        - start/end: 自動分配進度的範圍
        - overrides: {id: {"pct": int, "step": str, "status": str}}
        """
        comps = self._components()
        overrides = overrides or {}
        ids = [cid for cid in ids if cid in comps]
        total = len(ids)
        if total <= 0:
            return True

        ok_all = True
        for i, cid in enumerate(ids, start=1):
            name, fn = comps[cid]
            ov = overrides.get(cid, {}) or {}

            pct = ov.get("pct")
            if pct is None:
                pct = int(start + (end - start) * (i - 1) / max(1, total))

            status_txt = ov.get("status", f"正在安裝 {name}... ({i}/{total})")
            step_txt   = ov.get("step",   f"步驟 {i}/{total}：{name}")

            self.update_status(status_txt, status_color)
            self.update_progress(pct, step_text=step_txt)
            self.log(f"\n--- 正在安裝組件: {cid} ---")

            try:
                ok = bool(fn())
            except Exception as e:
                ok = False
                self.log(f"  ❌ 安裝流程發生例外：{str(e)}")

            if not ok:
                ok_all = False

        return ok_all

    def _async_repair_components(self, selected_components):
        """修復選定的組件"""
        self.log(f"--- 開始修復選定的組件: {', '.join(selected_components)} ---")
        self.update_status("正在準備修復環境...", "orange")

        if not self._ensure_setup_dirs(show_progress=True):
            self.is_processing = False
            self.update_status("修復失敗", "red")
            return

        # 用同一套共用 loop 跑所有元件安裝/更新
        success = self._run_components(selected_components, start=0, end=90, status_color="orange")

        if any(comp in selected_components for comp in ["ai_common", "ai_cpu", "ai_gpu"]):
            self.update_status("正在重新檢測運算環境...", "orange")
            self.update_progress(95, step_text="正在驗證運算環境...")
            self.log("\n🔄 正在根據新環境自動切換運算裝置...")
            self._startup_ort_check()

        self.update_progress(100, "修復完成", step_text="")
        self.is_processing = False
        self._reset_ort_fix_prompt_state(clear_history=True)
        self._startup_component_prompt_shown = False
        self.update_status("準備就緒", "green")
        if success:
            self.log("\n--- 所有選定組件修復完成 ---")
        else:
            self.log("\n--- 部分組件修復失敗，請查看上方日誌 ---")

        if self.current_tab_index == 6:
            self.root.after(100, lambda: self.check_components(prompt=False, show_list=True))

    def _is_ytdlp_installed(self):
        """檢查 yt-dlp 是否已安裝（Scripts/ 或 lib_dir 或 ytdlp_dir 均算）"""
        if (self.ytdlp_dir / "yt-dlp.exe").exists():
            return True
        if (self.ytdlp_dir / "yt_dlp" / "__main__.py").exists():
            return True
        if (self.py_dir / "Scripts" / "yt-dlp.exe").exists():
            return True
        if (self.lib_dir / "yt_dlp" / "__main__.py").exists():
            return True
        return False

    def _check_ytdlp(self):
        """檢查 yt-dlp 是否安裝，若無則靜默安裝"""
        if not self._is_ytdlp_installed():
            self.log("🚀 偵測到缺少 YouTube 下載組件，正在自動補齊...")
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
                self.log("✅ YouTube 下載組件已補齊。")
            else:
                self.log(f"❌ YouTube 下載組件安裝失敗: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            self.log("❌ YouTube 下載組件安裝逾時（超過 120 秒），請手動點擊「初始化/修復環境」。")
        except Exception as e:
            self.log(f"❌ YouTube 下載組件安裝出錯: {str(e)}")

    def _async_setup_environment(self, install_mode="auto"):
        self.log(f"--- 開始自動化環境部署 (模式: {install_mode}) ---")
        self.update_progress(0, step_text="步驟 1/5：建立目錄結構...")

        if not self._ensure_setup_dirs():
            self.is_processing = False
            return

        # 基礎元件：用同一套安裝 loop 跑（避免重複寫安裝流程）
        overrides = {
            "python": {"pct": 10, "step": "步驟 2/5：安裝內建 Python 核心 (約 10MB)..."},
            "ytdlp":  {"pct": 25, "step": "步驟 3/5：安裝 YouTube 下載器 yt-dlp (約 10-20MB)..."},
            "ffmpeg": {"pct": 40, "step": "步驟 4/5：安裝音訊引擎 FFmpeg (約 100MB+)..."},
        }
        base_ok = self._run_components(["python", "ytdlp", "ffmpeg"], start=10, end=40, status_color="orange", overrides=overrides)
        if not base_ok:
            self.is_processing = False
            return

        self.update_progress(60, step_text="步驟 5/5：檢查 AI 運算環境...")
        self.log("🔍 正在進行 AI 運算環境深度檢查...")
        packages_ok = False
        target_ai_dir = self._get_target_ai_dir(install_mode)
        expect_gpu_stack = (target_ai_dir == self.gpu_lib_dir)

        has_torch = (target_ai_dir / "torch").exists()
        has_sep = (self.common_lib_dir / "audio_separator").exists()
        has_ort = self._has_onnxruntime_package(target_ai_dir)

        if has_torch and has_sep and has_ort:
            try:
                self.log(f"  > 正在測試 {'GPU' if expect_gpu_stack else 'CPU'} 組件導入...")
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
                self.log(f"  > 核心組件狀態: {check_out}")

                if "CHECK_RESULT:OK" in check_out:
                    packages_ok = True
                elif "CHECK_RESULT:CPU_OK" in check_out:
                    self.log("✅ 無 NVIDIA 顯示卡，CPU 版本組件運作正常。")
                    packages_ok = True
                elif "CHECK_RESULT:SM120_INCOMPATIBLE" in check_out:
                    self.log("🔍 偵測到 RTX 50 系列顯示卡與現有運算核心不相容，將執行強制升級。")
                    packages_ok = False
                elif "CHECK_RESULT:WRONG_BUILD_FOR_CPU" in check_out:
                    self.log("🔍 偵測到安裝的是 GPU 版本但主機無 NVIDIA 顯示卡，將重裝為 CPU 版本。")
                    packages_ok = False
                else:
                    self.log("🔍 偵測到加速組件不完整或不支援 GPU，將執行修復。")
                    packages_ok = False
            except Exception as e:
                self.log(f"⚠️ 檢查過程發生異常: {str(e)}")
        else:
            if not has_torch: self.log("🔍 偵測到缺少 PyTorch 核心組件。")
            if not has_sep: self.log("🔍 偵測到缺少音訊分離核心組件。")
            if not has_ort: self.log("🔍 偵測到缺少 ONNX Runtime 核心。")
            packages_ok = False

        if not packages_ok or install_mode != "auto":
            self.update_progress(65, step_text="步驟 5/5：安裝 AI 運算組件 (需數分鐘)...")
            self.log(f"🚀 準備執行 AI 運算組件安裝/修復 (模式: {install_mode})...")
            if not self.install_packages_locally(install_mode=install_mode):
                self.log("❌ AI 組件安裝失敗，請查看上方詳細日誌。")
                self.is_processing = False
                return
            self.log("✅ AI 組件安裝/修復完成。")
            self.log("🔄 正在根據新環境自動切換運算裝置...")
            self._startup_ort_check()

        self.update_progress(100, "全部就緒", step_text="")
        self.is_processing = False
        self._reset_ort_fix_prompt_state(clear_history=True)
        self._startup_component_prompt_shown = False
        self.update_status("準備就緒", "green")
        self.log("--- 環境部署完成 ---")

    def fix_python_pth(self):
        try:
            pth_files = list(self.py_dir.glob("*._pth"))
            if not pth_files:
                self.log("⚠️ 找不到 Python .pth 設定檔，跳過路徑校正。")
                return
            pth_file = pth_files[0]
            with open(pth_file, "r") as f:
                lines = f.readlines()

            lines = [l.strip() for l in lines if l.strip()]
            removed_legacy = False
            legacy_entries = {"..\\ai_libraries", "..\\ai_libraries_cpu", "..\\ai_libraries_gpu", "..\\ai_libraries_directml", "..\\ai_libraries_common"}
            filtered_lines = []
            for line in lines:
                if line in legacy_entries:
                    removed_legacy = True
                    continue
                filtered_lines.append(line)
            lines = filtered_lines

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
                self.log("🔧 已校正 Python 路徑設定檔（改為執行時動態注入 CPU/GPU 套件路徑）。")
        except Exception as e:
            self.log(f"⚠️ 路徑校正失敗: {str(e)}")

    def download_portable_python(self):
        py_urls = [
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip",
        ]

        try:
            self.py_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"❌ 無法建立 Python 目錄: {self.py_dir}")
            self.log(f"   原因: {str(e)}")
            self.log("💡 請手動建立該資料夾，或將程式移至桌面等較短路徑後重試。")
            return False

        if not self.py_dir.exists():
            self.log(f"❌ 目錄建立後仍不存在（可能是權限問題）: {self.py_dir}")
            return False

        _test_file = self.py_dir / ".write_test"
        try:
            _test_file.write_text("ok")
            _test_file.unlink()
        except Exception as e:
            self.log(f"❌ 目錄無寫入權限: {self.py_dir}")
            self.log(f"   原因: {str(e)}")
            self.log("💡 請以系統管理員身份執行程式，或更換輸出目錄位置。")
            return False

        zip_path = self.py_dir / "py.zip"
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass

        self._setup_ssl_opener()

        for attempt, url in enumerate(py_urls, 1):
            self.log(f"🚀 正在下載 Python 核心 (來源 {attempt}/{len(py_urls)})...")
            try:
                self._last_log_percent = -1
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)

                if not zip_path.exists() or zip_path.stat().st_size < 1024:
                    self.log(f"⚠️ 來源 {attempt} 下載的檔案過小或不存在，嘗試下一個...")
                    zip_path.unlink(missing_ok=True)
                    continue

                if not zipfile.is_zipfile(str(zip_path)):
                    self.log(f"⚠️ 來源 {attempt} 下載的檔案損壞，嘗試下一個...")
                    zip_path.unlink(missing_ok=True)
                    continue

                self.log("📦 正在解壓縮 Python...")
                with zipfile.ZipFile(str(zip_path), "r") as zip_ref:
                    zip_ref.extractall(str(self.py_dir))

                self.fix_python_pth()
                zip_path.unlink(missing_ok=True)

                self.log("📦 正在安裝 pip 套件管理工具...")
                get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
                get_pip_path = self.py_dir / "get-pip.py"
                try:
                    urllib.request.urlretrieve(get_pip_url, str(get_pip_path))
                    subprocess.run( [str(self.local_python), str(get_pip_path)], capture_output=True, text=True, creationflags=self.subp_flags, timeout=180 )
                    get_pip_path.unlink(missing_ok=True)
                    self.log("✅ pip 安裝完成。")
                except Exception as e:
                    self.log(f"⚠️ pip 安裝失敗: {str(e)}")

                self.log(f"✅ Python 核心安裝完成（來源 {attempt}）。")
                return True

            except Exception as e:
                err_msg = str(e)
                self.log(f"⚠️ 來源 {attempt} 下載失敗: {err_msg}")
                if "No such file or directory" in err_msg:
                    self.log("   ⚠️ 寫入路徑失敗，目標目錄可能在下載過程中消失或被鎖定。")
                    self.log(f"   目標路徑: {zip_path}")
                zip_path.unlink(missing_ok=True)
                if attempt < len(py_urls):
                    self.log("🔄 嘗試下一個備用來源...")

        self.log("❌ Python 核心所有下載來源均失敗。")
        self.log("💡 可能原因：(1) 網路連線問題  (2) 防火牆封鎖  (3) 磁碟空間不足")
        self.log("💡 請確認網路正常後重試，或手動下載 Python embed zip 放入 runtime_python 資料夾。")
        return False

    def _download_reporthook(self, count, block_size, total_size):
        if total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            percent = min(percent, 100)
            self.update_progress(percent, "正在下載")
            if percent % 10 == 0:
                self._last_log_percent = getattr(self, '_last_log_percent', -1)
                if percent != self._last_log_percent:
                    self.log(f"  > 下載進度: {percent}%")
                    self._last_log_percent = percent

    def _setup_ssl_opener(self):
        """建立忽略 SSL 驗證的 urllib opener（解決 CERTIFICATE_VERIFY_FAILED）"""
        ssl_context = ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
        opener.addheaders = [('User-agent', 'Mozilla/5.0')]
        urllib.request.install_opener(opener)
        return opener

    def download_ffmpeg(self):
        primary_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
        fallback_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = self.bin_dir / "ffmpeg.zip"

        try:
            self.bin_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"❌ 無法建立 FFmpeg 目錄: {self.bin_dir}\n   原因: {str(e)}")
            return False
        _test = self.bin_dir / ".write_test"
        try:
            _test.write_text("ok"); _test.unlink()
        except Exception as e:
            self.log(f"❌ FFmpeg 目錄無寫入權限: {self.bin_dir}\n   原因: {str(e)}")
            return False

        for attempt, url in enumerate([primary_url, fallback_url], 1):
            try:
                self.log(f"🚀 正在連線至下載伺服器 (來源 {attempt}/2)...")
                self._setup_ssl_opener()
                self._last_log_percent = -1

                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)
                self.log("📦 正在提取 FFmpeg 引擎與共享函式庫 (DLLs)...")
                with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                    for file in zip_ref.namelist():
                        normalized_file = file.replace('\\', '/')
                        if "/bin/" in normalized_file and (normalized_file.endswith(".exe") or normalized_file.endswith(".dll")):
                            filename = os.path.basename(normalized_file)
                            with zip_ref.open(file) as source, open(self.bin_dir / filename, "wb") as target:
                                shutil.copyfileobj(source, target)
                if zip_path.exists():
                    os.remove(str(zip_path))
                return True
            except Exception as e:
                self.log(f"⚠️ 來源 {attempt} 下載失敗: {str(e)}")
                if zip_path.exists():
                    try: os.remove(str(zip_path))
                    except Exception: pass
                if attempt < 2:
                    self.log("🔄 嘗試備用下載來源...")

        self.log("❌ FFmpeg 所有下載來源均失敗，請檢查網路連線。")
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
                    self.log(f"  ⚠️ 清理舊組件失敗: {p.name} ({str(e)})")
        if removed_any:
            self.log(f"  ✅ 已清理舊組件: {target_dir.name}")

    def _install_ai_stack(self, target_dir, target_mode="cpu", is_rtx50=False, clean=False):
        target_dir.mkdir(parents=True, exist_ok=True)

        if target_mode == "cpu":
            self.log("📦 正在部署獨立 CPU AI 核心...")
            torch_index = "https://download.pytorch.org/whl/cpu"
            torch_ver, tv_ver, ta_ver = "2.5.1+cpu", "0.20.1+cpu", "2.5.1+cpu"
            install_steps = [
                ["setuptools", "wheel", "pip", "msvc-runtime>=14.40"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime==1.18.0", "audio-separator"]
            ]
        elif target_mode == "directml":
            self.log("📦 正在部署獨立 DirectML AI 核心...")
            torch_index = "https://download.pytorch.org/whl/cpu"
            torch_ver, tv_ver, ta_ver = "2.5.1+cpu", "0.20.1+cpu", "2.5.1+cpu"
            install_steps = [
                ["setuptools", "wheel", "pip", "msvc-runtime>=14.40"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime-directml==1.18.0", "torch-directml", "audio-separator"]
            ]
        else:
            if is_rtx50:
                self.log("📦 正在部署獨立 GPU AI 核心（cu128 / RTX 50）...")
                torch_index = "https://download.pytorch.org/whl/cu128"
                torch_ver = "2.7.1+cu128"
                tv_ver = "0.22.1+cu128"
                ta_ver = "2.7.1+cu128"
            else:
                self.log("📦 正在部署獨立 GPU AI 核心（cu124）...")
                torch_index = "https://download.pytorch.org/whl/cu124"
                torch_ver = "2.5.1+cu124"
                tv_ver = "0.20.1+cu124"
                ta_ver = "2.5.1+cu124"
            install_steps = [
                ["setuptools", "wheel", "pip", "msvc-runtime>=14.40"],
                ["nvidia-cuda-runtime-cu12", "nvidia-cudnn-cu12", "nvidia-cublas-cu12",
                 "nvidia-curand-cu12", "nvidia-cufft-cu12", "nvidia-cuda-nvrtc-cu12", "nvidia-ml-py"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime-gpu", "audio-separator[gpu]"]
            ]

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
            self.log(f"📦 正在執行安裝進度 ({i+1}/{len(install_steps)}): {' '.join(step_pkgs[-3:])}...")
            cmd = pip_base_cmd + step_pkgs
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, creationflags=self.subp_flags, encoding='utf-8',
                errors='replace', env=pip_env
            )

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
                            self.update_status(f"正在下載組件 ({i+1}/{len(install_steps)})...", "orange")

            process.wait()
            if process.returncode != 0:
                self.log(f"❌ 第 {i+1} 階段安裝失敗 (代碼: {process.returncode})。")
                return False
            if not has_output and i > 0:
                self.log(f"⚠️ 第 {i+1} 階段安裝似乎沒有輸出，請檢查環境。")

        if not (target_dir / "torch").exists():
            self.log(f"❌ 安裝程序已結束，但未能在 {target_dir.name} 中找到 torch。")
            return False
        if not (target_dir / "audio_separator").exists():
            self.log(f"❌ 安裝程序已結束，但未能在 {target_dir.name} 中找到 audio_separator。")
            return False
        if not self._has_onnxruntime_package(target_dir):
            self.log(f"❌ 安裝程序已結束，但未能在 {target_dir.name} 中找到 onnxruntime。")
            return False
        return True

    def install_packages_locally(self, install_mode="auto"):
        try:
            self.log("📥 下載 Pip 安裝工具...")
            pip_script = self.py_dir / "get-pip.py"
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", pip_script)

            self.log("📥 正在安裝 Pip 組件...")
            subprocess.run([str(self.local_python), str(pip_script)], creationflags=self.subp_flags, check=True)

            pip_check = subprocess.run(
                [str(self.local_python), "-m", "pip", "--version"],
                capture_output=True, text=True, creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
            )
            if pip_check.returncode != 0:
                self.log("❌ Pip 安裝失敗，無法繼續。")
                return False
            self.log(f"✅ Pip 已就緒: {pip_check.stdout.strip()}")
            self.log(f"📥 正在準備 AI 運算環境 (模式: {install_mode})...")

            has_nvidia_gpu = self._is_nvidia_gpu_present()
            is_rtx50 = self._is_rtx_50_series() if has_nvidia_gpu else False

            if any(ord(c) > 127 for c in str(self.app_dir)):
                self.log("⚠️ 偵測到路徑中含有中文或特殊字元，這極易導致安裝失敗。")
                self.log("💡 強烈建議：將程式資料夾移至磁碟根目錄 (例如 C:\\mp3_tool)，避免路徑問題。")

            install_targets = []
            if install_mode == "cpu":
                self.log("ℹ️ 使用者選擇強制安裝 CPU 版本。")
                install_targets = [(self.lib_dir, "cpu", False)]
            elif install_mode == "gpu":
                if not has_nvidia_gpu:
                    self.log("❌ 目前未偵測到 NVIDIA 顯示卡，無法安裝純 GPU 版本。")
                    return False
                self.log("ℹ️ 使用者選擇強制安裝 GPU 版本。")
                install_targets = [(self.gpu_lib_dir, "gpu", is_rtx50)]
            elif install_mode == "directml":
                self.log("ℹ️ 使用者選擇強制安裝 DirectML 版本。")
                install_targets = [(self.directml_lib_dir, "directml", False)]
            elif install_mode == "both":
                self.log("ℹ️ 使用者選擇安裝 CPU + GPU 雙支援版（分開存放）。")
                install_targets = [(self.lib_dir, "cpu", False)]
                if has_nvidia_gpu:
                    install_targets.append((self.gpu_lib_dir, "gpu", is_rtx50))
                else:
                    self.log("⚠️ 目前未偵測到 NVIDIA 顯示卡，本次僅安裝 CPU 套件。")
            else:
                if not has_nvidia_gpu:
                    self.log("ℹ️ 未偵測到 NVIDIA 顯示卡，將安裝 CPU 版本。")
                    install_targets = [(self.lib_dir, "cpu", False)]
                elif is_rtx50:
                    self.log("🚀 偵測到 RTX 50 系列，將安裝獨立 GPU cu128 核心。")
                    install_targets = [(self.gpu_lib_dir, "gpu", True)]
                else:
                    self.log("✅ 偵測到 NVIDIA 顯示卡，將安裝獨立 GPU cu124 核心。")
                    install_targets = [(self.gpu_lib_dir, "gpu", False)]

            for target_dir, target_mode, target_is_rtx50 in install_targets:
                if not self._install_ai_stack(target_dir, target_mode=target_mode, is_rtx50=target_is_rtx50, clean=True):
                    return False

            if pip_script.exists():
                os.remove(pip_script)
            return True
        except Exception as e:
            err_text = str(e)
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                self.log("❌ 磁碟空間不足，無法繼續安裝 AI 組件。")
                self.log("💡 建議先釋放磁碟空間後再重試。")
                self.log("💡 若不需要 GPU 加速，請改選「僅安裝 CPU 版」，所需空間會比雙支援版少很多。")
            else:
                self.log(f"安裝錯誤: {err_text}")
            return False

    def _is_rtx_50_series(self):
        """檢查是否有 RTX 50 系列顯示卡"""
        try:
            res = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True, text=True,
                creationflags=self.subp_flags, timeout=10,
                encoding="utf-8", errors="replace"
            )
            if res.returncode == 0 and "RTX 50" in res.stdout:
                return True
        except Exception:
            pass
        return False

    def _is_nvidia_gpu_present(self):
        """
        透過 wmic / PowerShell 實際查詢系統是否有 NVIDIA GPU（已啟用）。
        只查硬體，不依賴 CUDA/PyTorch，避免「GPU 停用但 CUDA driver 仍在」的誤判。
        """
        return self._detect_gpu_vendor() == "nvidia"

    def _startup_ort_check(self):
        """
        啟動時背景執行緒：
        1. 先檢查 NVIDIA GPU 核心是否可用（最高優先）
        2. 若無 NVIDIA GPU，檢查 DirectML 核心是否可用（第二優先）
        3. 最後檢查 CPU 核心是否可用
        CPU / GPU / DirectML 三條路徑完全分開，避免不必要的等待。
        """
        if not self.local_python.exists():
            return
        if self._startup_ort_check_running:
            self.log("ℹ️ 啟動環境檢測已在執行中，略過重複請求。")
            return

        self._startup_ort_check_running = True
        try:
            has_nvidia_gpu = self._is_nvidia_gpu_present()
            gpu_out = "STACK_SKIPPED"
            directml_out = "STACK_SKIPPED"
            cpu_out = "STACK_SKIPPED"

            if has_nvidia_gpu and (self.gpu_lib_dir / "torch").exists():
                gpu_out = self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True)
                if gpu_out == "ORT_OK_GPU":
                    self.log("✅ 偵測到獨立 NVIDIA GPU 核心已就緒，自動切換至 GPU 模式。")
                    self.root.after(0, lambda: self.device_var.set("gpu"))
                    self._reset_ort_fix_prompt_state(clear_history=False)
                    return

            if (self.directml_lib_dir / "onnxruntime").exists():
                directml_out = self._probe_onnxruntime_stack(self.directml_lib_dir, expect_gpu=False)
                if directml_out == "ORT_OK_CPU":
                    self.log("✅ 偵測到 DirectML 核心已就緒，自動切換至 DirectML 模式。")
                    self.root.after(0, lambda: self.device_var.set("directml"))
                    self._reset_ort_fix_prompt_state(clear_history=False)
                    return

            cpu_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
            if cpu_out == "ORT_OK_CPU":
                self.log("✅ 基礎環境已就緒（CPU 模式）。")
                if self.device_var.get() in ["gpu", "directml"]:
                    self.root.after(0, lambda: self.device_var.set("cpu"))
                self._reset_ort_fix_prompt_state(clear_history=False)
            else:
                self.log(f"ℹ️ CPU 核心檢測結果: {cpu_out}")

            if has_nvidia_gpu:
                if gpu_out not in ["STACK_SKIPPED", "STACK_MISSING"]:
                    self.log(f"🔍 NVIDIA GPU 核心檢測結果: {gpu_out}")
                if gpu_out in ["ORT_DLL_FAIL", "ORT_NO_OUTPUT"] and cpu_out == "ORT_OK_CPU":
                    self.log("⚠️ NVIDIA GPU 核心存在但無法載入，已保留 CPU 模式，不影響純 CPU 使用。")
            if directml_out not in ["STACK_SKIPPED", "STACK_MISSING"]:
                self.log(f"🔍 DirectML 核心檢測結果: {directml_out}")
            if not has_nvidia_gpu and cpu_out == "ORT_OK_CPU":
                self.log("💡 未偵測到 NVIDIA 顯示卡，CPU 模式為正常運行狀態。")
        except Exception as e:
            self.log(f"ℹ️ 啟動時環境檢測失敗: {str(e)}")
        finally:
            self._startup_ort_check_running = False

    def _prompt_ort_fix(self, issue_key="gpu_runtime_fallback"):
        """彈窗詢問使用者是否立即修復 onnxruntime 版本問題"""
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            self.log(f"ℹ️ [PROMPT] 已抑制修復提示，不再顯示: {issue_key}")
            return
        if self._ort_fix_prompt_active:
            self.log(f"ℹ️ [PROMPT] 修復提示已在顯示中: {issue_key}")
            return
        if self.is_processing:
            self._schedule_ort_fix_prompt(issue_key=issue_key, delay_ms=5000)
            return
        self._ort_fix_prompt_active = True
        self._ort_fix_prompt_shown_keys.add(issue_key)
        self.log(f"ℹ️ [PROMPT] 顯示修復提示: {issue_key}")
        try:
            answer = messagebox.askyesno(
                "建議修復 AI 組件",
                "偵測到 AI 組件版本與您的系統不符（GPU 版裝在無 NVIDIA 顯示卡的電腦上）。\n\n"
                "目前程式已自動切換至 CPU 模式，音訊分離功能仍可正常使用。\n\n"
                "建議執行修復以取得最佳效能並避免每次啟動的診斷延遲。\n"
                "（重新下載適合的 CPU 版本，約 800MB）\n\n"
                "是否立即自動修復？"
            )
            if answer:
                self._start_async_setup()
            else:
                self._ort_fix_prompt_suppressed_keys.add(issue_key)
                self.log(f"ℹ️ [PROMPT] 使用者已拒絕本次修復提示: {issue_key}")
        finally:
            self._ort_fix_prompt_active = False

    def _quick_check_gpu(self):
        """快速檢測 GPU 是否可用（不彈窗）"""
        if not self.local_python.exists(): return False

        if not self._is_nvidia_gpu_present():
            return False
        if not (self.gpu_lib_dir / "torch").exists():
            return False
        return self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True) == "ORT_OK_GPU"

    def _check_gpu_before_start(self):
        """若選用 GPU，執行快速檢測；不通過時詢問修復或降回 CPU。回傳 True 才可繼續。"""
        if self.device_var.get() != "gpu":
            return True
        if not self._quick_check_gpu():
            if messagebox.askyesno("環境未就緒",
                    "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？\n(若不修復將改用 CPU 運行，速度較慢)"):
                self.check_gpu_env()
                return False
            self.log("⚠️ 使用者選擇忽略，將嘗試改用 CPU 模式。")
            self.device_var.set("cpu")
        return True

    def start_separation(self):
        if not self.file_list:
            messagebox.showwarning("警告", "請先加入音檔！")
            return
        if self.is_processing: return

        if not self._check_gpu_before_start():
            return
        self._begin_processing("正在處理中...", self.batch_process)

    def start_yt_process(self):
        url = self.yt_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "請輸入 YouTube 網址！")
            return
        if self.is_processing: return

        if not self._check_gpu_before_start():
            return
        self._begin_processing("正在從 YouTube 下載並處理...", self.yt_process, url)

    def yt_process(self, url):
        start_time = time.time()
        output_dir = self.output_dir_var.get()
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self._last_downloaded_subtitle = None
        subtitle_mode = self.yt_subtitle_mode_var.get() if self.yt_cc_var.get() else "none"

        self.log(f"--- 正在處理 YouTube 影片: {url} ---")
        self.update_progress(5, "正在獲取影片資訊", step_text="步驟 1/5：獲取影片資訊")
        if subtitle_mode == "srt_only":
            self.log("📝 字幕模式：只抓 SRT，不封裝進成品")
        elif subtitle_mode == "mux":
            self.log("📝 字幕模式：抓字幕並合成到成品")

        dl_result = self._download_youtube_from_ui(
            url, output_dir, "both",
            quality="1080",
            download_subtitles=(subtitle_mode in ("srt_only", "mux")),
            log_quality_text="1080"
        )
        video_file, audio_file = dl_result if dl_result else (None, None)

        if not video_file:
            self.log("❌ YouTube 影片下載失敗。")
            self.finish_processing()
            return

        if not audio_file:
            self.log("❌ 音訊擷取失敗。")
            self.finish_processing()
            return

        self.update_progress(40, "正在分離人聲與伴奏", step_text="步驟 3/5：AI 人聲分離中")

        success = self.run_audio_separator(audio_file, output_dir)

        if success:
            enable_lyrics = self.enable_lyrics_recognition_var.get()
            srt_subtitle = None
            json_subtitle = None
            if enable_lyrics:
                self.update_progress(70, "正在識別歌詞", step_text="步驟 4/5：AI 歌詞辨識中")
                result = self.recognize_lyrics_and_generate_srt(audio_file, output_dir)
                if result:
                    srt_subtitle, json_subtitle, _ = result
                    video_stem = Path(video_file).stem
                    final_srt_path = Path(output_dir) / f"{video_stem}_KTV.srt"
                    final_json_path = Path(output_dir) / f"{video_stem}_KTV.json"

                    srt_subtitle = self._rename_output_file(srt_subtitle, final_srt_path, "SRT 字幕檔")
                    if json_subtitle:
                        self._rename_output_file(json_subtitle, final_json_path, "JSON 歌詞檔")

            self.log("📦 正在整理並重新命名產出檔案...")
            voc_file, inst_file = self.consolidate_stems(audio_file, video_file, output_dir)

            if voc_file and inst_file:
                vfmt = self.video_format_var.get()
                self.update_progress(80, f"正在合成 {vfmt.upper()} 伴唱帶", step_text=f"步驟 5/5：合成 {vfmt.upper()} 伴唱帶")
                output_file = Path(output_dir) / f"{Path(video_file).stem}_KTV.{vfmt}"

                subtitle_for_mux = self._last_downloaded_subtitle if subtitle_mode == "mux" else None
                if srt_subtitle and not subtitle_for_mux:
                    subtitle_for_mux = srt_subtitle

                mkv_success = self.synthesize_mkv( video_file, voc_file, inst_file, str(output_file), subtitle_file=subtitle_for_mux )

                if mkv_success:
                    self.log(f"✅ 成功生成 {vfmt.upper()} 伴唱帶: {output_file.name}")
                    if self._last_downloaded_subtitle:
                        self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, str(output_file))

                    self.update_progress(100, "處理完成", step_text="✅ 完成！")
                    elapsed_time = time.time() - start_time
                    self.log(f"⏱️ YouTube 處理完成，總花費時間: {elapsed_time:.2f} 秒")
                    messagebox.showinfo("成功", f"YouTube 處理完成！\n總花費時間: {elapsed_time:.2f} 秒\n檔案已儲存至: {output_dir}")
                    if os.name == 'nt' and os.path.exists(output_dir):
                        self._open_folder_no_dup(str(output_dir))
                else:
                    self.log("❌ MKV 合成失敗。")
            else:
                self.log("❌ 找不到分離後的必要檔案 (人聲或伴奏)。")
        else:
            self.log("❌ 音訊分離失敗。")

        self.finish_processing()

    def consolidate_stems(self, input_audio, reference_video, output_dir):
        """整理分離後的音軌：重新命名人聲，並合併多音軌為伴奏 (針對 Demucs)"""
        fmt = self.output_format_var.get()
        video_stem = Path(reference_video).stem
        out_path = Path(output_dir)

        def safe_stem(stem):
            """清除非法字元並截短，避免 Windows 路徑過長"""
            return AudioSeparatorApp.sanitize_filename(stem, max_len=60)

        voc_final  = out_path / f"{safe_stem(video_stem)}_人聲.{fmt}"
        inst_final = out_path / f"{safe_stem(video_stem)}_伴奏.{fmt}"

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
                if voc_final.exists(): os.remove(voc_final)
                f.rename(voc_final)
                break

        found_inst = False
        for f in all_sep_files:
            if any(kw in f.name for kw in inst_kw):
                if inst_final.exists(): os.remove(inst_final)
                f.rename(inst_final)
                found_inst = True
                break

        if not found_inst:
            stems_to_merge = [f for f in all_sep_files if any(kw in f.name for kw in demucs_kw)]

            if stems_to_merge:
                stem_names = ", ".join(sorted(f.name for f in stems_to_merge))
                self.log(f"  > 偵測到 Demucs 多音軌，正在合併 {len(stems_to_merge)} 個音軌為伴奏...")
                self.log(f"  > 參與合併的音軌: {stem_names}")
                inputs = []
                for f in stems_to_merge:
                    inputs.extend(["-i", str(f)])

                filter_str = "".join([f"[{i}:a]" for i in range(len(stems_to_merge))])
                filter_str += f"amix=inputs={len(stems_to_merge)}:duration=first[out]"

                merge_cmd = [str(self.bin_dir / "ffmpeg.exe"), "-y"] + inputs + \
                           ["-filter_complex", filter_str, "-map", "[out]", "-b:a", "320k", str(inst_final)]

                try:
                    subprocess.run(merge_cmd, check=True, creationflags=self.subp_flags)
                    found_inst = True
                except Exception as e:
                    self.log(f"  ❌ 合併音軌失敗: {str(e)}")

        self.log("🧹 正在清理暫存檔案...")
        for f in out_path.iterdir():
            if f.suffix == f".{fmt}" and any(kw in f.name for kw in all_kw) and '_karaoke.mp3' not in f.name:
                try: f.unlink()
                except Exception as e:
                    self.log(f"  ⚠️ 清理暫存檔失敗: {f.name} ({str(e)})")
        if Path(input_audio).exists():
            try: os.remove(input_audio)
            except Exception as e:
                self.log(f"  ⚠️ 清理原始音檔失敗: {str(e)}")

        return (str(voc_final) if voc_final.exists() else None,
                str(inst_final) if inst_final.exists() else None)

    def _begin_processing(self, status_msg, target_fn, *args):
        """通用：鎖定 UI、清除日誌、啟動背景執行緒"""
        if self.is_processing:
            return False
        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status(status_msg, "orange")
        threading.Thread(target=target_fn, args=args, daemon=True).start()
        return True

    @staticmethod
    def _safe_remove(path):
        """安全刪除檔案，不存在或失敗時靜默略過"""
        try:
            p = path if hasattr(path, 'unlink') else __import__('pathlib').Path(path)
            if p.exists():
                p.unlink()
        except Exception:
            pass

    def finish_processing(self):
        self.is_processing = False
        self.cancel_event.clear()
        self._current_process = None
        self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.cancel_btn.config(state=tk.DISABLED))
        self.root.after(0, self.refresh_start_button_text)
        self.update_status("準備就緒", "green")
        self.update_progress(0, step_text="")

    def cancel_processing(self):
        """中止當前正在執行的任務"""
        if not self.is_processing:
            return
        self.cancel_event.set()
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
                self.log("⚠️ 已發送中止訊號給子程序...")
            except Exception as e:
                self.log(f"⚠️ 中止子程序時出錯: {str(e)}")
        self.log("🛑 使用者已取消任務。")
        self.cancel_btn.config(state=tk.DISABLED)

    @staticmethod
    def sanitize_filename(title, max_len=80):
        """將標題截短並清除 Windows 非法字元，避免路徑過長導致各種失敗"""
        illegal = r'\/:*?"<>|'
        for ch in illegal:
            title = title.replace(ch, '_')

        title = "".join(char for char in title if char.isprintable())

        title = re.sub(r'[\s_]+', '_', title).strip('_')

        if len(title) > max_len:
            title = title[:max_len]

        while len(title.encode('utf-8', errors='replace')) > 180:
            title = title[:-1]

        return title.strip() or 'video'

    def extract_audio_from_video(self, video_file, mp3_out):
        """用 ffmpeg 從影片檔抽取 MP3 音訊，回傳輸出路徑或 None"""
        ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
        cmd = [str(ffmpeg_exe), "-y", "-i", video_file,
               "-vn", "-acodec", "libmp3lame", "-ab", "320k", mp3_out]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                encoding='utf-8', errors='replace',
                creationflags=self.subp_flags
            )
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
                self.log(f"  ✅ 音訊擷取完成: {os.path.basename(mp3_out)}")
                return mp3_out
            else:
                self.log("  ❌ ffmpeg 音訊擷取失敗")
                return None
        except Exception as e:
            self.log(f"  ❌ 音訊擷取出錯: {str(e)}")
            return None

    def upscale_video_to_1080p(self, input_video: str, *, replace_original: bool = True) -> str:
        """
        強制等比輸出 1080p（不足補黑邊）。
        - 主要用於「純下載」分頁：下載到的 MP4 若不是 1920x1080，也會做一次縮放輸出。
        - replace_original=True：成功後會覆蓋原影片（先輸出暫存檔，再取代原檔名），不留 _1080p 結尾檔。
        - 回傳：成功則回傳（覆蓋後的）原檔案路徑；失敗則回傳原檔案路徑。
        """
        try:
            if not input_video or (not os.path.exists(input_video)):
                return input_video

            # 既有程式使用 _get_video_size() 取得解析度
            w, h = self._get_video_size(input_video)
            if w == 1920 and h == 1080:
                self.log("🖼️ 影片已是 1080p（1920x1080），跳過放大。")
                return input_video

            in_path = Path(input_video)
            # 一律先輸出暫存檔，再視需求覆蓋原檔（避免直接覆蓋失敗造成檔案損毀）
            tmp_out = in_path.with_name(f"{in_path.stem}__tmp_1080p_{int(time.time())}.mp4")

            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            scale_filter = self._get_1080p_scale_pad_filter()
            cmd = [
                str(ffmpeg_exe), "-y",
                "-i", str(in_path),
                "-vf", scale_filter,
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(tmp_out)
            ]

            self.log("🖼️ 正在將影片縮放為 1080p（等比＋補黑邊）...")
            subprocess.run(cmd, check=True, creationflags=self.subp_flags, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
            if tmp_out.exists():
                if replace_original:
                    try:
                        os.replace(str(tmp_out), str(in_path))  # 覆蓋原檔（Windows 也可用）
                        self.log(f"  ✅ 1080p 放大完成（已覆蓋原檔）: {in_path.name}")
                        return str(in_path)
                    except Exception as e:
                        # 覆蓋失敗就保留暫存檔，避免白做
                        self.log(f"  ⚠️ 覆蓋原檔失敗，已保留放大後檔案: {tmp_out.name}")
                        self.log(f"     錯誤訊息: {str(e)}")
                        return str(tmp_out)
                else:
                    self.log(f"  ✅ 1080p 影片已輸出: {tmp_out.name}")
                    return str(tmp_out)
            return input_video
        except subprocess.CalledProcessError as e:
            self.log("  ❌ 1080p 放大失敗（ffmpeg 回傳錯誤）")
            if getattr(e, "stderr", None):
                err_lines = [l for l in e.stderr.splitlines() if l.strip()]
                for line in err_lines[-20:]:
                    self.log(f"    [ffmpeg] {line}")
            return input_video
        except Exception as e:
            self.log(f"  ❌ 1080p 放大失敗: {str(e)}")
            return input_video

    def download_youtube(self, url, output_dir, mode="both", download_subtitles=False, quality="1080"):
        """使用 yt-dlp 下載影片與音訊"""
        self.log("🚀 正在下載 YouTube 內容...")
        self._last_downloaded_subtitle = None

        video_id = self.extract_youtube_video_id(url) or "temp_id"

        ytdlp_cmd_base = self._get_ytdlp_command_base()
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = os.pathsep.join([str(self.ytdlp_dir), str(self.common_lib_dir), str(self.lib_dir)])
        js_runtime_opts = self._get_ytdlp_js_runtime_opts() if download_subtitles else []

        common_opts_with_cookie = self._build_ytdlp_common_opts(js_runtime_opts, force_no_cookie=False)
        common_opts_no_cookie   = self._build_ytdlp_common_opts(js_runtime_opts, force_no_cookie=True)

        def run_ytdlp_with_logging(cmd, step_name):
            self.log(f"  > 正在下載 {step_name}...")
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True, creationflags=self.subp_flags,
                encoding='utf-8', errors='replace', env=ytdlp_env
            )
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
                recent_lines.append(line)
                if len(recent_lines) > 12:
                    recent_lines.pop(0)

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
                            self.update_progress(mapped, f"正在下載 {step_name}", step_text=f"步驟 1/5：下載 {step_name} {int(percent)}%")
                elif any(x in line for x in ["[ffmpeg]", "Merging", "Extracting", "Destination"]):
                    self.log(f"    {line}")
                elif "ERROR" in line.upper():
                    self._log_ytdlp_error(line)
                    recent_errors.append(line)
                    if len(recent_errors) > 6:
                        recent_errors.pop(0)

            process.wait()
            self._current_process = None
            if process.returncode != 0 and recent_errors:
                self.log(f"  ⚠️ {step_name} 失敗摘要：{recent_errors[-1][:220]}")
            elif process.returncode != 0 and recent_lines:
                self.log(f"  ⚠️ {step_name} 最後輸出：{recent_lines[-1][:220]}")
            return process.returncode == 0, (recent_errors or recent_lines), has_cookie_error

        def find_downloaded_file(pattern):
            """使用 glob 尋找包含特定 ID 的檔案，解決 Windows 編碼導致的路徑變數亂碼問題"""
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
                    self.log(f"  📝 影片標題: {raw_title}")
                    self.log(f"  📝 安全檔名: {safe_name}")
                    break
        except Exception as e:
            self.log(f"  ⚠️ 取得標題失敗，使用影片 ID 作為檔名: {str(e)}")

        if download_subtitles and mode in ["both", "mp4"]:
            self._last_downloaded_subtitle = self.download_youtube_subtitle(url, output_dir, video_id)

        if mode in ["both", "mp4"]:
            mp4_out = os.path.join(output_dir, f"{safe_name}.mp4")
            video_format = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                if quality == "best" else
                f"bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/best[ext=mp4][height<={quality}]/best[ext=mp4]/best"
            )

            current_common_opts = common_opts_with_cookie
            try_no_cookie = True

            mp4_ok = False
            last_mp4_errors = []

            while try_no_cookie:
                try_no_cookie = False

                mp4_attempts = [
                    (
                        "MP4 影片",
                        ytdlp_cmd_base + current_common_opts + [
                            "-f", video_format,
                            "--merge-output-format", "mp4",
                            "-o", mp4_out,
                            url
                        ]
                    ),
                    (
                        "MP4 相容模式",
                        ytdlp_cmd_base + current_common_opts + [
                            "-f", "bv*+ba/b",
                            "--recode-video", "mp4",
                            "-o", mp4_out,
                            url
                        ]
                    )
                ]

                for idx, (attempt_name, mp4_cmd) in enumerate(mp4_attempts, start=1):
                    if idx > 1:
                        self.log("  ℹ️ 主要 MP4 格式失敗，改用相容模式重試...")
                    success, err_lines, has_cookie_error = run_ytdlp_with_logging(mp4_cmd, attempt_name)
                    last_mp4_errors = err_lines

                    if has_cookie_error and current_common_opts is common_opts_with_cookie:
                        self.log("  ℹ️ 偵測到 Cookie 錯誤，正在切換到無 Cookie 模式重試...")
                        current_common_opts = common_opts_no_cookie
                        try_no_cookie = True
                        break

                    if not success:
                        continue

                    if os.path.exists(mp4_out):
                        video_file = mp4_out
                        mp4_ok = True
                        self.log(f"  ✅ MP4 下載完成: {os.path.basename(video_file)}")
                        if self._last_downloaded_subtitle:
                            self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, video_file)
                        break
                    else:
                        video_file = find_downloaded_file("*.mp4")
                        if video_file:
                            mp4_ok = True
                            self.log(f"  ✅ MP4 下載完成: {os.path.basename(video_file)}")
                            if self._last_downloaded_subtitle:
                                self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, video_file)
                            break

                if mp4_ok:
                    break

            if not mp4_ok:
                if last_mp4_errors:
                    self.log(f"  ❌ MP4 下載失敗摘要：{last_mp4_errors[-1][:220]}")
                self.log("  ❌ MP4 下載過程出錯")
                if mode == "both": return None, None
                else: return None
            elif not video_file:
                self.log("  ❌ MP4 下載失敗: 找不到下載後的檔案")
                if mode == "both": return None, None
                else: return None

        if mode in ["both", "mp3"]:
            self.log("  > 正在準備 MP3 音訊...")
            mp3_out = os.path.join(output_dir, f"{safe_name}.mp3")
            mp3_ok = False

            # 第一頁/第二頁統一：一律用 yt-dlp 直接抓 MP3（不再用 ffmpeg 從 MP4 擷取）
            current_common_opts_mp3 = common_opts_with_cookie
            while True:
                mp3_cmd = ytdlp_cmd_base + current_common_opts_mp3 + [
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "mp3", "--audio-quality", "320K",
                    "--keep-video",
                    "-o", mp3_out, url
                ]
                success, err_lines, has_cookie_error = run_ytdlp_with_logging(mp3_cmd, "MP3 音訊")

                if has_cookie_error and current_common_opts_mp3 is common_opts_with_cookie:
                    self.log("  ℹ️ 偵測到 Cookie 錯誤，正在切換到無 Cookie 模式重試...")
                    current_common_opts_mp3 = common_opts_no_cookie
                    continue

                if success:
                    if os.path.exists(mp3_out):
                        audio_file = mp3_out
                        self.log(f"  ✅ MP3 下載完成: {os.path.basename(audio_file)}")
                        mp3_ok = True
                    else:
                        audio_file = find_downloaded_file("*.mp3")
                        if audio_file:
                            self.log(f"  ✅ MP3 下載完成: {os.path.basename(audio_file)}")
                            mp3_ok = True
                break

            if not mp3_ok:
                self.log("  ❌ MP3 下載過程出錯")
                if mode == "both": return video_file, None
                else: return None

        if mode == "both":
            return video_file, audio_file
        else:
            return video_file if mode == "mp4" else audio_file

    def download_youtube_subtitle(self, url, output_dir, video_id):
        """下載 YouTube 字幕，若同時存在多語字幕則讓使用者選擇。"""
        self.log("  > 正在檢查 YouTube CC 字幕...")

        ytdlp_cmd_base = self._get_ytdlp_command_base()

        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)
        ytdlp_env["PYTHONIOENCODING"] = "utf-8"
        js_runtime_opts = self._get_ytdlp_js_runtime_opts()
        cookie_opts = self._get_cookie_opts()

        subtitle_out = os.path.join(output_dir, f"{video_id}.%(ext)s")
        subtitle_patterns = [ f"{video_id}*.srt", f"{video_id}*.vtt", f"{video_id}*.ass", f"{video_id}*.srv3", ]
        def clear_old_subtitles():
            for pattern in subtitle_patterns:
                for old_file in Path(output_dir).glob(pattern):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass

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
            """取得所有可用的字幕語言清單，並標註是否為手動字幕"""
            lang_display_map = {
                "zh-TW": "繁體中文 (台灣)",
                "zh-Hant": "繁體中文",
                "zh-HK": "繁體中文 (香港)",
                "cmn-Hant": "繁體中文",
                "zh-CN": "簡體中文 (中國)",
                "zh-Hans": "簡體中文",
                "cmn-Hans": "簡體中文",
                "zh": "中文",
                "en": "英文",
                "ja-orig": "日文 (原始)",
                "ja": "日文",
                "ko": "韓文",
                "fr": "法文",
                "de": "德文",
                "es": "西班牙文",
                "pt": "葡萄牙文",
                "it": "義大利文",
                "ru": "俄文",
                "ar": "阿拉伯文",
                "hi": "印地文",
                "th": "泰文",
                "vi": "越南文",
                "id": "印尼文",
                "ms": "馬來文",
                "tl": "他加祿語",
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
                        self.log(f"  ⚠️ 讀取字幕語言清單失敗（代碼 {result.returncode}）：{err_preview[:180]}")
                    return []

                data = json.loads(result.stdout)
                manual = {k for k in (data.get("subtitles") or {}).keys() if k and k != "live_chat"}
                auto = {k for k in (data.get("automatic_captions") or {}).keys() if k and k != "live_chat"}

                langs_with_info = []

                for lang in sorted(manual):
                    display_name = lang_display_map.get(lang, lang)
                    langs_with_info.append((lang, display_name, True))

                for lang in sorted(auto):
                    if lang not in manual:
                        display_name = lang_display_map.get(lang, lang)
                        langs_with_info.append((lang, display_name, False))

                return langs_with_info
            except Exception as e:
                self.log(f"  ⚠️ 讀取字幕語言清單失敗，改用精簡策略重試：{str(e)}")
                return []

        def show_language_selection_dialog(langs_with_info):
            """顯示語言選擇對話框，讓使用者選擇要下載的字幕語言"""
            selected_lang = [None]

            dialog = tk.Toplevel(self.root)
            dialog.title("選擇字幕語言")
            dialog.geometry("500x400")
            dialog.transient(self.root)
            dialog.grab_set()

            dialog.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")

            tk.Label(dialog, text="請選擇要下載的字幕語言：", font=("Arial", 12)).pack(pady=10)

            listbox_frame = tk.Frame(dialog)
            listbox_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

            listbox = tk.Listbox(listbox_frame, font=("Arial", 11))
            scrollbar = tk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=listbox.yview)
            listbox.configure(yscrollcommand=scrollbar.set)

            listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            for i, (lang_code, display_name, is_manual) in enumerate(langs_with_info):
                tag = "手動字幕" if is_manual else "自動字幕"
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

            tk.Button(btn_frame, text="確定", command=on_select, bg="#4CAF50", fg="white", width=12).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="取消", command=on_cancel, width=12).pack(side=tk.LEFT, padx=10)

            self.root.wait_window(dialog)
            return selected_lang[0]

        langs_with_info = get_available_langs_from_metadata()

        if not langs_with_info:
            self.log("  ℹ️ 這支影片沒有可用的 YouTube CC 字幕。")
            return None

        self.log(f"  ℹ️ 找到 {len(langs_with_info)} 種可用字幕語言")

        selected_lang = None
        try:
            selected_lang = show_language_selection_dialog(langs_with_info)
        except Exception as e:
            self.log(f"  ⚠️ 顯示語言選擇對話框失敗：{str(e)}")

        if not selected_lang:
            self.log("  ℹ️ 使用者取消字幕下載。")
            return None

        self.log(f"  > 已選擇字幕語言：{selected_lang}")

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

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True,
            creationflags=self.subp_flags, encoding='utf-8', errors='replace',
            env=ytdlp_env
        )
        self._current_process = process

        for line in process.stdout:
            if self.cancel_event.is_set():
                process.terminate()
                self.log("🛑 字幕下載已取消。")
                self._current_process = None
                return None
            line = line.strip()
            if not line:
                continue
            if any(token in line for token in ["subtitle", "Subtitles", "Writing video subtitles", "Deleting original file"]):
                self.log(f"    {line}")
            elif "WARNING" in line.upper():
                self.log(f"  ⚠️ {line}")
            elif "ERROR" in line.upper():
                self.log(f"  ❌ {line}")

        process.wait()
        self._current_process = None
        subtitle_candidates = collect_subtitle_candidates()

        if subtitle_candidates:
            if process.returncode != 0:
                self.log("  ⚠️ 字幕下載程序部分失敗，但已找到可用字幕檔，將直接採用。")
            selected = subtitle_candidates[0]
            self.log(f"  ✅ 已找到字幕檔：{selected.name}")
            return str(selected)
        else:
            self.log(f"  ⚠️ 選擇的語言 {selected_lang} 沒有可下載字幕。")
            return None

    def synthesize_mkv(self, video_file, vocal_file, instrumental_file, output_file, subtitle_file=None):
        """合成 KTV 伴唱帶：支援雙音軌模式與左伴唱/右人聲單音軌模式"""
        vfmt = self.video_format_var.get().upper()
        track_mode = self.audio_track_mode_var.get()  # "dual" or "lr"
        vocal_mix = max(0.0, min(1.0, float(self.vocal_mix_var.get()) / 100.0))
        instrumental_mix = max(0.0, 1.0 - vocal_mix)
        vocal_pct = int(round(vocal_mix * 100))
        inst_pct = int(round(instrumental_mix * 100))
        force_1080p = self.force_1080p_var.get()

        self.log(f"🎬 正在合成 {vfmt} 伴唱帶（音軌模式：{'雙音軌' if track_mode == 'dual' else '左伴唱/右人聲+伴奏' if track_mode == 'lr' else '純伴唱'}）...")
        if track_mode == "dual":
            self.log(f"🎚️ 導唱混合比例：人聲 {vocal_pct}% / 伴奏 {inst_pct}%")
        elif track_mode == "inst":
            self.log("🎚️ 純伴唱模式：僅輸出伴奏音軌，不含人聲。")
        else:
            self.log("🎚️ 目前為左伴唱／右人聲+伴奏模式，混合比例設定不套用於此模式。")
        if force_1080p:
            self.log("🖼️ 已啟用強制等比輸出 1080p，必要時會補黑邊。")
        subtitle_file = None

        missing = []
        if not os.path.exists(video_file): missing.append(f"影片檔: {os.path.basename(video_file)}")
        if not os.path.exists(vocal_file): missing.append(f"人聲檔: {os.path.basename(vocal_file)}")
        if not os.path.exists(instrumental_file): missing.append(f"伴奏檔: {os.path.basename(instrumental_file)}")

        if missing:
            self.log("  ❌ 合成失敗，缺少必要檔案：\n    - " + "\n    - ".join(missing))
            return False

        ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
        cmd = [str(ffmpeg_exe), "-y", "-i", str(video_file)]

        if track_mode == "lr":
            import tempfile, os as _os
            lr_tmp = Path(tempfile.mktemp(suffix="_lr_stereo.mp3"))
            self.log("🎚️ 正在製作「左伴奏／右人聲+伴奏」立體聲音軌...")
            if not self.create_lr_stereo(str(instrumental_file), str(vocal_file), str(lr_tmp)):
                self.log("  ❌ 無法製作 LR 立體聲音軌，合成中止。")
                return False

            cmd += ["-i", str(lr_tmp)]   # 1: 已完成的 LR 立體聲

            audio_filter = None          # 不需要音訊 filter
            audio_maps = [ "-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "title=左伴唱／右人聲+伴奏", ]
        elif track_mode == "inst":
            cmd += ["-i", str(instrumental_file)]   # 1: 伴奏

            audio_filter = None
            audio_maps = [ "-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "title=伴唱 (純伴奏)", ]
        else:
            cmd += [ "-i", str(vocal_file), "-i", str(instrumental_file), ]
            audio_filter = f"[1:a][2:a]amix=inputs=2:duration=first:weights='{vocal_mix:.2f} {instrumental_mix:.2f}'[mix]"
            audio_maps = [
                "-map", "0:v",
                "-map", "[mix]",                 # 音軌 1: 導唱 (人聲+伴奏)
                "-map", "2:a",                   # 音軌 2: 純伴奏
                "-metadata:s:a:0", f"title=導唱 (人聲{vocal_pct}% + 伴奏{inst_pct}%)",
                "-metadata:s:a:1", "title=伴唱 (純伴奏)",
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
            self.log(f"  ❌ {vfmt} 合成出錯 (代碼: {e.returncode})")
            if e.stderr:
                err_lines = [l for l in e.stderr.splitlines() if l.strip()]
                for line in err_lines[-20:]:
                    self.log(f"    [ffmpeg] {line}")
            return False
        except Exception as e:
            self.log(f"  ❌ {vfmt} 合成出錯: {str(e)}")
            return False
        finally:
            if track_mode == "lr":
                try:
                    if lr_tmp.exists():
                        lr_tmp.unlink()
                except Exception:
                    pass

    def batch_process(self):
        start_time = time.time()
        total = len(self.file_list)
        output_dir = self.output_dir_var.get()
        enable_lyrics = self.enable_lyrics_recognition_var.get()

        for i, input_file in enumerate(self.file_list):
            if self.cancel_event.is_set():
                self.log("🛑 批次分離已中止。")
                break
            if not os.path.exists(input_file):
                self.log(f"⚠️ 找不到檔案: {input_file}")
                continue

            self.log(f"--- 正在處理 ({i+1}/{total}): {os.path.basename(input_file)} ---")
            self.update_progress(int(i / total * 100), f"正在處理 {i+1}/{total}")

            success = self.run_audio_separator(input_file, output_dir)

            if success:
                self.log(f"✅ 檔案處理完成: {os.path.basename(input_file)}")

                if enable_lyrics:
                    self.recognize_lyrics_and_generate_srt(input_file, output_dir)
            else:
                self.log(f"❌ 檔案處理失敗: {os.path.basename(input_file)}，請檢查上方日誌。")

        self.update_progress(100, "全部完成")
        elapsed_time = time.time() - start_time
        self.log(f"⏱️ 批次處理完成，總花費時間: {elapsed_time:.2f} 秒 (已處理 {total} 個檔案)")
        self.update_status("批次處理完成！", "green")
        messagebox.showinfo("成功", f"批次處理完成！\n已處理 {total} 個檔案。\n總花費時間: {elapsed_time:.2f} 秒")
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

# 建立 logger（和 cli.py 一樣）
logger = logging.getLogger(__name__)
log_handler = logging.StreamHandler()
log_formatter = logging.Formatter(fmt="%(asctime)s.%(msecs)03d - %(levelname)s - %(module)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

# 解析參數（和 cli.py 一樣）
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

# 建立 Separator，加入 use_directml 參數！
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

# 處理模型和分離（和 cli.py 一樣）
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
                self.log(f"  🔍 正在檢查輸出目錄: {out_path}")
                self.log(f"  🔍 預期前綴: {input_stem}")

                for fname in os.listdir(str(out_path)):
                    if fname.endswith(f".{fmt}"):
                        found_files.append(fname)
                        if any(kw in fname for kw in keywords):
                            self.log(f"  ✅ 找到輸出檔案: {fname}")
                            return True

                self.log(f"  ⚠️ 找到的檔案數量: {len(found_files)}")
                for f in found_files[:5]:  # 只顯示前5個
                    self.log(f"    - {f}")
            except Exception as e:
                self.log(f"  ❌ 檢查輸出檔案時出錯: {str(e)}")
                self.log(f"     {traceback.format_exc()}")
            return False

        def run_model(model_name, is_retry=False):
            command, is_demucs = build_command(model_name)

            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding='utf-8', errors='replace',
                    creationflags=self.subp_flags, env=env
                )
                self._current_process = process

                gpu_kernel_error = False
                unsupported_model_error = False
                unsupported_model_hash = None

                for line in process.stdout:
                    if self.cancel_event.is_set():
                        process.terminate()
                        self.log("🛑 AI 分離任務已取消。")
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
                                    self.update_progress(mapped, "正在分離人聲與伴奏", step_text=f"步驟 3/5：AI 人聲分離中 {pct}%")
                            except Exception:
                                pass

                process.wait()
                self._current_process = None

                if process.returncode == 0 and not gpu_kernel_error and not unsupported_model_error:
                    if has_output_files():
                        return True, "success"
                    self.log("❌ 雖然程式回報成功，但未能在輸出目錄找到產出的音檔。")
                    return False, "missing_output"

                if gpu_kernel_error:
                    self.log("\n❌ 偵測到 GPU 核心錯誤 (no kernel image)。")
                    self.log("💡 這通常是因為您的 GPU 太新 (RTX 50 系列)，目前的穩定版組件尚未完全支援。")
                    self.log("💡 建議：請在主介面將「運算裝置」切換為 CPU 模式運行，或嘗試執行「一鍵修復」升級至最新實驗性核心。")
                    return False, "gpu_kernel"

                if unsupported_model_error:
                    model_path = self.models_dir / model_name
                    self.log("\n❌ 偵測到 UVR 模型參數不相容。")
                    if unsupported_model_hash:
                        self.log(f"💡 此模型的 MD5 雜湊值為: {unsupported_model_hash}")
                    self.log(f"💡 目前模型 `{model_name}` 的內容不在 audio-separator 內建支援表中。")
                    if model_path.exists():
                        self.log(f"💡 建議刪除後重新下載模型檔: {model_path}")
                    else:
                        self.log("💡 這通常代表模型檔下載不完整、版本不相容，或內容已被替換。")

                    if not is_demucs and model_name != fallback_model:
                        if not is_retry:
                            self.log(f"🔁 將自動改用 `{fallback_model}` 再重試一次...")
                            return False, "retry_with_demucs"
                    else:
                        self.log("💡 可改用 `htdemucs.yaml` 或 `htdemucs_ft.yaml` 進行分離。")
                    return False, "unsupported_model"

                return False, "process_failed"
            except Exception as e:
                self.log(f"執行錯誤: {str(e)}")
                self._current_process = None
                return False, "exception"

        device_display = "NVIDIA GPU" if device == "cuda" else ("DirectML" if device == "directml" else "CPU")
        success, reason = run_model(selected_model, is_retry=False)
        if success:
            elapsed_time = time.time() - start_time
            self.log(f"⏱️ 音訊分離完成，總花費時間: {elapsed_time:.2f} 秒 (裝置: {device_display})")
            return True

        if reason == "retry_with_demucs":
            self.log(f"🎯 回退模型: {selected_model} → {fallback_model}")
            retry_success, retry_reason = run_model(fallback_model, is_retry=True)
            if retry_success:
                elapsed_time = time.time() - start_time
                self.log(f"⏱️ 音訊分離完成，總花費時間: {elapsed_time:.2f} 秒 (裝置: {device_display})")
                self.log(f"✅ 已改用 `{fallback_model}` 完成音訊分離。")
                self.log("💡 若想恢復使用原本的 MDX 模型，請刪除舊的 .onnx 後重新下載。")
                return True

            if retry_reason == "unsupported_model":
                self.log("❌ 備援模型也無法載入，請執行「一鍵修復/初始化環境」，或手動清理模型目錄後再試。")
            else:
                self.log("❌ 已嘗試自動切換備援模型，但仍未成功完成分離。")

        return False

    def _browse_video_file(self, title="選擇影片檔案"):
        """通用：彈出影片選擇對話框，回傳路徑字串或 None"""
        return filedialog.askopenfilename(
            title=title,
            filetypes=[("影片檔案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有檔案", "*.*")]
        )

    def browse_merge_video(self):
        file_path = self._browse_video_file()
        if file_path:
            self.merge_video_path_var.set(file_path)
            self.player_video_path = file_path
            self.reload_player()

    def browse_merge_subtitle(self):
        file_path = filedialog.askopenfilename( title="選擇字幕檔案", filetypes=[("字幕檔案", "*.srt *.ass *.ssa *.json"), ("所有檔案", "*.*")] )
        if file_path:
            self.merge_subtitle_path_var.set(file_path)
            self.player_subtitle_path = file_path
            is_json = file_path.lower().endswith('.json')
            is_srt  = file_path.lower().endswith('.srt')
            if is_json or is_srt:
                self.ktv_color_frame.pack(fill=tk.X, pady=5)
                if is_json:
                    self.ktv_color_frame.config(text="KTV 逐字漸變顏色設定")
                    self.unplayed_label.config(text="未唱顏色:")
                    self.played_row.pack(fill=tk.X, pady=5)
                    if hasattr(self, 'two_line_row'):
                        self.two_line_row.pack(fill=tk.X, pady=5)
                    if hasattr(self, 'two_line_x_row'):
                        self.two_line_x_row.pack(fill=tk.X, pady=5)
                else:
                    self.ktv_color_frame.config(text="字幕樣式設定")
                    self.unplayed_label.config(text="字幕顏色:")
                    self.played_row.pack_forget()
                    if hasattr(self, 'two_line_row'):
                        self.two_line_row.pack_forget()
                    if hasattr(self, 'two_line_x_row'):
                        self.two_line_x_row.pack_forget()
            else:
                self.ktv_color_frame.pack_forget()
            # 動態顯示「將 JSON 轉 ASS 字幕」按鈕
            if hasattr(self, 'json_to_ass_btn') and hasattr(self, 'merge_start_btn'):
                if is_json:
                    self.json_to_ass_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
                else:
                    self.json_to_ass_btn.pack_forget()
            self.reload_player()

    def browse_rec_video(self):
        file_path = self._browse_video_file()
        if file_path:
            self.rec_video_path_var.set(file_path)

    def start_recognize_lyrics(self):
        video_path = self.rec_video_path_var.get().strip()

        if not video_path:
            messagebox.showwarning("警告", "請選擇影片檔案！")
            return
        if not os.path.exists(video_path):
            messagebox.showerror("錯誤", "找不到影片檔案！")
            return
        if self.is_processing:
            return

        self._begin_processing("正在辨識歌詞...", self.recognize_lyrics_process, video_path)

    def recognize_lyrics_process(self, video_path):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)

            import tempfile
            temp_dir = tempfile.gettempdir()
            video_stem = Path(video_path).stem
            original_video_stem = video_stem
            temp_audio = os.path.join(temp_dir, f"{video_stem}_temp_audio.mp3")

            self.update_status("正在提取音訊...", "orange")
            self.log("\n🎤 開始從影片提取音訊...")

            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            ffmpeg_cmd = [ str(ffmpeg_exe), "-i", video_path, "-vn", "-acodec", "libmp3lame", "-ab", "192k", "-ar", "44100", "-y", temp_audio ]

            self.log(f"  > 執行: {' '.join(ffmpeg_cmd)}")

            process = subprocess.Popen( ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=self.subp_flags )
            self._current_process = process

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    self.log("🛑 已取消操作")
                    return
                try:
                    line_decoded = line.decode('utf-8', errors='replace').strip()
                except:
                    line_decoded = line.decode('cp950', errors='replace').strip()
                if line_decoded:
                    self.log(f"    {line_decoded}")

            process.wait()
            self._current_process = None

            if process.returncode != 0:
                self.log("❌ 提取音訊失敗")
                return

            self.log("✅ 音訊提取完成")

            separate_first = self.rec_separate_first_var.get()
            audio_for_recognition = temp_audio  # 預設直接用全音軌

            if separate_first:
                self.update_status("正在分離人聲...", "orange")
                self.log("\n🎵 先進行人聲分離以提升辨識準確度...")

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

                    safe_stem = AudioSeparatorApp.sanitize_filename(original_video_stem, max_len=60)
                    if found_vocal:
                        vocal_final = Path(output_dir) / f"{safe_stem}_人聲{found_vocal.suffix}"
                        try:
                            if vocal_final.exists():
                                vocal_final.unlink()
                            shutil.move(str(found_vocal), str(vocal_final))
                            found_vocal = vocal_final
                        except Exception as e:
                            self.log(f"  ⚠️ 人聲檔重新命名失敗: {e}")
                        self.log(f"  ✅ 人聲檔案已儲存: {found_vocal.name}")
                        audio_for_recognition = str(found_vocal)
                    else:
                        self.log("  ⚠️ 找不到分離後的人聲檔，改用原始音訊辨識")

                    if found_inst:
                        inst_final = Path(output_dir) / f"{safe_stem}_伴奏{found_inst.suffix}"
                        try:
                            if inst_final.exists():
                                inst_final.unlink()
                            shutil.move(str(found_inst), str(inst_final))
                        except Exception as e:
                            self.log(f"  ⚠️ 伴奏檔重新命名失敗: {e}")
                            inst_final = found_inst
                        self.log(f"  ✅ 伴奏檔案已儲存: {inst_final.name}")
                else:
                    self.log("  ⚠️ 人聲分離失敗，改用原始音訊辨識")

            self.update_status("正在進行 AI 歌詞識別...", "orange")
            self.update_progress(55, "正在識別歌詞", step_text="步驟 3/5：準備載入 Whisper 模型...")
            rec_lang = self.rec_lyrics_language_var.get() if hasattr(self, 'rec_lyrics_language_var') else "traditional"
            result = self.recognize_lyrics_and_generate_srt(audio_for_recognition, output_dir, original_video_stem, override_language=rec_lang)

            if temp_audio and os.path.exists(temp_audio):
                try:
                    os.remove(temp_audio)
                except Exception:
                    pass

            if result:
                srt_file, json_file, _ = result
                self.update_progress(100, "完成")
                self.log(f"\n✅ 歌詞辨識完成！")
                self.log(f"   SRT 字幕: {Path(srt_file).name}")
                self.log(f"   JSON 歌詞: {Path(json_file).name}")
                self._show_done_and_open("完成", f"歌詞辨識完成！\n\nSRT 字幕: {Path(srt_file).name}\nJSON 歌詞: {Path(json_file).name}\n\n檔案已儲存至: {output_dir}", output_dir, select_file=str(srt_file))
            else:
                self.log("❌ 歌詞辨識失敗")
                messagebox.showerror("錯誤", "歌詞辨識失敗，請查看日誌！")

        except Exception as e:
            self.log(f"❌ 發生錯誤: {str(e)}")
            self.log(f"   {traceback.format_exc()}")
            messagebox.showerror("錯誤", f"發生錯誤: {str(e)}")
        finally:
            # 一律走共同收尾：解鎖 UI、刷新主按鈕文字/狀態
            self.finish_processing()

    def _validate_json_inputs(self):
        """驗證 JSON 按鈕共用的路徑，回傳 (video_path, subtitle_path) 或 (None, None)"""
        subtitle_path = self.merge_subtitle_path_var.get().strip()
        if not subtitle_path:
            messagebox.showwarning("警告", "請選擇字幕檔案！")
            return None, None
        if not os.path.exists(subtitle_path):
            messagebox.showerror("錯誤", "找不到字幕檔案！")
            return None, None
        if not subtitle_path.lower().endswith('.json'):
            messagebox.showwarning("警告", "這三個按鈕僅適用於 JSON 字幕檔案！")
            return None, None
        if self.is_processing:
            return None, None
        video_path = self.merge_video_path_var.get().strip()
        return video_path, subtitle_path

    def _validate_json_video_inputs(self):
        """驗證 JSON + 影片兩者路徑，回傳 (video_path, subtitle_path) 或 (None, None)"""
        video_path, subtitle_path = self._validate_json_inputs()
        if subtitle_path is None:
            return None, None
        if not video_path:
            messagebox.showwarning("警告", "請選擇影片檔案！")
            return None, None
        if not os.path.exists(video_path):
            messagebox.showerror("錯誤", "找不到影片檔案！")
            return None, None
        return video_path, subtitle_path

    def _read_two_line_settings(self):
        """讀取雙行字幕 GUI 設定，回傳 (enabled, advance_sec, top_x, bottom_x, line_gap_px)"""
        two_line_enabled = False
        advance_sec = 1.5
        top_x_offset = 0.0
        bottom_x_offset = 0.0
        line_gap_px = None  # None = 自動（使用字體大小公式）
        try:
            if hasattr(self, 'ktv_two_line_var') and self.ktv_two_line_var.get():
                two_line_enabled = True
            if hasattr(self, 'ktv_two_line_advance_var'):
                advance_sec = float(self.ktv_two_line_advance_var.get())
            if hasattr(self, 'ktv_two_line_top_x_var'):
                top_x_offset = float(self.ktv_two_line_top_x_var.get())
            if hasattr(self, 'ktv_two_line_bottom_x_var'):
                bottom_x_offset = float(self.ktv_two_line_bottom_x_var.get())
            if hasattr(self, 'ktv_two_line_gap_var'):
                raw = self.ktv_two_line_gap_var.get().strip()
                if raw:
                    line_gap_px = int(float(raw))
        except Exception:
            two_line_enabled = False
            advance_sec = 1.5
            top_x_offset = 0.0
            bottom_x_offset = 0.0
            line_gap_px = None
        if advance_sec < 0:
            advance_sec = 0.0
        print(f"[DEBUG] _read_two_line_settings → two_line={two_line_enabled} advance={advance_sec} line_gap_px={line_gap_px!r}")
        return two_line_enabled, advance_sec, top_x_offset, bottom_x_offset, line_gap_px

    def _collect_json_to_ass_kwargs(self, video_path=None):
        """收集呼叫 _json_to_ass 所需的所有 GUI 設定，回傳 kwargs dict"""
        two_line, advance_sec, top_x, bottom_x, line_gap_px = self._read_two_line_settings()
        use_played_border = hasattr(self, 'ktv_use_played_border_var') and self.ktv_use_played_border_var.get()
        played_border_color = (self.ktv_played_border_color_var.get().strip()
                               if (use_played_border and hasattr(self, 'ktv_played_border_color_var')) else None)
        vw, vh = self._get_video_size(video_path) if video_path else (None, None)
        return dict(
            unplayed_color=self.ktv_unplayed_color_var.get().strip() or "#FFFFFF",
            played_color=self.ktv_played_color_var.get().strip() or "#0000FF",
            border_color=self.ktv_border_color_var.get().strip() or "#000000",
            font_name=self.ktv_font_var.get() if hasattr(self, 'ktv_font_var') else "微軟正黑體",
            margin_v_offset=int(self.subtitle_margin_var.get()) if hasattr(self, 'subtitle_margin_var') else 0,
            color_mode="slide",
            font_size=int(self.ktv_font_size_var.get()) if hasattr(self, 'ktv_font_size_var') else 60,
            two_line=two_line,
            advance_sec=advance_sec,
            top_x_offset=top_x,
            bottom_x_offset=bottom_x,
            line_gap_px=line_gap_px,
            played_border_color=played_border_color,
            use_clip_mask=True,
            pre_show_sec=float(self.ktv_pre_show_var.get()) if hasattr(self, 'ktv_pre_show_var') else 0.0,
            hold_sec=float(self.ktv_hold_sec_var.get()) if hasattr(self, 'ktv_hold_sec_var') else 0.0,
            speed_factor=float(self.ktv_speed_factor_var.get()) if hasattr(self, 'ktv_speed_factor_var') else 1.0,
            singing_end_ratio=float(self.ktv_singing_end_ratio_var.get()) if hasattr(self, 'ktv_singing_end_ratio_var') else 1.0,
            video_width=vw,
            video_height=vh,
            outline_size=int(self.ktv_border_map_expand_factor_var.get()) if hasattr(self, 'ktv_border_map_expand_factor_var') else 4,
        )

    def _json_process_common_setup(self, video_path):
        """各 JSON process 方法共用的開頭：取得 output_dir、vfmt、output_file"""
        output_dir = self.output_dir_var.get()
        os.makedirs(output_dir, exist_ok=True)
        vfmt = self.merge_video_format_var.get()
        output_file = str(Path(output_dir) / f"{Path(video_path).stem}_含字幕.{vfmt}")
        return output_dir, vfmt, output_file

    def start_json_burn(self):
        """JSON 轉 ASS 後燒錄進影片（重新編碼）"""
        video_path, subtitle_path = self._validate_json_video_inputs()
        if subtitle_path is None:
            return
        self._begin_processing("JSON 轉 ASS 燒錄中...", self._json_burn_process, video_path, subtitle_path)

    def _select_file_in_explorer(self, file_path):
        """
        在已開著的 Explorer 視窗中選取檔案（不開新視窗）。
        若該資料夾尚未開著，才開新視窗。
        全程用一段 PowerShell 腳本處理，不依賴 ctypes COM。
        """
        try:
            file_path = os.path.normpath(str(file_path))
            folder_path = os.path.normpath(str(Path(file_path).parent))

            # 這段 PowerShell：
            # 1. 找出路徑相符的現有 Explorer 視窗
            # 2. 若找到 → 把視窗帶到前面，再用 Shell SelectItem 選取
            # 3. 若沒找到 → 用 explorer /select 開新視窗
            ps_script = r"""
param([string]$FilePath, [string]$FolderPath)
Add-Type -AssemblyName Microsoft.VisualBasic
$shell = New-Object -ComObject Shell.Application
$found = $false
foreach ($win in $shell.Windows()) {
    try {
        $winPath = $win.Document.Folder.Self.Path
        if ([System.IO.Path]::GetFullPath($winPath).ToLower() -eq [System.IO.Path]::GetFullPath($FolderPath).ToLower()) {
            # 視窗已開著：帶到最前面
            $hwnd = $win.HWND
            $sig = '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);'
            $t = Add-Type -MemberDefinition $sig -Name WinAPI -Namespace NativeMethods -PassThru -ErrorAction SilentlyContinue
            if ($t) {
                $t::ShowWindow([IntPtr]$hwnd, 9)
                $t::SetForegroundWindow([IntPtr]$hwnd)
            }
            # 選取目標檔案
            foreach ($item in $win.Document.Folder.Items()) {
                if ([System.IO.Path]::GetFullPath($item.Path).ToLower() -eq [System.IO.Path]::GetFullPath($FilePath).ToLower()) {
                    $win.Document.SelectItem($item, 29)
                    break
                }
            }
            $found = $true
            break
        }
    } catch {}
}
if (-not $found) {
    Start-Process explorer.exe -ArgumentList "/select,`"$FilePath`""
}
"""
            # 把腳本寫到暫存檔再執行，避免命令列長度限制與引號跳脫問題
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ps1',
                                            delete=False, encoding='utf-8') as tf:
                tf.write(ps_script)
                tmp_ps1 = tf.name

            subprocess.Popen(
                ['powershell', '-NoProfile', '-NonInteractive',
                 '-ExecutionPolicy', 'Bypass',
                 '-File', tmp_ps1,
                 '-FilePath', file_path,
                 '-FolderPath', folder_path],
                creationflags=self.subp_flags
            )
            # 腳本執行完後刪暫存（延遲刪，給 powershell 時間讀取）
            def _cleanup(p):
                import time as _t
                _t.sleep(5)
                try:
                    os.remove(p)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(tmp_ps1,), daemon=True).start()

        except Exception:
            # 兜底：直接用 explorer /select
            try:
                subprocess.Popen(
                    ['explorer', f'/select,{os.path.normpath(str(file_path))}'],
                    creationflags=self.subp_flags
                )
            except Exception:
                pass


    def _open_folder_no_dup(self, folder_path):
        """開啟資料夾，若已有 Explorer 視窗開著就不重複開。"""
        try:
            folder_path = os.path.normpath(str(folder_path))
            ps_script = r"""
param([string]$FolderPath)
$shell = New-Object -ComObject Shell.Application
foreach ($win in $shell.Windows()) {
    try {
        $winPath = $win.Document.Folder.Self.Path
        if ([System.IO.Path]::GetFullPath($winPath).ToLower() -eq [System.IO.Path]::GetFullPath($FolderPath).ToLower()) {
            $hwnd = $win.HWND
            $sig = '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);'
            $t = Add-Type -MemberDefinition $sig -Name WinAPI2 -Namespace NativeMethods2 -PassThru -ErrorAction SilentlyContinue
            if ($t) {
                $t::ShowWindow([IntPtr]$hwnd, 9)
                $t::SetForegroundWindow([IntPtr]$hwnd)
            }
            exit 0
        }
    } catch {}
}
Start-Process explorer.exe -ArgumentList "`"$FolderPath`""
"""
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ps1',
                                            delete=False, encoding='utf-8') as tf:
                tf.write(ps_script)
                tmp_ps1 = tf.name
            subprocess.Popen(
                ['powershell', '-NoProfile', '-NonInteractive',
                 '-ExecutionPolicy', 'Bypass',
                 '-File', tmp_ps1,
                 '-FolderPath', folder_path],
                creationflags=self.subp_flags
            )
            def _cleanup(p):
                import time as _t
                _t.sleep(5)
                try:
                    os.remove(p)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(tmp_ps1,), daemon=True).start()
        except Exception:
            if os.path.exists(str(folder_path)):
                os.startfile(str(folder_path))

    def _show_done_and_open(self, title, message, open_path=None, select_file=None):
        """通用：顯示完成對話框，並在 Windows 上開啟資料夾（若有 select_file 則聚焦該檔案）
        若目標資料夾已開著，不重複開新視窗。"""
        messagebox.showinfo(title, message)
        if os.name != 'nt':
            return
        if select_file and os.path.isfile(select_file):
            self._select_file_in_explorer(select_file)
        elif open_path and os.path.exists(open_path):
            folder = str(open_path) if os.path.isdir(open_path) else str(Path(open_path).parent)
            self._open_folder_no_dup(folder)

    def _json_burn_process(self, video_path, subtitle_path):
        try:
            output_dir, _, output_file = self._json_process_common_setup(video_path)
            self.log(f"--- [燒錄] JSON 轉 ASS 後燒錄合併 ---")
            self.update_progress(10, "準備中...")
            success = self.process_json_subtitle_to_video(video_path, subtitle_path, output_file)
            if success:
                self.update_progress(100, "燒錄完成")
                self.log(f"✅ 燒錄完成: {Path(output_file).name}")
                self._show_done_and_open("完成", f"燒錄完成！\n檔案已儲存至: {output_file}", output_dir, select_file=str(output_file))
            else:
                self.log("❌ 燒錄失敗。")
                messagebox.showerror("錯誤", "燒錄失敗，請查看日誌！")
        except Exception as e:
            self.log(f"❌ 燒錄過程出錯: {str(e)}")
            self.log(f"   {traceback.format_exc()}")
        finally:
            self.finish_processing()

    def start_json_mux(self):
        """JSON 轉 ASS 後以封裝方式合併（不重新編碼，快速）"""
        video_path, subtitle_path = self._validate_json_video_inputs()
        if subtitle_path is None:
            return
        self._begin_processing("JSON 轉 ASS 封裝中...", self._json_mux_process, video_path, subtitle_path)

    def _load_and_convert_json_to_ass(self, subtitle_path, ass_file, video_path=None):
        """讀取 JSON 字幕並轉成 ASS 檔案，回傳是否成功"""
        import json as _json
        with open(subtitle_path, 'r', encoding='utf-8') as f:
            lyrics_data = _json.load(f)
        kw = self._collect_json_to_ass_kwargs(video_path)
        self._json_to_ass(lyrics_data, ass_file,
                          kw.pop('unplayed_color'), kw.pop('played_color'), kw.pop('border_color'),
                          kw.pop('font_name'), kw.pop('margin_v_offset'), kw.pop('color_mode'), kw.pop('font_size'),
                          **kw)

    def _json_mux_process(self, video_path, subtitle_path):
        try:
            output_dir, vfmt, output_file = self._json_process_common_setup(video_path)
            self.log(f"--- [封裝] JSON 轉 ASS 後封裝合併 ---")
            self.update_progress(10, "正在轉換 JSON → ASS...")

            ass_file = str(Path(output_dir) / f"{Path(subtitle_path).stem}.ass")
            self._load_and_convert_json_to_ass(subtitle_path, ass_file, video_path)
            self.update_progress(50, "正在封裝合併...")
            self.log(f"  🎬 正在使用 FFmpeg 封裝（不重新編碼）...")
            success = self.merge_subtitle_with_ffmpeg(video_path, ass_file, output_file, vfmt)
            if success:
                self.update_progress(100, "封裝完成")
                self.log(f"✅ 封裝完成: {Path(output_file).name}")
                self._show_done_and_open("完成", f"封裝完成！\n檔案已儲存至: {output_file}", output_dir, select_file=str(output_file))
            else:
                self.log("❌ 封裝失敗。")
                messagebox.showerror("錯誤", "封裝失敗，請查看日誌！")
        except Exception as e:
            self.log(f"❌ 封裝過程出錯: {str(e)}")
            self.log(f"   {traceback.format_exc()}")
        finally:
            self.finish_processing()

    def start_json_to_ass_only(self):
        """只做 JSON → ASS 轉換，不合併影片"""
        _, subtitle_path = self._validate_json_inputs()
        if subtitle_path is None:
            return
        self._begin_processing("JSON 轉 ASS 中...", self._json_to_ass_only_process, subtitle_path)

    def _json_to_ass_only_process(self, subtitle_path):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)
            ass_file = str(Path(output_dir) / f"{Path(subtitle_path).stem}.ass")
            self.log(f"--- [只轉換] JSON 轉 ASS 字幕 ---")
            self.update_progress(10, "正在轉換...")
            self._load_and_convert_json_to_ass(subtitle_path, ass_file)
            self.update_progress(100, "轉換完成")
            self.log(f"✅ ASS 字幕已儲存: {Path(ass_file).name}")
            self._show_done_and_open("完成", f"轉換完成！\nASS 字幕已儲存至: {ass_file}", output_dir, select_file=str(ass_file))
        except Exception as e:
            self.log(f"❌ 轉換過程出錯: {str(e)}")
            self.log(f"   {traceback.format_exc()}")
        finally:
            self.finish_processing()

    def start_merge_subtitle_video(self):
        video_path = self.merge_video_path_var.get().strip()
        subtitle_path = self.merge_subtitle_path_var.get().strip()

        if not video_path:
            messagebox.showwarning("警告", "請選擇影片檔案！")
            return
        if not subtitle_path:
            messagebox.showwarning("警告", "請選擇字幕檔案！")
            return
        if not os.path.exists(video_path):
            messagebox.showerror("錯誤", "找不到影片檔案！")
            return
        if not os.path.exists(subtitle_path):
            messagebox.showerror("錯誤", "找不到字幕檔案！")
            return
        if self.is_processing:
            return

        self._begin_processing("正在合併字幕與影片...", self.merge_subtitle_video_process, video_path, subtitle_path)
        self.start_btn.config(text="處理中...")

    def merge_subtitle_video_process(self, video_path, subtitle_path):
        try:
            output_dir = self.output_dir_var.get()
            os.makedirs(output_dir, exist_ok=True)

            video_stem = Path(video_path).stem
            vfmt = self.merge_video_format_var.get()
            output_file = Path(output_dir) / f"{video_stem}_含字幕.{vfmt}"

            self.log(f"--- 正在合併: {os.path.basename(video_path)} + {os.path.basename(subtitle_path)} ---")
            self.update_progress(10, "準備合併...")

            is_json_subtitle = subtitle_path.lower().endswith('.json')
            is_srt_subtitle  = subtitle_path.lower().endswith('.srt')
            is_ass_subtitle  = subtitle_path.lower().endswith('.ass') or subtitle_path.lower().endswith('.ssa')

            if is_json_subtitle:
                self.log("🎤 偵測到 JSON 字幕，將處理為 KTV 逐字效果...")
                success = self.process_json_subtitle_to_video(video_path, subtitle_path, str(output_file))
            elif is_srt_subtitle:
                self.log("📝 偵測到 SRT 字幕，將套用字幕樣式後燒錄...")
                success = self.process_srt_subtitle_to_video(video_path, subtitle_path, str(output_file))
            elif is_ass_subtitle:
                self.log("🎨 偵測到 ASS/SSA 字幕，將直接燒錄進影片...")
                success = self.burn_ass_subtitle_to_video(video_path, subtitle_path, str(output_file))
            else:
                self.log("📝 正在使用 FFmpeg 合併字幕...")
                success = self.merge_subtitle_with_ffmpeg(video_path, subtitle_path, str(output_file), vfmt)

            if success:
                self.update_progress(100, "合併完成")
                self.log(f"✅ 成功生成影片: {output_file.name}")
                self._show_done_and_open("完成", f"合併完成！\n檔案已儲存至: {output_file}", output_dir, select_file=str(output_file))
            else:
                self.log("❌ 合併失敗。")
                messagebox.showerror("錯誤", "合併失敗，請查看日誌！")

        except Exception as e:
            self.log(f"❌ 合併過程中出錯: {str(e)}")
            self.log(f"   {traceback.format_exc()}")
        finally:
            self.finish_processing()

    def merge_subtitle_with_ffmpeg(self, video_path, subtitle_path, output_file, vfmt):
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            subtitle_codec = "mov_text" if vfmt == "mp4" else "srt"
            cmd = [
                str(ffmpeg_exe), "-y", "-i", str(video_path), "-i", str(subtitle_path),
                "-map", "0:v:0", "-map", "0:a?", "-map", "1:s:0",
                "-c:v", "copy", "-c:a", "copy", "-c:s", subtitle_codec,
                "-disposition:s:0", "default",
                "-metadata:s:s:0", "title=字幕",
                str(output_file)
            ]
            self.log(f"  > 正在執行 FFmpeg...")
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            self.log(f"  ✅ FFmpeg 合併完成")
            return True
        except Exception as e:
            self.log(f"  ❌ FFmpeg 合併失敗: {str(e)}")
            return False

    def process_json_subtitle_to_video(self, video_path, json_path, output_file):
        try:
            self.log(f"  > 正在讀取 JSON 字幕...")
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

            self.log(f"  🎤 正在生成 KTV 滑動漸變字幕...")
            self.log(f"    - 未唱顏色: {unplayed_color}")
            self.log(f"    - 已唱顏色: {played_color}")
            self.log(f"    - 未唱邊框: {border_color}")
            self.log(f"    - 已唱邊框: {played_border_color if use_played_border else '關閉（邊框同未唱，但仍使用滑動漸變）'}")
            self.log(f"    - 字體: {selected_font}  大小: {font_size}pt")
            self.log(f"    - 變色模式: 滑動漸變")
            if two_line_enabled:
                self.log(f"    - 雙行字幕: 開啟（下一句提前 {advance_sec:.2f} 秒出現）")
                self.log(f"    - 雙行水平位置: 上行 X={top_x_offset:.0f} / 下行 X={bottom_x_offset:.0f}（正右負左）")
            else:
                self.log(f"    - 雙行字幕: 關閉")
            _vw, _vh = kw['video_width'], kw['video_height']
            if _vw and _vh:
                self.log(f"    - 影片解析度: {_vw}x{_vh}（PlayResX 將設為 {int(round(1080 * _vw / _vh))}）")

            ass_file = Path(output_file).parent / f"{Path(json_path).stem}.ass"
            self._json_to_ass(lyrics_data, str(ass_file),
                              kw.pop('unplayed_color'), kw.pop('played_color'), kw.pop('border_color'),
                              kw.pop('font_name'), kw.pop('margin_v_offset'), kw.pop('color_mode'), kw.pop('font_size'),
                              **kw)

            self.log(f"  🎬 正在使用 FFmpeg 燒錄字幕...")
            return self.burn_ass_subtitle_to_video(video_path, str(ass_file), output_file)

        except Exception as e:
            self.log(f"  ❌ JSON 字幕處理失敗: {str(e)}")
            self.log(f"     {traceback.format_exc()}")
            return False

    def process_srt_subtitle_to_video(self, video_path, srt_path, output_file):
        """將 SRT 字幕套用字幕樣式後燒錄進影片"""
        try:
            text_color   = self.ktv_unplayed_color_var.get().strip() or "#FFFFFF"
            border_color = self.ktv_border_color_var.get().strip()   or "#000000"
            played_border_color = self.ktv_played_border_color_var.get().strip() if hasattr(self, 'ktv_played_border_color_var') else border_color
            selected_font = self.ktv_font_var.get() if hasattr(self, 'ktv_font_var') else "微軟正黑體"
            try:
                font_size = int(self.ktv_font_size_var.get())
                if font_size < 10 or font_size > 200:
                    font_size = 60
            except Exception:
                font_size = 60
            try:
                margin_v_offset = int(self.subtitle_margin_var.get())
            except Exception:
                margin_v_offset = 0

            try:
                outline_size = int(self.ktv_border_map_expand_factor_var.get())
            except Exception:
                outline_size = 4

            self.log(f"  📝 字幕樣式: 顏色={text_color}  未唱邊框={border_color}  已唱邊框={played_border_color}  字體={selected_font} {font_size}pt  邊框={outline_size}px")

            ass_file = str(Path(output_file).parent / (Path(srt_path).stem + "_styled.ass"))
            self._srt_to_ass(srt_path, ass_file, text_color, border_color, selected_font, font_size, margin_v_offset, outline_size)

            self.log(f"  🎬 正在燒錄字幕樣式...")
            return self.burn_ass_subtitle_to_video(video_path, ass_file, output_file)

        except Exception as e:
            self.log(f"  ❌ SRT 字幕處理失敗: {str(e)}")
            self.log(f"     {traceback.format_exc()}")
            return False

    @staticmethod
    def _hex_to_bgr(hex_color, inline=False):
        """將 #RRGGBB 色碼轉換為 ASS &HBBGGRR 格式；inline=True 時結尾帶 & 符號"""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 3:
            hex_color = ''.join([c * 2 for c in hex_color])
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"&H{b:02X}{g:02X}{r:02X}" + ("&" if inline else "")


    def _srt_to_ass(self, srt_path, ass_file, text_color, border_color, font_name, font_size, margin_v_offset, outline_size=4):
        """將 SRT 字幕轉換為帶樣式的 ASS 格式（單一顏色，無逐字效果）"""
        try:
            hex_to_bgr = self._hex_to_bgr

            def srt_time_to_ass(srt_time):
                srt_time = srt_time.strip().replace(',', '.')
                h, m, rest = srt_time.split(':', 2)
                s, ms = rest.split('.')
                cs = int(ms[:3]) // 10
                return f"{int(h)}:{int(m):02d}:{int(s):02d}.{cs:02d}"

            text_bgr   = hex_to_bgr(text_color)
            border_bgr = hex_to_bgr(border_color)
            base_margin_v = 50
            margin_v = max(0, base_margin_v + margin_v_offset)

            srt_content = Path(srt_path).read_text(encoding='utf-8', errors='ignore')
            block_re = re.compile( r'\d+\s*\n' r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n' r'(.*?)(?=\n\n|\Z)', re.DOTALL )

            with open(ass_file, 'w', encoding='utf-8-sig', newline='\n') as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n\n")
                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                f.write(f"Style: Default,{font_name},{font_size},{text_bgr},{text_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,10,10,{margin_v},1\n\n")
                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

                for m in block_re.finditer(srt_content):
                    raw_start = m.group(1)
                    raw_end   = m.group(2)
                    raw_text  = m.group(3).strip()
                    raw_text = re.sub(r'<[^>]+>', '', raw_text)
                    raw_text = raw_text.replace('\n', ' ').strip()
                    if not raw_text:
                        continue
                    f.write(f"Dialogue: 0,{raw_start},{raw_end},Default,,0,0,0,,{raw_text}\n")

            self.log(f"  ✅ 已生成樣式化 ASS 字幕: {Path(ass_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ SRT 轉 ASS 失敗: {str(e)}")
            self.log(f"     {traceback.format_exc()}")

    @staticmethod
    def _format_srt_timestamp(seconds):
        """格式化秒數為 SRT 時間戳記 HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _json_to_srt(self, lyrics_data, srt_file):
        try:
            format_timestamp = self._format_srt_timestamp

            with open(srt_file, 'w', encoding='utf-8-sig') as f:
                srt_idx = 1
                if 'segments' in lyrics_data:
                    for segment in lyrics_data['segments']:
                        seg_start = segment['start']
                        seg_end   = segment['end']
                        text = segment.get('text', '').strip()
                        if not text:
                            continue
                        words = segment.get('words', [])
                        text_norm = text.replace('\n', ' ').strip()
                        if not text_norm:
                            continue
                        ts = self._format_srt_timestamp(seg_start)
                        te = self._format_srt_timestamp(seg_end)
                        f.write(f"{srt_idx}\n{ts} --> {te}\n{text_norm}\n\n")
                        srt_idx += 1
            self.log(f"  ✅ 已轉換為 SRT: {Path(srt_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ JSON 轉 SRT 失敗: {str(e)}")

    def _json_to_ass(self, lyrics_data, ass_file, unplayed_color, played_color, border_color, font_name="微軟正黑體", margin_v_offset=0, color_mode="char", font_size=60, two_line=False, advance_sec=1.5, top_x_offset=0.0, bottom_x_offset=0.0, line_gap_px=None, played_border_color=None, use_clip_mask=True, pre_show_sec=0.0, hold_sec=0.5, speed_factor=1.0, singing_end_ratio=1.0, video_width=None, video_height=None, outline_size=4):
        try:
            hex_to_bgr = self._hex_to_bgr
            hex_to_bgr_inline = lambda c: self._hex_to_bgr(c, inline=True)

            def format_ass_time(seconds):
                if seconds is None:
                    seconds = 0
                try:
                    seconds = float(seconds)
                except Exception:
                    seconds = 0
                if seconds < 0:
                    seconds = 0
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                centis = int((seconds - int(seconds)) * 100)
                return f"{hours:01d}:{minutes:02d}:{secs:02d}.{centis:02d}"

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
                估算字幕文字寬度（像素）。
                目的：用於 \clip 做左右半遮罩，讓同一行可同時呈現
                  右半（未唱）：白字+黑邊
                  左半（已唱）：藍字+白邊
                註：ASS 無法在同一行內真正做到「邊框跟著 Karaoke 逐字變化」，
                    因此使用「兩行疊圖 + 逐步 \clip」的近似方式；寬度用估算即可。
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
                取得 Tk 的字寬量測函數（像素）。
                注意：Tk 的像素尺度不一定等同 ASS PlayRes 的尺度，
                因此只用來提供「相對寬度分佈」（prefix/total 的比例），
                再映射回我們自己的 line_est_px（PlayRes 尺度）以避免整體尺度跑掉。
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
            border_bgr_inline = hex_to_bgr_inline(border_color)
            played_border_bgr_inline = hex_to_bgr_inline(played_border_color) if played_border_color else border_bgr_inline

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
                            """把 words 依序分配給每個子句，回傳 list of list"""
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

                                first_w_offset_cs = 0
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
                                        if i == 0:
                                            first_w_offset_cs = gap_cs

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
                                    karaoke_style = "KTV_Top_Karaoke"
                                    preview_style = "KTV_Top_Preview"
                                    x = base_x + float(top_x_offset)
                                    y = play_res_y - (margin_v + _line_gap)
                                else:
                                    karaoke_style = "KTV_Bottom_Karaoke"
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

            mode_desc = "平滑滑動漸變" if color_mode == "slide" else "逐字變色"
            self.log(f"  ✅ 已生成 KTV {mode_desc}字幕: {Path(ass_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ JSON 轉 ASS 失敗: {str(e)}")
            self.log(f"     {traceback.format_exc()}")

    def burn_ass_subtitle_to_video(self, video_path, ass_path, output_file):
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"

            import tempfile
            import shutil

            def _escape_filter_path(p: str) -> str:
                """
                給 FFmpeg filter 使用的路徑轉義：
                - Windows drive letter 的 ':' 需要寫成 '\\:'
                - 反斜線改成 '/'
                - 逗號 ',' 在 filter 參數中也建議轉義（避免被當成分隔符）
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

                cmd = [
                    str(ffmpeg_exe),
                    "-y",
                    "-i", temp_video,
                    "-vf", vf,
                    "-map", "0:v:0",
                    "-map", "0:a?",
                    "-c:v", "libx264",
                    "-c:a", "copy",
                    temp_output
                ]

                self.log(f"  > 正在執行 FFmpeg 燒錄...")
                subprocess.run(cmd, check=True, creationflags=self.subp_flags, cwd=temp_dir)

                shutil.move(temp_output, output_file)
                return True
            finally:
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
        except Exception as e:
            self.log(f"  ❌ 燒錄字幕失敗: {str(e)}")
            self.log(f"     {traceback.format_exc()}")
            return False

    def recognize_lyrics_and_generate_srt(self, audio_file, output_dir, output_stem=None, override_language=None):
        """使用 Whisper 模型識別歌詞並生成 SRT 字幕檔案"""
        import tempfile
        import shutil
        self.update_status("正在進行 AI 歌詞識別...", "orange")
        self.log("\n🎤 開始 AI 歌詞識別...")

        if output_stem is None:
            output_stem = Path(audio_file).stem

        try:
            device = self._resolve_device()
            runtime_ready, actual_device, runtime_lib_dir = self._ensure_runtime_stack_ready(device)
            if not runtime_ready:
                self.log("⚠️ AI 環境就緒失敗，跳過歌詞識別。")
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

            lang_display = "auto(自動偵測)" if whisper_lang == "auto" else whisper_lang
            self.log(f"  > 語言: {lang_display}，模型: {whisper_model_size}")

            temp_dir = tempfile.gettempdir()
            temp_audio_path = os.path.join(temp_dir, "temp_whisper_audio.mp3")
            shutil.copy2(audio_file, temp_audio_path)
            self.log(f"  > 已建立暫存檔案: {temp_audio_path}")

            temp_audio_posix = self._p(temp_audio_path)
            output_dir_posix = self._p(output_dir)

            _song_name = AudioSeparatorApp.sanitize_filename(output_stem, max_len=80)

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

            script = f"""
import sys, os
import json

target_lib = r'{runtime_lib_dir_posix}'
common_lib = r'{common_lib_dir_posix}'
app_bin_dir = r'{app_bin_dir_posix}'
app_py_dir = r'{app_py_dir_posix}'
whisper_models_dir = r'{whisper_models_dir_posix}'

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
    subprocess.run([r'{self.local_python}', '-m', 'pip', 'install',
                    '--target', common_lib, 'openai-whisper', 'ffmpeg-python'],
                  capture_output=True)
    import whisper

# stable-ts 用於強制對齊，提升逐字時間軸精度；load_audio 等工具仍用原生 whisper
_USE_STABLE_TS_SETTING = {_use_stable_ts}  # 由設定籤頁的勾選寫入 config.json
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

audio_file = r'{temp_audio_posix}'
output_dir = r'{output_dir_posix}'
original_stem = r'{output_stem}'
_whisper_lang_raw = r'{whisper_lang}'.strip().lower()
# whisper_lang=None => Whisper 會自動偵測語言；這能保留混合語言歌曲的原文字（例如中文+韓文）
whisper_lang = None if _whisper_lang_raw in ("", "auto", "detect", "none") else _whisper_lang_raw
model_size = r'{whisper_model_size}'
song_name = r'{_song_name}'

# Whisper 參數（由設定籤頁寫入 config.json，於此注入）
_CFG_ZH = dict(
    no_speech_threshold={_wz_nst},
    compression_ratio_threshold={_wz_crt},
    temperature={_wz_temp},
    beam_size={_wz_beam},
    nsp_skip={_wz_nsp},
    logprob_skip={_wz_lp},
    nsp_skip_score_threshold=3,
    nsp_score_85=3, nsp_score_70=2, nsp_score_55=1,
    logprob_score_neg15=2, logprob_score_neg10=1,
    short_text_chars=2, short_text_score=1,
)
_CFG_EN = dict(
    no_speech_threshold={_we_nst},
    compression_ratio_threshold={_we_crt},
    temperature={_we_temp},
    beam_size={_we_beam},
    nsp_skip={_we_nsp},
    logprob_skip={_we_lp},
)

# 載入模型（stable-ts 可用時用它的 load_model，讓 transcribe 自動做強制對齊）
print(f"[INFO] Loading Whisper model: {{model_size}}...")
if _STABLE_TS:
    model = _stable_whisper.load_model(model_size, download_root=whisper_models_dir)
else:
    model = whisper.load_model(model_size, download_root=whisper_models_dir)

print("[INFO] Transcribing audio (silence-based segmentation)...")
import numpy as np

# load audio（load_audio / pad_or_trim / SAMPLE_RATE / CHUNK_LENGTH 皆屬原生 whisper）
audio = whisper.load_audio(audio_file)
audio = whisper.pad_or_trim(audio, length=len(audio))

RATE      = whisper.audio.SAMPLE_RATE   # 16000
MAX_CHUNK = whisper.audio.CHUNK_LENGTH  # 30s max

# ──────────────────────────────────────────────
# 音訊能量對齊校正（方案 C）
# 對每個 word 的 start/end 用 RMS 能量找更精準的邊界
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

        # 搜尋範圍（秒 → 樣本）
        lo_sec = max(0.0, orig_start - search_before)
        hi_sec = min(audio_len / rate, orig_start + search_after)
        lo = int(lo_sec * rate)
        hi = int(hi_sec * rate)

        best_start = orig_start
        best_rise  = -1.0

        # 每 frame 計算 RMS，找能量上升最大點
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

        # 只有找到明顯上升（rise > 靜音底噪）才採用修正值
        NOISE_FLOOR = 0.002
        if best_rise < NOISE_FLOOR:
            best_start = orig_start  # 無明顯上升，保留原值

        new_w = dict(w)
        new_w['start'] = best_start
        # end 暫時保留原值，下面再連鎖修正
        new_w['end']   = orig_end
        refined.append(new_w)

    # 讓 word[i].end = word[i+1].start，保持連續不重疊
    for i in range(len(refined) - 1):
        next_start = refined[i + 1]['start']
        cur_end    = refined[i]['end']
        # 只往前修，不往後拉（避免把 end 拉超過原來的 end）
        if next_start < cur_end:
            refined[i]['end'] = next_start
        # 確保 end > start
        if refined[i]['end'] <= refined[i]['start']:
            refined[i]['end'] = refined[i]['start'] + 0.05

    # 最後一個 word：end 保留原值但確保 > start
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
    # 平滑：7 幀（140ms），比原本 5 幀更能吸收短暫殘響
    smooth_len = 7
    rms_smooth = np.convolve(rms, np.ones(smooth_len) / smooth_len, mode='same')
    is_silence = rms_smooth < threshold

    min_sil_frames = int(min_silence / 0.02)
    min_phr_frames = int(min_phrase  / 0.02)
    max_phr_frames = int(max_phrase  / 0.02)

    # 允許靜音區中最多連續幾幀的短暫突波不中斷靜音計數
    SPIKE_TOLERANCE = 2

    def find_silence_cut(start_frame, end_frame):
        '''
        在 [start_frame, end_frame-1] 內找最佳靜音切點。
        回傳切點 frame index，找不到回傳 None。
        使用突波容忍：連續靜音中允許最多 SPIKE_TOLERANCE 幀的非靜音突波。
        '''
        best_cut = None
        best_rms  = float('inf')
        sil_run   = 0   # 已連續偵測到的靜音幀數（含容忍突波）
        spike_cnt = 0   # 當前突波連續幀數

        for f in range(start_frame, end_frame):
            if is_silence[f]:
                sil_run  += 1
                spike_cnt = 0
            else:
                if spike_cnt < SPIKE_TOLERANCE and sil_run > 0:
                    # 容忍短暫突波，不重置靜音計數
                    spike_cnt += 1
                    sil_run   += 1
                else:
                    sil_run   = 0
                    spike_cnt = 0

            if sil_run >= min_sil_frames:
                # 這幀在有效靜音區內，若能量更低則記錄為候選切點
                if rms_smooth[f] < best_rms:
                    best_rms  = rms_smooth[f]
                    best_cut  = f

        return best_cut

    boundaries = [0]
    i = 0
    # 回溯/延伸搜尋的最大幀數（3 秒）
    SEARCH_FRAMES = int(3.0 / 0.02)

    while i < n_frames:
        phrase_end = i + max_phr_frames

        if phrase_end >= n_frames:
            # 剩餘音訊不足一個 max_phrase，直接收尾
            break

        search_start = i + min_phr_frames
        search_end   = min(n_frames, phrase_end)
        cut = find_silence_cut(search_start, search_end)

        if cut is not None:
            # 正常找到靜音，切在靜音最深處
            boundaries.append(cut)
            i = cut
            continue

        retro_start = max(i + min_phr_frames, phrase_end - SEARCH_FRAMES)
        cut = find_silence_cut(retro_start, phrase_end)

        if cut is not None:
            boundaries.append(cut)
            i = cut
            continue

        extend_end = min(n_frames, phrase_end + SEARCH_FRAMES)
        cut = find_silence_cut(phrase_end, extend_end)

        if cut is not None:
            boundaries.append(cut)
            i = cut
            continue

        #    切在延伸範圍後半段能量最低點，盡量避開能量高峰
        fallback_end = min(n_frames, phrase_end + SEARCH_FRAMES)
        region = rms_smooth[phrase_end:fallback_end]
        if len(region) > 0:
            cut = phrase_end + int(np.argmin(region))
        else:
            cut = phrase_end
        boundaries.append(cut)
        i = cut

    boundaries.append(n_frames)

    return [(boundaries[k] * frame, boundaries[k+1] * frame)
            for k in range(len(boundaries) - 1)
            if (boundaries[k+1] - boundaries[k]) >= min_phr_frames]

print("[INFO] Detecting silence boundaries...")
# ── 英文用稍寬鬆的切割（英文句子比中文長，讓 Whisper 有更多上下文）
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
    HARD_BREAK_AFTER  = {{',', '.', '!', '?', ';', ':', '...', '-'}}
    SOFT_BREAK_BEFORE = {{
        'and','but','or','so','yet','nor','for',
        'because','although','though','while','never','till','until',
        'when','where','that','which','who',
        'i','you','we','they','he','she','it',
    }}

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

        # initial_prompt 只做風格提示，不能用容易被 Whisper 當成辨識結果輸出的字串
        # 用純音樂符號，Whisper 幾乎不會把它當成語音輸出
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
            # stable-ts 回傳 WhisperResult 物件；轉成 dict 讓後續過濾邏輯不用改
            if _STABLE_TS and hasattr(chunk_result_raw, 'to_dict'):
                chunk_result = chunk_result_raw.to_dict()
            else:
                chunk_result = chunk_result_raw

            chunk_texts = []
            for seg in chunk_result.get("segments", []):
                text = seg["text"].strip()
                if not text:
                    continue

                seg_time_str_en = f"{{int((seg['start']+offset_sec)//60):02d}}:{{int((seg['start']+offset_sec)%60):02d}}"

                if _is_zero_duration(seg):
                    print(f"[SKIP] [{{seg_time_str_en}}] zero-duration segment, skip: {{text}}")
                    continue

                no_speech_prob = seg.get("no_speech_prob", 0)
                # 提高閾值：間奏/純音樂段落的 no_speech_prob 通常偏高，積極過濾
                if no_speech_prob > _CFG_EN['nsp_skip']:
                    print(f"[SKIP] [{{seg_time_str_en}}] no_speech_prob={{no_speech_prob:.2f}} too high, skip: {{text}}")
                    continue

                avg_logprob = seg.get("avg_logprob", 0)
                # 提高閾值：純音樂的 avg_logprob 通常很低（辨識信心差）
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

                # 偵測單一字元大量重複（Hmmmm... / aaaa...）
                if _is_repeated_char_hallucination(text):
                    print(f"[SKIP] [{{seg_time_str_en}}] repeated-char hallucination, skip: {{text[:40]}}")
                    continue

                # 偵測跨 chunk 的重複（同一句出現在不同 chunk）
                if _is_cross_chunk_repetition(text, recent_global_texts):
                    print(f"[SKIP] [{{seg_time_str_en}}] cross-chunk repetition, skip: {{text}}")
                    continue

                seg_words = [
                    {{**w, "start": w["start"] + offset_sec,
                           "end":   w["end"]   + offset_sec}}
                    for w in seg.get("words", [])
                ]

                # ── 音訊能量校正：修正每個 word 的 start 時間
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
                        # 過濾零時長或時間倒退的 segment
                        grp_start = grp[0]["start"]
                        grp_end   = grp[-1]["end"]
                        if grp_end <= grp_start:
                            print(f"[SKIP] zero/negative duration grp, skip: {{grp_text}}")
                            continue
                        seg_id += 1
                        all_segments.append({{
                            "id":    seg_id,
                            "start": grp_start,
                            "end":   grp_end,
                            "text":  grp_text,
                            "words": grp,
                        }})
                chunk_texts.append(text)
                recent_global_texts.append(text)
                if len(recent_global_texts) > MAX_GLOBAL_RECENT:
                    recent_global_texts.pop(0)

            if chunk_texts:
                prev_text = chunk_texts[-1]

        except Exception as e:
            print(f"[WARN] phrase at {{offset_sec:.1f}}s failed: {{e}}")

# ══════════════════════════════════════════════════════════════
#  中文辨識邏輯（保留原有邏輯，完全不動）
# ══════════════════════════════════════════════════════════════
def transcribe_chinese(model, audio, phrases, RATE, MAX_CHUNK, whisper_lang):
    global seg_id, prev_text, all_segments

    HALLUCINATION_PATTERNS = [
        "詞曲", "作詞", "作曲", "編曲", "監製", "出品", "版權所有",
        "製作人", "發行", "唱片", "music by", "lyrics by",
        "produced by", "written by",
        # initial_prompt 回聲
        "一首中文歌曲", "以下是", "歌詞如下", "以下歌詞",
        # YouTube 平台推廣字串
        "點贊", "訂閱", "轉發", "打賞", "明鏡", "字幕组", "字幕 by", "字幕by",
    ]

    recent_global_texts_zh = []
    MAX_GLOBAL_RECENT_ZH = 8

    def _is_cross_chunk_repetition_zh(text, recent_texts, max_repeats=2):
        t = text.lower().strip()
        count = sum(1 for rt in recent_texts[-max_repeats:] if rt.lower().strip() == t)
        return count >= max_repeats

    def _is_repeated_char_hallucination_zh(text, min_len=6, threshold=0.85):
        return False  # 不過濾重複字元
        if len(text) < min_len:
            return False
        clean = text.strip().replace(' ', '')
        if not clean:
            return False
        most_common_ratio = max(clean.count(c) for c in set(clean)) / len(clean)
        return most_common_ratio >= threshold

    def _fmt_time(seconds):
        # 格式化秒數為 mm:ss 供 SKIP log 顯示
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{{m:02d}}:{{s:02d}}"

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
            # stable-ts 回傳 WhisperResult 物件；轉成 dict 讓後續過濾邏輯不用改
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

                # ── 直接門檻過濾（與英文對稱）
                if no_speech_prob > _CFG_ZH['nsp_skip']:
                    print(f"[SKIP] [{{seg_time_str}}] no_speech_prob={{no_speech_prob:.2f}} 超過門檻，跳過: {{text}}")
                    continue
                if avg_logprob < _CFG_ZH['logprob_skip']:
                    print(f"[SKIP] [{{seg_time_str}}] avg_logprob={{avg_logprob:.2f}} 低於門檻，跳過: {{text}}")
                    continue

                # ── 硬性黑名單過濾（優先於複合評分）
                text_lower = text.lower()
                if any(pat in text_lower for pat in HALLUCINATION_PATTERNS):
                    print(f"[SKIP] [{{seg_time_str}}] 黑名單幻覺，跳過: {{text}}")
                    continue

                # ── 跨 chunk 重複
                if _is_cross_chunk_repetition_zh(text, recent_global_texts_zh):
                    print(f"[SKIP] [{{seg_time_str}}] 跨chunk重複幻覺，跳過: {{text}}")
                    continue

                # ── 單字重複幻覺
                if _is_repeated_char_hallucination_zh(text):
                    print(f"[SKIP] [{{seg_time_str}}] 重複字元幻覺，跳過: {{text[:40]}}")
                    continue

                # ── 複合評分（取代舊版單一閾值）
                is_hallucination, hal_score = _is_hallucination_by_composite(
                    text, no_speech_prob, avg_logprob)
                if is_hallucination:
                    print(f"[SKIP] [{{seg_time_str}}] 複合評分={{hal_score}} "
                          f"(nsp={{no_speech_prob:.2f}} lp={{avg_logprob:.2f}})，跳過: {{text}}")
                    continue

                seg_words = [
                    {{**w, "start": w["start"] + offset_sec,
                           "end":   w["end"]   + offset_sec}}
                    for w in seg.get("words", [])
                ]

                # ── 音訊能量校正：修正每個 word 的 start 時間
                if seg_words:
                    seg_words = refine_words_by_energy(seg_words, audio, RATE)

                text_norm = text.replace('\\n', ' ')
                sub_lines = [l.strip() for l in text_norm.split(' ') if l.strip()]

                if len(sub_lines) <= 1 or not seg_words:
                    seg_id += 1
                    all_segments.append({{
                        "id":    seg_id,
                        "start": seg["start"] + offset_sec,
                        "end":   seg["end"]   + offset_sec,
                        "text":  text,
                        "words": seg_words,
                    }})
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
                    seg_dur = (seg["end"] + offset_sec - (seg["start"] + offset_sec)) / len(sub_lines)

                    for li, line in enumerate(sub_lines):
                        line_words = word_groups[li]
                        if line_words:
                            line_start = line_words[0].get('start', seg["start"] + offset_sec + li * seg_dur)
                            line_end   = line_words[-1].get('end',   seg["start"] + offset_sec + (li + 1) * seg_dur)
                        else:
                            line_start = seg["start"] + offset_sec + li * seg_dur
                            line_end   = seg["start"] + offset_sec + (li + 1) * seg_dur
                        seg_id += 1
                        all_segments.append({{
                            "id":    seg_id,
                            "start": line_start,
                            "end":   line_end,
                            "text":  line,
                            "words": line_words,
                        }})
                    chunk_texts.append(text)

            if chunk_texts:
                prev_text = " ".join(chunk_texts[-2:])
                # ── Bug 修正：更新跨 chunk 重複偵測記憶
                for ct in chunk_texts:
                    recent_global_texts_zh.append(ct)
                if len(recent_global_texts_zh) > MAX_GLOBAL_RECENT_ZH:
                    recent_global_texts_zh = recent_global_texts_zh[-MAX_GLOBAL_RECENT_ZH:]

        except Exception as e:
            print(f"[WARN] phrase at {{offset_sec:.1f}}s failed: {{e}}")

# ══════════════════════════════════════════════════════════════
#  分流入口：依語言選擇辨識路徑
# ══════════════════════════════════════════════════════════════
if whisper_lang == 'en':
    print("[INFO] Using English recognition pipeline...")
    transcribe_english(model, audio, phrases, RATE, MAX_CHUNK)
else:
    print("[INFO] Using CJK recognition pipeline...")
    transcribe_chinese(model, audio, phrases, RATE, MAX_CHUNK, whisper_lang)

# ══════════════════════════════════════════════════════════════
#  短句合併後處理
#  字數 < MIN_CHARS 的 segment 往後合併，直到夠長或沒有下一句
#  合併條件：下一句與本句間隔 <= MAX_GAP_SEC（避免跨越明顯停頓）
# ══════════════════════════════════════════════════════════════
MIN_CHARS   = 5      # 少於幾個字（CJK 字元）才觸發合併，可調 4～6
MAX_GAP_SEC = 1.5    # 兩句間隔超過這個秒數就不合併，可調

def _char_count(text):
    # 只計 CJK 字元數；英文則計 word 數
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
        # 只要目前句子夠短，就嘗試往後吸
        while _char_count(cur['text']) < min_chars and i + 1 < len(segs):
            nxt = segs[i + 1]
            gap = nxt['start'] - cur['end']
            if gap > max_gap_sec:
                break   # 間隔太大，不合併
            # 合併文字（中間加空格供英文，CJK 不影響）
            cur['text'] = cur['text'].rstrip() + ' ' + nxt['text'].lstrip()
            cur['end']  = nxt['end']
            cur['words'] = cur['words'] + list(nxt.get('words', []))
            i += 1   # 跳過被吸掉的那句
        merged.append(cur)
        i += 1
    # 重新編號
    for idx, s in enumerate(merged, 1):
        s['id'] = idx
    return merged

all_segments = merge_short_segments(all_segments, MIN_CHARS, MAX_GAP_SEC)
print(f"[INFO] After short-segment merge: {{len(all_segments)}} segments")

result = {{"segments": all_segments, "language": whisper_lang}}

# 保存結果
output_json = os.path.join(output_dir, original_stem + '.json')
with open(output_json, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

# 生成 SRT
srt_file = os.path.join(output_dir, original_stem + '.srt')

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{{hours:02d}}:{{minutes:02d}}:{{secs:02d}},{{millis:03d}}"

srt_index = 1
with open(srt_file, 'w', encoding='utf-8-sig') as f:
    for segment in result['segments']:
        seg_start = segment['start']
        seg_end   = segment['end']
        text = segment['text'].strip()
        if not text:
            continue
        f.write(str(srt_index) + chr(10))
        f.write(format_timestamp(seg_start) + " --> " + format_timestamp(seg_end) + chr(10))
        f.write(text + chr(10) + chr(10))
        srt_index += 1

print(f"[SUCCESS] {{srt_file}}")
print(f"[JSON] {{output_json}}")
"""

            self.log("  > 正在載入 Whisper 模型（首次使用會自動下載）...")
            start_time = time.time()

            # 把 script 寫成暫存 .py 檔再執行，避免 Windows CreateProcess
            # 命令列長度上限（~32767 字元）造成 WinError 206
            temp_script_path = os.path.join(temp_dir, "temp_whisper_script.py")
            with open(temp_script_path, 'w', encoding='utf-8') as _sf:
                _sf.write(script)

            process = subprocess.Popen(
                [str(self.local_python), temp_script_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                encoding='utf-8', errors='replace',
                creationflags=self.subp_flags, env=env
            )
            self._current_process = process

            srt_output_file = None
            json_output_file = None

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    if os.path.exists(temp_audio_path):
                        try:
                            os.remove(temp_audio_path)
                        except Exception:
                            pass
                    try:
                        if os.path.exists(temp_script_path):
                            os.remove(temp_script_path)
                    except Exception:
                        pass
                    self.log("🛑 歌詞識別已取消。")
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
                            self.update_progress(60, "正在識別歌詞", step_text="步驟 4/5：載入 Whisper 模型中...")
                        elif "Transcribing audio" in line or "Detecting silence" in line:
                            self.update_progress(65, "正在識別歌詞", step_text="步驟 4/5：偵測句子邊界中...")
                        elif "Found" in line and "phrase segments" in line:
                            self.update_progress(68, "正在識別歌詞", step_text=f"步驟 4/5：{line.split(chr(93)+chr(32))[-1].strip()}")
                        else:
                            m = re.search(r"phrase\s+(\d+)\s*/\s*(\d+)", line)
                            if m:
                                cur, total = int(m.group(1)), int(m.group(2))
                                pct = 70 + int(cur / total * 9)  # 映射到 70–79%
                                self.update_progress(pct, "正在識別歌詞", step_text=f"步驟 4/5：AI 歌詞辨識中 {cur}/{total}")

            process.wait()
            self._current_process = None

            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                    self.log("  > 已清除暫存檔案")
                except Exception as e:
                    self.log(f"  ⚠️ 清除暫存檔案失敗: {str(e)}")
            try:
                if os.path.exists(temp_script_path):
                    os.remove(temp_script_path)
            except Exception:
                pass
            if process.returncode == 0 and srt_output_file:
                target_lang = override_language if override_language is not None else self.lyrics_language_var.get()
                if target_lang != "original" and json_output_file and os.path.exists(json_output_file):
                    try:
                        self.log(f"  > 正在轉換歌詞語言為: {'繁體中文' if target_lang == 'traditional' else '簡體中文'}")

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
                                seg_s = segment['start']
                                seg_e = segment['end']
                                text = segment['text'].strip()
                                if not text:
                                    continue
                                text = text.replace('\n', ' ').strip()
                                if not text:
                                    continue
                                ts = self._format_srt_timestamp(seg_s)
                                te = self._format_srt_timestamp(seg_e)
                                f.write(f"{srt_idx}\n{ts} --> {te}\n{text}\n\n")
                                srt_idx += 1

                        self.log("  ✅ 歌詞語言轉換完成")
                    except Exception as e:
                        self.log(f"  ⚠️ 歌詞語言轉換失敗: {str(e)}")
                        self.log(f"     {traceback.format_exc()}")

                elapsed_time = time.time() - start_time
                self.log(f"✅ 歌詞識別完成！花費 {elapsed_time:.2f} 秒")
                self.log(f"   SRT 字幕檔: {Path(srt_output_file).name}")
                return srt_output_file, json_output_file, None
            else:
                self.log("❌ 歌詞識別失敗。")
                return None

        except Exception as e:
            self.log(f"❌ 歌詞識別過程中出錯: {str(e)}")
            self.log(f"   {traceback.format_exc()}")
            return None

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioSeparatorApp(root)
    root.mainloop()
