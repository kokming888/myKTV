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

# 防止 EXE 遞迴啟動
if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and not any(arg.startswith('--multiprocessing') for arg in sys.argv):
        sys.exit(0)

class AudioSeparatorApp:
    def __init__(self, root):
        self.root = root
        self.version = "v2.13"
        self.root.title(f"MP3 人聲分離 & YouTube 下載/KTV 製作工具 {self.version}")
        self.root.geometry("1100x650")
        self.root.minsize(900, 560)  # 設定最小尺寸
        
        # 取得執行路徑 (EXE 所在目錄)
        # 注意：PyInstaller onefile 模式下 sys.executable 指向 EXE 本身（正確）
        # 但部分版本 sys._MEIPASS 指向暫存解壓目錄，必須用 sys.executable 而非 __file__
        if getattr(sys, 'frozen', False):
            exe_path = Path(sys.executable)
            # 若 EXE 被放在 Temp 目錄（代表是 onefile 解壓中間狀態），改用環境變數
            if 'Temp' in str(exe_path) or 'temp' in str(exe_path):
                # 嘗試從 _MEIPASS 的父目錄推算（onefile 解壓時 sys.executable 仍正確）
                # 這種情況實際上不應發生，但作為保護
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
            
        # 自動遷移舊資料夾名稱 (與 gui_app_2.py 保持一致)
        migrations = {
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
                    pass  # 資料夾遷移失敗不中斷啟動，通常是權限問題

        # 定義全外部目錄 (與 gui_app_2.py 完全共用)
        self.bin_dir = self.app_dir / "engine_ffmpeg"      # 存放音訊引擎 (FFmpeg)
        self.py_dir = self.app_dir / "runtime_python"      # 存放內建 Python 核心
        self.ytdlp_dir = self.app_dir / "yt-dlp"           # 共用 yt-dlp 下載器
        self.common_lib_dir = self.app_dir / "ai_libraries_common"  # 所有模式共用的 Python 套件
        self.lib_dir = self.app_dir / "ai_libraries_cpu"   # CPU AI 組件（硬體特定）
        self.gpu_lib_dir = self.app_dir / "ai_libraries_gpu"  # GPU AI 組件（硬體特定）
        self.directml_lib_dir = self.app_dir / "ai_libraries_directml"  # DirectML AI 組件（硬體特定）
        self.models_dir = self.app_dir / "ai_models"       # 存放 AI 分離模型
        self.whisper_models_dir = self.app_dir / "ai_models_whisper"  # 存放 Whisper 歌詞識別模型
        
        for d in [self.bin_dir, self.py_dir, self.ytdlp_dir, self.common_lib_dir, self.lib_dir, self.gpu_lib_dir, self.directml_lib_dir, self.models_dir, self.whisper_models_dir]:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass  # 建立失敗不中斷啟動，後續操作會再次嘗試
        
        # 內建 Python 的路徑
        self.local_python = self.py_dir / "python.exe"
        
        # 設定環境變數與 DLL 載入路徑
        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{self.py_dir}{os.pathsep}{os.environ['PATH']}"
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(str(self.bin_dir))
            except Exception:
                pass  # Windows 版本過舊或路徑無效時忽略
        
        os.environ["PYTHONPATH"] = os.pathsep.join([str(self.common_lib_dir), str(self.lib_dir)])
        
        # 把 common_lib_dir 加到 sys.path，這樣 Python 才能找到安裝在那裡的套件
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
        
        # 設定檔相關
        self.config_file = self.app_dir / "config.json"
        self.load_config()
        
        self.setup_ui_style()
        self.setup_ui()
        
        # 啟動時自動靜默檢查環境（不顯示列表、不彈窗）
        def _startup_check():
            self.update_status("正在檢查環境...", "gray")
            self.check_components(prompt=False, show_list=False)
            self.update_status("準備就緒", "green")
        self.root.after(500, _startup_check)

    def setup_ui_style(self):
        """設定 UI 風格，讓介面更現代美觀"""
        style = ttk.Style()
        
        # 嘗試使用現代主題（如果有安裝的話就用，沒有就用預設）
        available_themes = style.theme_names()
        
        # 優先使用的主題順序
        preferred_themes = ['clam', 'alt', 'default', 'classic']
        for theme in preferred_themes:
            if theme in available_themes:
                style.theme_use(theme)
                break
        
        # 定義顏色方案
        colors = {
            'primary': '#2563EB',      # 主色：藍色
            'success': '#10B981',      # 成功：綠色
            'warning': '#F59E0B',      # 警告：橙色
            'danger': '#EF4444',       # 危險：紅色
            'info': '#6366F1',         # 資訊：靛藍
            'bg': '#F8FAFC',           # 背景色
            'fg': '#1E293B',           # 前景色
            'border': '#E2E8F0',       # 邊框色
            'hover': '#DBEAFE',        # 懸停色
        }
        
        # 設定整體視窗背景
        self.root.configure(bg=colors['bg'])
        
        # 設定 Frame 風格
        style.configure('TFrame', background=colors['bg'])
        
        # 設定 Label 風格
        style.configure('TLabel', background=colors['bg'], foreground=colors['fg'], font=('Arial', 9))
        
        # 設定按鈕風格
        style.configure('TButton', 
                       font=('Arial', 9, 'bold'),
                       padding=8,
                       borderwidth=1,
                       relief='flat')
        
        # 設定 Entry 風格
        style.configure('TEntry', 
                       fieldbackground='white',
                       bordercolor=colors['border'],
                       lightcolor=colors['border'],
                       darkcolor=colors['border'])
        
        # 設定 LabelFrame 風格
        style.configure('TLabelframe', 
                       background=colors['bg'],
                       bordercolor=colors['border'],
                       lightcolor=colors['border'],
                       darkcolor=colors['border'])
        style.configure('TLabelframe.Label', 
                       background=colors['bg'],
                       foreground=colors['primary'],
                       font=('Arial', 10, 'bold'))
        
        # 設定 Notebook 風格
        style.configure('TNotebook', background=colors['bg'], bordercolor=colors['border'])
        style.configure('TNotebook.Tab', 
                       background=colors['bg'],
                       foreground=colors['fg'],
                       padding=[10, 5],
                       font=('Arial', 9))
        style.map('TNotebook.Tab',
                 background=[('selected', colors['primary']), ('active', colors['hover'])],
                 foreground=[('selected', 'white')])
        
        # 設定進度條風格
        style.configure('Horizontal.TProgressbar',
                       background=colors['primary'],
                       troughcolor=colors['border'],
                       bordercolor=colors['border'],
                       lightcolor=colors['primary'],
                       darkcolor=colors['primary'])
        
        # 設定 Checkbutton 和 Radiobutton 風格
        style.configure('TCheckbutton', background=colors['bg'], foreground=colors['fg'])
        style.configure('TRadiobutton', background=colors['bg'], foreground=colors['fg'])
        
        # 儲存顏色供後續使用
        self.ui_colors = colors

    def setup_ui(self):
        # 標題放在 root 上，固定在最上面，使用我們的配色方案
        tk.Label(self.root, 
                text=f"MP3 人聲分離 & YouTube 下載/KTV 製作工具 {self.version}", 
                font=("Arial", 13, "bold"),
                fg=self.ui_colors['primary'],
                bg=self.ui_colors['bg']).pack(pady=(3, 2))
        
        # 建立左右兩欄布局
        main_split = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=self.ui_colors['bg'])
        main_split.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        
        # --- 左側：按鈕區 ---
        left_panel = tk.Frame(main_split, bg=self.ui_colors['bg'], width=200)
        left_panel.pack_propagate(False)
        main_split.add(left_panel, minsize=180)
        
        # 按鈕區標題
        tk.Label(left_panel, 
                text="功能選單", 
                font=("Arial", 12, "bold"),
                fg=self.ui_colors['primary'],
                bg=self.ui_colors['bg']).pack(pady=(3, 5))
        
        # 建立功能按鈕
        self.tab_buttons = []
        self.tabs = []
        self.current_tab_index = 0
        
        # 定義功能按鈕資訊
        tab_info = [
            ("📺", "YouTube 一鍵轉 KTV"),
            ("📥", "YouTube 下載 (MP3/MP4)"),
            ("🎬", "本地影片轉 KTV"),
            ("🎵", "本地音檔批量分離"),
            ("🎤", "本地影片辨識歌詞"),
            ("📝", "合併字幕與影片"),
            ("🔧", "環境修復"),
            ("📋", "執行日誌"),
            ("📧", "聯絡作者")
        ]
        
        # --- 右側：內容區 ---
        self.right_scrollable_frame = tk.Frame(main_split, bg=self.ui_colors['bg'])
        main_split.add(self.right_scrollable_frame, minsize=600)
        
        # 建立垂直容器來放置所有內容
        self.right_main_container = tk.Frame(self.right_scrollable_frame, bg=self.ui_colors['bg'])
        self.right_main_container.pack(fill=tk.X, padx=5, pady=3)
        
        # 建立中繼容器：所有內容頁面用 grid 放在這裡
        self.tab_container = tk.Frame(self.right_main_container, bg=self.ui_colors['bg'])
        self.tab_container.pack(fill=tk.X, pady=(0, 3))
        
        # 設定 tab_container 的 grid 配置
        self.tab_container.columnconfigure(0, weight=1)
        
        # 建立所有內容頁面（先建立但不顯示）
        self.content_frames = []
        
        # 建立功能按鈕和對應的內容頁面
        for i, (icon, name) in enumerate(tab_info):
            # 在 YouTube 相關結束、本地相關開始時加入分隔線
            if i == 2:
                tk.Frame(left_panel, bg=self.ui_colors['border'], height=2).pack(fill=tk.X, padx=10, pady=3)
                tk.Label(left_panel, text="【 本地功能 】", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], font=("Arial", 9, "bold")).pack(anchor=tk.W, padx=15, pady=(3, 1))
            
            # 建立按鈕
            btn = tk.Button(left_panel, 
                          text=f"{icon} {name}",
                          font=("Arial", 10),
                          bg=self.ui_colors['bg'],
                          fg=self.ui_colors['fg'],
                          relief='flat',
                          padx=15,
                          pady=6,
                          anchor='w',
                          cursor='hand2',
                          command=lambda idx=i: self.switch_tab(idx))
            btn.pack(fill=tk.X, padx=5, pady=1)
            self.tab_buttons.append(btn)
            
            # 建立內容框架（放在 tab_container 中，用 grid）
            content_frame = tk.Frame(self.tab_container, bg=self.ui_colors['bg'])
            self.content_frames.append(content_frame)
            self.tabs.append(content_frame)
        
        # 把所有內容框架都用 grid 放在同一個位置，預設只有第一個顯示
        for i, frame in enumerate(self.content_frames):
            sticky = "nsew" if i == 7 else "new"
            frame.grid(row=0, column=0, sticky=sticky, padx=3, pady=2)
            if i != 0:
                frame.grid_remove()
        
        self.update_tab_buttons(0)

        # --- Tab 1: YouTube 轉 MKV ---
        yt_tab = self.content_frames[0]
        
        yt_url_frame = tk.Frame(yt_tab, bg=self.ui_colors['bg'])
        yt_url_frame.pack(fill=tk.X, pady=2)
        
        tk.Label(yt_url_frame, text="YouTube 網址:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_entry = tk.Entry(yt_url_frame, textvariable=self.yt_url_var)
        self.yt_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_entry.bind("<Button-1>", self.quick_paste_url)
        
        # 提示標籤
        tk.Label(yt_tab, text="(點擊輸入框自動貼上剪貼簿網址)", fg="gray", font=("Arial", 8), bg=self.ui_colors['bg']).pack(anchor=tk.W, padx=(85, 0), pady=(1, 0))

        # --- Tab 2: YouTube 純下載 ---
        yt_dl_tab = self.content_frames[1]

        # 第一列：網址輸入（Tab 2 獨立變數，不影響 Tab 1）
        dl_url_row = tk.Frame(yt_dl_tab, bg=self.ui_colors['bg'])
        dl_url_row.pack(fill=tk.X, pady=2)
        tk.Label(dl_url_row, text="YouTube 網址:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_dl_url_var = tk.StringVar()
        self.yt_dl_entry = tk.Entry(dl_url_row, textvariable=self.yt_dl_url_var)
        self.yt_dl_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_dl_entry.bind("<Button-1>", self.quick_paste_dl_url)
        tk.Label(dl_url_row, text="(點擊自動貼上)", fg="gray", font=("Arial", 8), bg=self.ui_colors['bg']).pack(side=tk.LEFT)

        # 第二列：下載格式 + 畫質選擇
        dl_opt_row = tk.Frame(yt_dl_tab, bg=self.ui_colors['bg'])
        dl_opt_row.pack(fill=tk.X, pady=2)

        tk.Label(dl_opt_row, text="下載格式:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.dl_type_var = tk.StringVar(value="both")
        tk.Radiobutton(dl_opt_row, text="MP3 + MP4", variable=self.dl_type_var, value="both", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="僅 MP3",    variable=self.dl_type_var, value="mp3", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="僅 MP4",    variable=self.dl_type_var, value="mp4", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)

        tk.Label(dl_opt_row, text="  |  MP4 畫質:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(side=tk.LEFT, padx=(10, 0))
        self.dl_quality_var = tk.StringVar(value="1080")
        tk.Radiobutton(dl_opt_row, text="最佳",  variable=self.dl_quality_var, value="best", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="1080p", variable=self.dl_quality_var, value="1080", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="720p",  variable=self.dl_quality_var, value="720", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="480p",  variable=self.dl_quality_var, value="480", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)

        # 第三列：是否進行音訊分離（Checkbox）
        dl_sep_toggle_row = tk.Frame(yt_dl_tab, bg=self.ui_colors['bg'])
        dl_sep_toggle_row.pack(fill=tk.X, pady=(4, 0))
        self.dl_do_separate_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dl_sep_toggle_row,
            text="下載後進行 AI 音訊分離（人聲／伴奏）",
            variable=self.dl_do_separate_var,
            command=self._toggle_dl_separate_options,
            font=("Arial", 9, "bold"),
            bg=self.ui_colors['bg'],
            fg=self.ui_colors['fg'],
            selectcolor=self.ui_colors['bg']
        ).pack(side=tk.LEFT)

        # 音訊分離選項區（預設隱藏）
        self.dl_sep_options_frame = tk.LabelFrame(yt_dl_tab, text="音訊分離設定", padx=8, pady=4, bg=self.ui_colors['bg'])
        # 不在這裡 pack，由 _toggle_dl_separate_options 控制顯示

        # 分離選項第一列：輸出格式
        sep_fmt_row = tk.Frame(self.dl_sep_options_frame)
        sep_fmt_row.pack(fill=tk.X, pady=2)
        tk.Label(sep_fmt_row, text="分離輸出格式:").pack(side=tk.LEFT)
        self.dl_sep_format_var = tk.StringVar(value="mp3")
        for fmt_val in ["mp3", "wav", "flac"]:
            tk.Radiobutton(sep_fmt_row, text=fmt_val.upper(),
                           variable=self.dl_sep_format_var, value=fmt_val).pack(side=tk.LEFT, padx=8)
        
        # 分離選項第二列：左伴奏右人聲選項
        sep_lr_row = tk.Frame(self.dl_sep_options_frame)
        sep_lr_row.pack(fill=tk.X, pady=2)
        self.dl_sep_lr_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            sep_lr_row,
            text="產生「左伴奏 / 右人聲+伴奏」立體聲 MP3",
            variable=self.dl_sep_lr_var,
            font=("Arial", 9)
        ).pack(side=tk.LEFT)

        # --- Tab 3: 本地影片轉 MKV ---
        local_v_tab = self.content_frames[2]
        
        v_list_frame = tk.Frame(local_v_tab, bg=self.ui_colors['bg'])
        v_list_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=5, pady=5)
        
        self.v_listbox = tk.Listbox(v_list_frame, height=6, selectmode=tk.EXTENDED)
        self.v_listbox.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        v_scrollbar = tk.Scrollbar(v_list_frame)
        v_scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.v_listbox.config(yscrollcommand=v_scrollbar.set)
        v_scrollbar.config(command=self.v_listbox.yview)
        
        v_btn_frame = tk.Frame(local_v_tab, bg=self.ui_colors['bg'])
        v_btn_frame.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        self.v_list =[] # 儲存影片檔案路徑
        tk.Button(v_btn_frame, text="加入影片", command=self.browse_local_video, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        tk.Button(v_btn_frame, text="加入資料夾", command=self.browse_local_v_folder, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        tk.Button(v_btn_frame, text="移除選取", command=self.remove_selected_v, width=10, bg=self.ui_colors['warning'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        tk.Button(v_btn_frame, text="清除清單", command=self.clear_v_list, width=10, bg=self.ui_colors['danger'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)

        # --- Tab 4: 本地檔案分離 ---
        file_tab = self.content_frames[3]
        
        file_list_frame = tk.Frame(file_tab, bg=self.ui_colors['bg'])
        file_list_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=5, pady=5)
        
        self.file_listbox = tk.Listbox(file_list_frame, height=6, selectmode=tk.EXTENDED)
        self.file_listbox.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        scrollbar = tk.Scrollbar(file_list_frame)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.file_listbox.yview)
        
        file_btn_frame = tk.Frame(file_tab, bg=self.ui_colors['bg'])
        file_btn_frame.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        tk.Button(file_btn_frame, text="加入檔案", command=self.browse_file, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        tk.Button(file_btn_frame, text="移除選取", command=self.remove_selected_file, width=10, bg=self.ui_colors['warning'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        tk.Button(file_btn_frame, text="清除清單", command=self.clear_files, width=10, bg=self.ui_colors['danger'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(pady=2)
        
        # --- Tab 5: 本地影片辨識歌詞 ---
        recognize_tab = self.content_frames[4]
        
        # 影片檔選擇
        rec_video_frame = tk.LabelFrame(recognize_tab, text="影片檔案", padx=10, pady=10, bg=self.ui_colors['bg'])
        rec_video_frame.pack(fill=tk.X, pady=5)
        
        self.rec_video_path_var = tk.StringVar()
        tk.Entry(rec_video_frame, textvariable=self.rec_video_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(rec_video_frame, text="選擇影片", command=self.browse_rec_video, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.LEFT)
        
        # 辨識選項
        rec_options_frame = tk.LabelFrame(recognize_tab, text="辨識選項", padx=10, pady=10, bg=self.ui_colors['bg'])
        rec_options_frame.pack(fill=tk.X, pady=5)
        
        # 第一列：語言
        rec_lang_row = tk.Frame(rec_options_frame, bg=self.ui_colors['bg'])
        rec_lang_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(rec_lang_row, text="語言:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.rec_language_var = tk.StringVar(value="zh")
        rec_lang_menu = ttk.Combobox(rec_lang_row, textvariable=self.rec_language_var, values=["zh (中文)", "en (英文)", "ja (日文)", "ko (韓文)"], state="readonly", width=18)
        rec_lang_menu.pack(side=tk.LEFT, padx=5)

        # 第一列右側：模型大小
        tk.Label(rec_lang_row, text="模型大小:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(side=tk.LEFT, padx=(20, 5))
        self.whisper_model_var = tk.StringVar(value="medium")
        rec_model_menu = ttk.Combobox(rec_lang_row, textvariable=self.whisper_model_var,
                                      values=["tiny", "base", "small", "medium", "large"],
                                      state="readonly", width=8)
        rec_model_menu.pack(side=tk.LEFT, padx=5)
        tk.Label(rec_lang_row, text="（medium 準確度高，large 最準但較慢）",
                 bg=self.ui_colors['bg'], fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)
        
        # 第二列：分離人聲再辨識
        rec_sep_row = tk.Frame(rec_options_frame, bg=self.ui_colors['bg'])
        rec_sep_row.pack(fill=tk.X)
        self.rec_separate_first_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            rec_sep_row,
            text="先分離人聲再辨識（準確度較高，需要較長時間）",
            variable=self.rec_separate_first_var,
            bg=self.ui_colors['bg'],
            fg=self.ui_colors['fg'],
            selectcolor=self.ui_colors['bg'],
            font=("Arial", 9)
        ).pack(side=tk.LEFT, padx=5)

        # 第三列：歌詞語言轉換
        rec_zh_row = tk.Frame(rec_options_frame, bg=self.ui_colors['bg'])
        rec_zh_row.pack(fill=tk.X, pady=(4, 0))
        tk.Label(rec_zh_row, text="輸出文字:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.rec_lyrics_language_var = tk.StringVar(value="traditional")
        tk.Radiobutton(rec_zh_row, text="繁體中文（預設）", variable=self.rec_lyrics_language_var, value="traditional",
                       bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(rec_zh_row, text="簡體中文", variable=self.rec_lyrics_language_var, value="simplified",
                       bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(rec_zh_row, text="原文字（不轉換）", variable=self.rec_lyrics_language_var, value="original",
                       bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=4)

        # 提示標籤
        tk.Label(recognize_tab, text="💡 提示：此功能需要使用語音辨識模型", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], font=("Arial", 9)).pack(anchor=tk.W, padx=10, pady=5)
        
        # --- Tab 6: 合併字幕與影片 ---
        merge_tab = self.content_frames[5]
        
        # 影片檔選擇
        video_frame = tk.LabelFrame(merge_tab, text="影片檔案", padx=10, pady=10, bg=self.ui_colors['bg'])
        video_frame.pack(fill=tk.X, pady=5)
        
        self.merge_video_path_var = tk.StringVar()
        tk.Entry(video_frame, textvariable=self.merge_video_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(video_frame, text="選擇影片", command=self.browse_merge_video, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.LEFT)
        
        # 字幕檔選擇
        subtitle_frame = tk.LabelFrame(merge_tab, text="字幕檔案", padx=10, pady=10, bg=self.ui_colors['bg'])
        subtitle_frame.pack(fill=tk.X, pady=5)
        
        self.merge_subtitle_path_var = tk.StringVar()
        tk.Entry(subtitle_frame, textvariable=self.merge_subtitle_path_var).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        tk.Button(subtitle_frame, text="選擇字幕", command=self.browse_merge_subtitle, width=10, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.LEFT)
        
        # 提醒標籤
        warning_label = tk.Label(
            merge_tab,
            text="⚠️ 提醒：AI 辨識的歌詞可能有錯字，建議您先手動修正，或使用其他 AI 工具修復錯字後再進行字幕合併。",
            font=("Arial", 9),
            fg="#E65100",
            bg=self.ui_colors['bg'],
            wraplength=780,
            justify=tk.LEFT
        )
        warning_label.pack(fill=tk.X, padx=10, pady=(5, 5))
        
        # 字幕樣式設定（JSON 時顯示全部；SRT 時隱藏「已唱顏色」和「變色模式」）
        self.ktv_color_frame = tk.LabelFrame(merge_tab, text="字幕樣式設定", padx=10, pady=10, bg=self.ui_colors['bg'])
        # 先 pack 再立刻 forget，這樣可以保持正確的順序位置
        self.ktv_color_frame.pack(fill=tk.X, pady=5)
        self.ktv_color_frame.pack_forget()
        
        # --- 字幕顏色（SRT 時稱「字幕顏色」；JSON 時稱「未唱顏色」）---
        unplayed_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        unplayed_row.pack(fill=tk.X, pady=5)
        self.unplayed_label = tk.Label(unplayed_row, text="字幕顏色:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W)
        self.unplayed_label.pack(side=tk.LEFT, padx=5)
        
        self.ktv_unplayed_color_var = tk.StringVar(value="#FFFFFF")
        # Color preview rectangle
        self.unplayed_color_preview = tk.Canvas(unplayed_row, width=30, height=20, bg=self.ktv_unplayed_color_var.get(), relief="solid", bd=1)
        self.unplayed_color_preview.pack(side=tk.LEFT, padx=5)
        
        # Color hex entry
        tk.Entry(unplayed_row, textvariable=self.ktv_unplayed_color_var, width=10).pack(side=tk.LEFT, padx=5)
        
        # Color picker button
        def pick_unplayed_color():
            color = colorchooser.askcolor(title="選擇字幕顏色", initialcolor=self.ktv_unplayed_color_var.get())
            if color[1]:
                self.ktv_unplayed_color_var.set(color[1])
                self.update_ktv_color_previews()
        
        tk.Button(unplayed_row, text="選擇顏色", command=pick_unplayed_color, bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        
        # --- 已唱顏色（僅 JSON 顯示）---
        self.played_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        self.played_row.pack(fill=tk.X, pady=5)
        tk.Label(self.played_row, text="已唱顏色:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        
        self.ktv_played_color_var = tk.StringVar(value="#FFFF00")
        # Color preview rectangle
        self.played_color_preview = tk.Canvas(self.played_row, width=30, height=20, bg=self.ktv_played_color_var.get(), relief="solid", bd=1)
        self.played_color_preview.pack(side=tk.LEFT, padx=5)
        
        # Color hex entry
        tk.Entry(self.played_row, textvariable=self.ktv_played_color_var, width=10).pack(side=tk.LEFT, padx=5)
        
        # Color picker button
        def pick_played_color():
            color = colorchooser.askcolor(title="選擇已唱顏色", initialcolor=self.ktv_played_color_var.get())
            if color[1]:
                self.ktv_played_color_var.set(color[1])
                self.update_ktv_color_previews()
        
        tk.Button(self.played_row, text="選擇顏色", command=pick_played_color, bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        
        # --- 邊框顏色 ---
        border_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        border_row.pack(fill=tk.X, pady=5)
        tk.Label(border_row, text="邊框顏色:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        
        self.ktv_border_color_var = tk.StringVar(value="#000000")
        # Color preview rectangle
        self.border_color_preview = tk.Canvas(border_row, width=30, height=20, bg=self.ktv_border_color_var.get(), relief="solid", bd=1)
        self.border_color_preview.pack(side=tk.LEFT, padx=5)
        
        # Color hex entry
        tk.Entry(border_row, textvariable=self.ktv_border_color_var, width=10).pack(side=tk.LEFT, padx=5)
        
        # Color picker button
        def pick_border_color():
            color = colorchooser.askcolor(title="選擇邊框顏色", initialcolor=self.ktv_border_color_var.get())
            if color[1]:
                self.ktv_border_color_var.set(color[1])
                self.update_ktv_color_previews()
        
        tk.Button(border_row, text="選擇顏色", command=pick_border_color, bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        
        # --- 字體選擇 + 字體大小 ---
        font_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        font_row.pack(fill=tk.X, pady=5)
        tk.Label(font_row, text="字幕字體:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        
        # Available fonts
        available_fonts = ["Arial", "微軟正黑體", "新細明體", "標楷體", "DFKai-SB", "Microsoft JhengHei", "Microsoft YaHei"]
        self.ktv_font_var = tk.StringVar(value="微軟正黑體")
        font_menu = ttk.Combobox(font_row, textvariable=self.ktv_font_var, values=available_fonts, state="readonly", width=20)
        font_menu.pack(side=tk.LEFT, padx=5)
        font_menu.bind("<<ComboboxSelected>>", lambda e: self.update_ktv_color_previews())

        tk.Label(font_row, text="字體大小:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(side=tk.LEFT, padx=(20, 5))
        self.ktv_font_size_var = tk.StringVar(value="60")
        font_size_spin = tk.Spinbox(font_row, from_=10, to=200, textvariable=self.ktv_font_size_var, width=5, justify='center')
        font_size_spin.pack(side=tk.LEFT, padx=2)
        tk.Label(font_row, text="pt　（預設 60）", bg=self.ui_colors['bg'], fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=2)

        # --- 字幕高度（垂直位置）---
        margin_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        margin_row.pack(fill=tk.X, pady=5)
        tk.Label(margin_row, text="字幕高度:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.subtitle_margin_var = tk.StringVar(value="0")
        tk.Entry(margin_row, textvariable=self.subtitle_margin_var, width=6, justify='center').pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(margin_row, text="（0 = 預設位置，正數往上，負數往下）",
                 bg=self.ui_colors['bg'], fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

        # --- 變色模式（僅 JSON 顯示）---
        self.mode_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        self.mode_row.pack(fill=tk.X, pady=5)
        tk.Label(self.mode_row, text="變色模式:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        self.ktv_color_mode_var = tk.StringVar(value="char")
        tk.Radiobutton(
            self.mode_row, text="逐字變色（每唱完一個字整個字變色）",
            variable=self.ktv_color_mode_var, value="char",
            bg=self.ui_colors['bg'], fg=self.ui_colors['fg'],
            selectcolor=self.ui_colors['bg'],
            command=self.update_ktv_color_previews
        ).pack(side=tk.LEFT, padx=6)
        tk.Radiobutton(
            self.mode_row, text="滑動漸變（顏色由左至右平滑掃過）",
            variable=self.ktv_color_mode_var, value="slide",
            bg=self.ui_colors['bg'], fg=self.ui_colors['fg'],
            selectcolor=self.ui_colors['bg'],
            command=self.update_ktv_color_previews
        ).pack(side=tk.LEFT, padx=6)

        # 模式說明（僅 JSON 顯示）
        self.mode_hint_row = tk.Frame(self.ktv_color_frame, bg=self.ui_colors['bg'])
        self.mode_hint_row.pack(fill=tk.X, pady=(0, 3))
        self.ktv_mode_hint_label = tk.Label(
            self.mode_hint_row,
            text="  💡 逐字變色：使用 \\k tag，每個字唱完後瞬間切換顏色",
            font=("Arial", 8), fg="gray", bg=self.ui_colors['bg'], anchor=tk.W
        )
        self.ktv_mode_hint_label.pack(side=tk.LEFT, padx=10)

                # 輸出格式
        output_frame = tk.LabelFrame(merge_tab, text="輸出設定", padx=10, pady=10, bg=self.ui_colors['bg'])
        output_frame.pack(fill=tk.X, pady=5)

        # 第一列：輸出格式
        fmt_row = tk.Frame(output_frame, bg=self.ui_colors['bg'])
        fmt_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(fmt_row, text="輸出格式:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(side=tk.LEFT)
        self.merge_video_format_var = tk.StringVar(value="mp4")
        tk.Radiobutton(fmt_row, text="MP4", variable=self.merge_video_format_var, value="mp4", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(fmt_row, text="MKV", variable=self.merge_video_format_var, value="mkv", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], selectcolor=self.ui_colors['bg']).pack(side=tk.LEFT, padx=10)
        
        # --- Tab 7: 環境修復 ---
        repair_tab = self.content_frames[6]
        
        # 組件列表框架
        self.repair_components_frame = tk.Frame(repair_tab, bg=self.ui_colors['bg'])
        self.repair_components_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 按鈕區域
        repair_btn_frame = tk.Frame(repair_tab, bg=self.ui_colors['bg'])
        repair_btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.repair_select_all_btn = tk.Button(repair_btn_frame, text="全選", width=10,
                                                bg="#f0f0f0", cursor='hand2')
        self.repair_select_all_btn.pack(side=tk.LEFT, padx=5)
        
        self.repair_select_none_btn = tk.Button(repair_btn_frame, text="全不選", width=10,
                                                bg="#f0f0f0", cursor='hand2')
        self.repair_select_none_btn.pack(side=tk.LEFT, padx=5)
        
        self.repair_start_btn = tk.Button(repair_btn_frame, text="🔧 開始修復", 
                                          bg=self.ui_colors['warning'], fg="white", 
                                          font=("Arial", 10, "bold"), 
                                          padx=20, pady=8, cursor='hand2')
        self.repair_start_btn.pack(side=tk.RIGHT, padx=5)
        
        # --- Tab 8: 執行日誌 ---
        log_tab = self.content_frames[7]
        log_tab.rowconfigure(1, weight=1)  # 讓 log_area 列可以垂直擴展

        log_header_frame = tk.Frame(log_tab, bg=self.ui_colors['bg'])
        log_header_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(log_header_frame,
                text="執行日誌",
                fg=self.ui_colors['fg'],
                bg=self.ui_colors['bg'],
                font=("Arial", 12, "bold")).pack(side=tk.LEFT)

        def copy_log_to_clipboard():
            content = self.log_area.get("1.0", tk.END).strip()
            if content:
                self.root.clipboard_clear()
                self.root.clipboard_append(content)
                copy_log_btn.config(text="✅ 已複製！")
                self.root.after(2000, lambda: copy_log_btn.config(text="📋 複製到剪貼簿"))
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

        self.log_area = scrolledtext.ScrolledText(log_tab,
                                                  height=20,
                                                  font=("Consolas", 9),
                                                  bg='white',
                                                  fg=self.ui_colors['fg'],
                                                  relief='flat',
                                                  bd=1,
                                                  padx=8,
                                                  pady=8)
        self.log_area.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

        # --- Tab 9: 聯絡作者 ---
        contact_tab = self.content_frames[8]
        
        # 開發者資訊區
        tk.Label(contact_tab, text="【開發者資訊】", font=("Arial", 14, "bold"), 
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W, padx=20, pady=(20, 10))
        tk.Label(contact_tab, text="作者：張書維", font=("Arial", 11), 
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W, padx=40)
        tk.Label(contact_tab, text="Line ID：game76420", font=("Arial", 11), 
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W, padx=40)
        
        fb_frame = tk.Frame(contact_tab, bg=self.ui_colors['bg'])
        fb_frame.pack(anchor=tk.W, padx=40, pady=5)
        tk.Label(fb_frame, text="Facebook：", font=("Arial", 11), 
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(side=tk.LEFT)
        
        fb_link = tk.Label(fb_frame, text="www.facebook.com/changshuwei/", 
                          fg="blue", cursor="hand2", font=("Arial", 11, "underline"),
                          bg=self.ui_colors['bg'])
        fb_link.pack(side=tk.LEFT)
        fb_link.bind("<Button-1>", lambda e: webbrowser.open("https://www.facebook.com/changshuwei/"))

        tk.Label(contact_tab, text="", bg=self.ui_colors['bg']).pack(pady=15)
        
        # 捐款資訊區
        tk.Label(contact_tab, text="【捐款贊助】", font=("Arial", 14, "bold"), 
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W, padx=20, pady=(0, 10))
        tk.Label(contact_tab, text="若您覺得此工具好用，歡迎贊助支持開發者！", 
                 wraplength=600, justify=tk.LEFT, font=("Arial", 11),
                 bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W, padx=40)
        
        bank_frame = tk.Frame(contact_tab, bg=self.ui_colors['bg'])
        bank_frame.pack(anchor=tk.W, padx=40, pady=15)
        tk.Label(bank_frame, text="銀行代碼：822 (中國信託)", font=("Arial", 11),
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W)
        tk.Label(bank_frame, text="帳號：159540291165", font=("Arial", 11, "bold"), fg="#D32F2F",
                bg=self.ui_colors['bg']).pack(anchor=tk.W)
        tk.Label(bank_frame, text="戶名：張書維", font=("Arial", 11),
                bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(anchor=tk.W)
        
        # --- 設定區 (簡化版) ---
        self.settings_frame = tk.LabelFrame(self.right_main_container, text="核心設定", padx=10, pady=5, bg=self.ui_colors['bg'])
        self.settings_frame.pack(fill=tk.X, pady=(0, 5))
        
        # --- 按鈕區 (在核心設定後面) ---
        self.btn_frame = tk.Frame(self.right_main_container, bg=self.ui_colors['bg'])
        self.btn_frame.pack(pady=(0, 3))
        
        # 左側按鈕群組 (包含開始、取消、預覽)
        self.left_btn_frame = tk.Frame(self.btn_frame, bg=self.ui_colors['bg'])
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
        
        # 輸出目錄
        out_row = tk.Frame(self.settings_frame)
        out_row.pack(fill=tk.X, pady=2)
        self.output_dir_var = tk.StringVar(value=self.config.get("output_dir", str(self.app_dir / "output")))
        tk.Label(out_row, text="輸出目錄:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        tk.Entry(out_row, textvariable=self.output_dir_var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        tk.Button(out_row, text="瀏覽", command=self.browse_output_dir, bg=self.ui_colors['info'], fg='white', relief='flat', padx=5, pady=5, cursor='hand2').pack(side=tk.RIGHT)
        
        # 運算裝置與去噪
        self.opt_row = tk.Frame(self.settings_frame)
        opt_row = self.opt_row
        opt_row.pack(fill=tk.X, pady=2)
        
        tk.Label(opt_row, text="運算裝置:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.device_var = tk.StringVar(value="cpu")
        tk.Radiobutton(opt_row, text="CPU", variable=self.device_var, value="cpu").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(opt_row, text="GPU (NVIDIA)", variable=self.device_var, value="gpu").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(opt_row, text="GPU (DirectML)", variable=self.device_var, value="directml").pack(side=tk.LEFT, padx=5)
        
        tk.Button(opt_row, text="🔍 檢測 GPU 環境", command=self.check_gpu_env, 
                  font=("Arial", 9), bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=10)
        
        self.denoise_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_row, text="啟用 AI 去噪 (推薦)", variable=self.denoise_var).pack(side=tk.RIGHT, padx=10)

        # 保留邏輯需要的變數
        self.overlap_var = tk.DoubleVar(value=0.5)
        self.vocal_mix_var = tk.DoubleVar(value=50)
        self.vocal_mix_label_var = tk.StringVar(value="")
        
        # 影片輸出格式 (MKV/MP4)
        self.video_format_var = tk.StringVar(value="mkv")
        # 音軌模式: "dual" = 雙音軌(伴唱+人聲), "lr" = 左伴唱右人聲
        self.audio_track_mode_var = tk.StringVar(value="dual")
        
        # 輸出格式與模型 (分成兩個獨立的 row，方便分別控制顯示)
        self.model_row = tk.Frame(self.settings_frame)
        model_row = self.model_row
        model_row.pack(fill=tk.X, pady=2)
        
        tk.Label(model_row, text="AI 模型:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        model_options =[
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏優化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品質綜合)",
            "Kim_Vocal_2.onnx (MDX - 極致人聲提取)",
            "htdemucs.yaml (Demucs - 4音軌高品質分離)",
            "htdemucs_ft.yaml (Demucs - 流行樂優化)",
            "htdemucs_6s.yaml (Demucs - 6音軌擴充版)"
        ]
        self.model_menu = ttk.Combobox(model_row, textvariable=self.model_var, values=model_options, state="readonly", width=45)
        self.model_menu.pack(side=tk.LEFT, padx=5)
        self.model_menu.current(0)
        
        self.output_format_row = tk.Frame(self.settings_frame)
        output_format_row = self.output_format_row
        output_format_row.pack(fill=tk.X, pady=2)
        
        tk.Label(output_format_row, text="輸出格式:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.output_format_var = tk.StringVar(value="mp3")
        for fmt in ["mp3", "wav", "flac"]:
            tk.Radiobutton(output_format_row, text=fmt.upper(), variable=self.output_format_var, value=fmt).pack(side=tk.LEFT, padx=10)

        # KTV 影片設定列
        self.ktv_row = tk.Frame(self.settings_frame)
        ktv_row = self.ktv_row
        ktv_row.pack(fill=tk.X, pady=2)

        tk.Label(ktv_row, text="KTV 影片格式:").pack(side=tk.LEFT)
        tk.Radiobutton(ktv_row, text="MKV（預設，相容性最佳）",
                       variable=self.video_format_var, value="mkv").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(ktv_row, text="MP4",
                       variable=self.video_format_var, value="mp4").pack(side=tk.LEFT, padx=5)

        # 伴唱帶音軌模式列
        self.track_row = tk.Frame(self.settings_frame)
        track_row = self.track_row
        track_row.pack(fill=tk.X, pady=2)

        tk.Label(track_row, text="伴唱帶音軌:").pack(side=tk.LEFT)
        tk.Radiobutton(track_row, text="雙音軌（伴唱＋人聲，預設）",
                       variable=self.audio_track_mode_var, value="dual").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(track_row, text="左伴唱／右人聲+伴奏（單音軌立體聲）",
                       variable=self.audio_track_mode_var, value="lr").pack(side=tk.LEFT, padx=5)

        # 導唱混合比例列
        self.mix_row = tk.Frame(self.settings_frame)
        mix_row = self.mix_row
        mix_row.pack(fill=tk.X, pady=2)
        tk.Label(mix_row, text="導唱混合比例:").pack(side=tk.LEFT)
        tk.Scale(
            mix_row,
            from_=0, to=100,
            orient=tk.HORIZONTAL,
            showvalue=False,
            resolution=5,
            length=180,
            variable=self.vocal_mix_var,
            command=lambda _value: self.update_vocal_mix_label()
        ).pack(side=tk.LEFT, padx=5)
        tk.Label(mix_row, textvariable=self.vocal_mix_label_var, width=28, anchor="w").pack(side=tk.LEFT, padx=5)
        tk.Label(mix_row, text="人聲越高，越適合跟唱練習", fg="#666").pack(side=tk.LEFT, padx=5)
        self.update_vocal_mix_label()

        # 歌詞識別選項列
        self.lyrics_row = tk.Frame(self.settings_frame)
        lyrics_row = self.lyrics_row
        lyrics_row.pack(fill=tk.X, pady=2)
        
        self.enable_lyrics_recognition_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            lyrics_row,
            text="啟用 Whisper AI 歌詞識別（產生 SRT 字幕檔）",
            variable=self.enable_lyrics_recognition_var,
            font=("Arial", 9, "bold")
        ).pack(side=tk.LEFT)
        
        tk.Label(lyrics_row, text="輸出文字:").pack(side=tk.LEFT, padx=(20, 5))
        self.lyrics_language_var = tk.StringVar(value="traditional")
        tk.Radiobutton(lyrics_row, text="繁體中文", variable=self.lyrics_language_var, value="traditional").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(lyrics_row, text="簡體中文", variable=self.lyrics_language_var, value="simplified").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(lyrics_row, text="原文字", variable=self.lyrics_language_var, value="original").pack(side=tk.LEFT, padx=4)

        # 歌詞識別語言與模型列
        self.lyrics_options_row = tk.Frame(self.settings_frame)
        lyrics_options_row = self.lyrics_options_row
        lyrics_options_row.pack(fill=tk.X, pady=2)

        tk.Label(lyrics_options_row, text="辨識語言:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg'], width=12, anchor='w').pack(side=tk.LEFT)
        self.yt_whisper_language_var = tk.StringVar(value="zh")
        yt_lang_menu = ttk.Combobox(lyrics_options_row, textvariable=self.yt_whisper_language_var,
                                    values=["zh (中文)", "en (英文)", "ja (日文)", "ko (韓文)"],
                                    state="readonly", width=12)
        yt_lang_menu.pack(side=tk.LEFT, padx=5)

        tk.Label(lyrics_options_row, text="模型大小:", bg=self.ui_colors['bg'], fg=self.ui_colors['fg']).pack(side=tk.LEFT, padx=(20, 5))
        self.yt_whisper_model_var = tk.StringVar(value="medium")
        yt_model_menu = ttk.Combobox(lyrics_options_row, textvariable=self.yt_whisper_model_var,
                                     values=["tiny", "base", "small", "medium", "large"],
                                     state="readonly", width=8)
        yt_model_menu.pack(side=tk.LEFT, padx=5)
        tk.Label(lyrics_options_row, text="（medium 準確度高，large 最準但較慢）",
                 bg=self.ui_colors['bg'], fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

        # 額外影片選項列
        self.extra_video_row = tk.Frame(self.settings_frame)
        extra_video_row = self.extra_video_row
        extra_video_row.pack(fill=tk.X, pady=2)

        self.force_1080p_var = tk.BooleanVar(value=False)
        self.force_1080p_chk = tk.Checkbutton(
            extra_video_row,
            text="強制等比輸出 1080p（不足自動補黑邊）",
            variable=self.force_1080p_var
        )
        self.force_1080p_chk.pack(side=tk.LEFT)

        self.yt_cc_var = tk.BooleanVar(value=False)
        self.yt_cc_chk = tk.Checkbutton(
            extra_video_row,
            text="啟用 YouTube CC 字幕處理",
            variable=self.yt_cc_var,
            command=self.refresh_yt_subtitle_mode_ui
        )
        self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))

        self.yt_subtitle_mode_var = tk.StringVar(value="mux")
        self.yt_subtitle_mode_row = tk.Frame(self.settings_frame)
        tk.Label(self.yt_subtitle_mode_row, text="字幕模式:").pack(side=tk.LEFT)
        tk.Radiobutton(
            self.yt_subtitle_mode_row,
            text="下載SRT字幕",
            variable=self.yt_subtitle_mode_var,
            value="srt_only",
            command=self.refresh_yt_subtitle_mode_ui
        ).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(
            self.yt_subtitle_mode_row,
            text="下載srt字幕並合成",
            variable=self.yt_subtitle_mode_var,
            value="mux",
            command=self.refresh_yt_subtitle_mode_ui
        ).pack(side=tk.LEFT, padx=5)
        
        # 狀態與進度 (移動回 setup_ui 正確位置)
        self.status_frame = tk.Frame(self.right_scrollable_frame, bg=self.ui_colors['bg'])
        self.status_frame.pack(fill=tk.X, padx=20, pady=(4, 0))
        self.status_var = tk.StringVar(value="狀態: 就緒")
        self.status_label = tk.Label(self.status_frame, 
                                     textvariable=self.status_var, 
                                     fg=self.ui_colors['primary'],
                                     bg=self.ui_colors['bg'],
                                     font=("Arial", 10))
        self.status_label.pack(side=tk.LEFT)
        
        self.progress_text = tk.Label(self.status_frame, 
                                      text="0%", 
                                      font=("Arial", 9, "bold"),
                                      fg=self.ui_colors['fg'],
                                      bg=self.ui_colors['bg'])
        self.progress_text.pack(side=tk.RIGHT)
        
        # 細項進度條
        progress_header = tk.Frame(self.right_scrollable_frame, bg=self.ui_colors['bg'])
        progress_header.pack(fill=tk.X, padx=20, pady=(3, 0))
        tk.Label(progress_header,
                text="進度:",
                fg=self.ui_colors['fg'],
                bg=self.ui_colors['bg'],
                font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.step_label = tk.Label(progress_header,
                text="",
                fg=self.ui_colors['info'],
                bg=self.ui_colors['bg'],
                font=("Arial", 9))
        self.step_label.pack(side=tk.LEFT, padx=(8, 0))
        self.item_progress_bar = ttk.Progressbar(self.right_scrollable_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.item_progress_bar.pack(fill=tk.X, padx=20, pady=(2, 5))

        # 立即刷新視窗並顯示歡迎訊息
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
        
        # 確保 common_lib_dir 在 sys.path 中
        if hasattr(self, 'common_lib_dir'):
            if str(self.common_lib_dir) not in sys.path:
                sys.path.insert(0, str(self.common_lib_dir))
        
        # 嘗試使用 zhconv 套件（純 Python，無編譯問題）
        zhconv_available = False
        try:
            import zhconv
            zhconv_available = True
        except ImportError:
            # 嘗試自動安裝 zhconv
            try:
                self.log("  📦 正在安裝 zhconv 中文簡繁轉換套件...")
                self.log("     （這可能需要幾秒鐘，請稍候...）")
                self.update_status("正在安裝 zhconv 套件...", "orange")
                
                # 使用本地 Python 安裝到 common_lib 目錄
                if hasattr(self, 'local_python') and hasattr(self, 'common_lib_dir'):
                    import subprocess
                    result = subprocess.run(
                        [str(self.local_python), '-m', 'pip', 'install', 
                         '--target', str(self.common_lib_dir), 'zhconv'],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        self.log("  ✅ zhconv 安裝成功！")
                        # 重新檢查匯入
                        try:
                            # 清除可能的快取
                            if 'zhconv' in sys.modules:
                                del sys.modules['zhconv']
                            # 確保路徑在最前面
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
        
        # 使用 zhconv 進行轉換
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

    def _get_ytdlp_js_runtime_opts(self):
        """自動偵測 yt-dlp 可用的 JS runtime，提升 YouTube 資訊與字幕擷取成功率。"""
        if self._yt_js_runtime_cache is not None:
            return list(self._yt_js_runtime_cache)

        runtime_candidates = [
            ("deno", shutil.which("deno")),
            ("node", shutil.which("node")),
            ("bun", shutil.which("bun")),
        ]

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
        """回傳可實際啟動 yt-dlp 的命令前綴，避免 embed Python 下 `-m yt_dlp` 匯入失敗。"""
        ytdlp_exe = self.ytdlp_dir / "yt-dlp.exe"
        if ytdlp_exe.exists():
            return [str(ytdlp_exe)]
        
        ytdlp_exe = self.py_dir / "Scripts" / "yt-dlp.exe"
        if ytdlp_exe.exists():
            return [str(ytdlp_exe)]
        
        ytdlp_exe = self.lib_dir / "bin" / "yt-dlp.exe"
        if ytdlp_exe.exists():
            return [str(ytdlp_exe)]

        ytdlp_main = self.ytdlp_dir / "yt_dlp" / "__main__.py"
        if ytdlp_main.exists():
            return [str(self.local_python), str(ytdlp_main)]
        
        ytdlp_main = self.lib_dir / "yt_dlp" / "__main__.py"
        if ytdlp_main.exists():
            return [str(self.local_python), str(ytdlp_main)]

        return [str(self.local_python), "-m", "yt_dlp"]

    def refresh_start_button_text(self):
        """依當前分頁與字幕模式更新主按鈕文字。"""
        current_tab = self.current_tab_index
        vfmt = self.video_format_var.get().upper() if hasattr(self, 'video_format_var') else "MKV"
        if current_tab == 6 or current_tab == 7 or current_tab == 8:
            self.btn_frame.pack_forget()
        else:
            self.btn_frame.pack(pady=(0, 3))
            # 更新開始按鈕文字
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
            elif current_tab == 5:
                self.start_btn.config(text="開始合併字幕與影片", bg=self.ui_colors['warning'])

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

    def normalize_subtitle_filename(self, subtitle_file, desired_stem):
        """將字幕檔改為固定主檔名，副檔名統一為 .srt。"""
        try:
            subtitle_path = Path(subtitle_file)
            if not subtitle_path.exists():
                return subtitle_file
            desired_path = subtitle_path.parent / f"{desired_stem}.srt"
            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)
            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass
            shutil.move(str(subtitle_path), str(desired_path))
            return str(desired_path)
        except Exception:
            return subtitle_file

    def _toggle_dl_separate_options(self):
        """根據音訊分離 Checkbox 顯示或隱藏分離設定區"""
        if self.dl_do_separate_var.get():
            self.dl_sep_options_frame.pack(fill=tk.X, pady=(2, 4), padx=0)
            # 如果在 YouTube 下載分頁（Tab 2，index 1），同時顯示核心設定 (運算裝置、AI 模型)
            if self.current_tab_index == 1:
                self.opt_row.pack(fill=tk.X, pady=2)
                self.opt_row.pack_forget()
                self.model_row.pack_forget()

    def switch_tab(self, index):
        """切換到指定的分頁"""
        if self.current_tab_index == index:
            return
        
        # 隱藏目前的頁面和相關設定
        if self.current_tab_index == 1:  # 離開 YouTube 下載頁
            self.dl_sep_options_frame.pack_forget()
            self.opt_row.pack_forget()
            self.model_row.pack_forget()
        
        # 隱藏目前的頁面
        self.content_frames[self.current_tab_index].grid_remove()
        
        # 顯示新的頁面，日誌分頁需要撐滿，其他靠上排列
        self.current_tab_index = index
        # index 7 = 執行日誌，需要撐滿高度；其他靠上不 expand
        is_log = (index == 7)
        if is_log:
            self.tab_container.pack_configure(fill=tk.BOTH, expand=True)
        else:
            self.tab_container.pack_configure(fill=tk.X, expand=False)
        sticky = "nsew" if is_log else "new"
        self.content_frames[index].grid(row=0, column=0, sticky=sticky, padx=3, pady=2)
        
        # 如果切換到環境修復分頁，更新元件列表
        if self.current_tab_index == 6:
            self.check_components(prompt=False)
        
        # 顯示分離選項（如果是 YouTube 下載頁且勾選了分離）
        if self.current_tab_index == 1 and self.dl_do_separate_var.get():
            self.dl_sep_options_frame.pack(fill=tk.X, pady=(2, 4), padx=0)
            self.opt_row.pack(fill=tk.X, pady=2)
            self.model_row.pack(fill=tk.X, pady=2)
        
        # 強制更新佈局
        self.root.update_idletasks()
        
        # 更新按鈕樣式
        self.update_tab_buttons(index)
        
        # 呼叫原來的 on_tab_changed 邏輯
        self._on_tab_changed_logic(index)
    
    def update_tab_buttons(self, active_index):
        """更新按鈕的視覺樣式"""
        for i, btn in enumerate(self.tab_buttons):
            if i == active_index:
                # 選中的按鈕
                btn.configure(bg=self.ui_colors['primary'], 
                            fg='white',
                            relief='sunken')
            else:
                # 未選中的按鈕
                btn.configure(bg=self.ui_colors['bg'], 
                            fg=self.ui_colors['fg'],
                            relief='flat')
    
    def _on_tab_changed_logic(self, current_tab):
        """原來的 on_tab_changed 邏輯，用於處理核心設定顯示/隱藏等"""
        # Tab 1=YouTube KTV, Tab 2=純下載, Tab 3=本地KTV, Tab 4=批量分離, Tab 5=合併字幕, Tab 6=環境修復, Tab 7=日誌, Tab 8=聯絡作者
        is_download_only = (current_tab == 1)
        is_merge_tab = (current_tab == 5)
        is_repair_tab = (current_tab == 6)
        is_log_tab = (current_tab == 7)
        is_contact_tab = (current_tab == 8)
        
        # 合併字幕 Tab、環境修復 Tab、日誌 Tab 和聯絡作者 Tab 不需要顯示核心設定
        if is_merge_tab or is_repair_tab or is_log_tab or is_contact_tab:
            self.settings_frame.pack_forget()
        else:
            # 確保核心設定被放在正確的位置
            self.settings_frame.pack(fill=tk.X, pady=(0, 3))
            
            is_recognize_tab = (current_tab == 4)
            
            # 純下載 Tab 和辨識歌詞 Tab 不需要 KTV 相關設定和輸出格式選擇
            rows_for_ai_except_opt_model = [self.output_format_row, self.ktv_row, self.track_row, self.mix_row]
            for row in rows_for_ai_except_opt_model:
                if is_download_only or is_recognize_tab:
                    row.pack_forget()
                else:
                    row.pack(fill=tk.X, pady=2)
            
            # 歌詞識別列：純下載和辨識歌詞 Tab 不顯示
            if is_download_only or is_recognize_tab:
                self.lyrics_row.pack_forget()
                self.lyrics_options_row.pack_forget()
            else:
                self.lyrics_row.pack(fill=tk.X, pady=2)
                self.lyrics_options_row.pack(fill=tk.X, pady=2)
            
            # 顯示/隱藏核心設定 (運算裝置、AI 模型) 根據分頁和分離選項
            if current_tab == 1:
                # YouTube 下載分頁：只有勾選「下載後進行 AI 音訊分離」才顯示
                if self.dl_do_separate_var.get():
                    self.opt_row.pack(fill=tk.X, pady=2)
                    self.model_row.pack(fill=tk.X, pady=2)
                else:
                    self.opt_row.pack_forget()
                    self.model_row.pack_forget()
            else:
                # 其他分頁：永遠顯示核心設定
                self.opt_row.pack(fill=tk.X, pady=2)
                self.model_row.pack(fill=tk.X, pady=2)

            # 額外影片選項只在 KTV 影片流程顯示；YouTube CC 僅在第 1 籤頁顯示
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
        
        # 更新啟動按鈕文字
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
        file_paths = filedialog.askopenfilenames(
            title="選擇影片檔案",
            filetypes=[("影片檔案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有檔案", "*.*")]
        )
        if file_paths:
            for fp in file_paths:
                fp_abs = str(Path(fp).absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))

    def browse_local_v_folder(self):
        folder_path = filedialog.askdirectory(title="選擇影片資料夾")
        if folder_path:
            for fp in Path(folder_path).glob("*.mp4"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.mkv"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.avi"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.mov"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.wmv"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.webm"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))

    def remove_selected_v(self):
        selected = self.v_listbox.curselection()
        for index in reversed(selected):
            self.v_list.pop(index)
            self.v_listbox.delete(index)

    def clear_v_list(self):
        self.v_list =[]
        self.v_listbox.delete(0, tk.END)

    def start_local_v_process(self):
        if not self.v_list:
            messagebox.showwarning("警告", "請先加入影片檔案！")
            return
        if self.is_processing: return

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在進行批次影片處理...", "orange")
        
        threading.Thread(target=self.local_v_batch_process, daemon=True).start()

    def local_v_batch_process(self):
        try:
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir): os.makedirs(output_dir)
            enable_lyrics = self.enable_lyrics_recognition_var.get()
            
            total = len(self.v_list)
            for i, video_path in enumerate(self.v_list):
                if not self.is_processing or self.cancel_event.is_set():
                    self.log("🛑 批次處理已中止。")
                    break
                
                video_stem = Path(video_path).stem
                self.log(f"\n--- 正在處理 ({i+1}/{total}): {os.path.basename(video_path)} ---")
                
                # 更新 Listbox 顯示目前處理中
                self.v_listbox.selection_clear(0, tk.END)
                self.v_listbox.selection_set(i)
                self.v_listbox.see(i)
                
                temp_audio = Path(output_dir) / f"{video_stem}_temp_audio.mp3"
                
                # 1. 擷取音訊
                progress_base = int((i / total) * 100)
                progress_step = int(100 / total)
                
                self.update_progress(progress_base + int(progress_step * 0.1), f"正在擷取音訊 ({i+1}/{total})")
                self.log("  > 正在從影片擷取音訊...")
                ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
                extract_cmd =[
                    str(ffmpeg_exe), "-y", "-i", video_path,
                    "-vn", "-acodec", "libmp3lame", "-ab", "320k", str(temp_audio)
                ]
                subprocess.run(extract_cmd, check=True, creationflags=self.subp_flags)
                
                # 2. 執行分離
                self.update_progress(progress_base + int(progress_step * 0.3), f"正在 AI 分離 ({i+1}/{total})")
                success = self.run_audio_separator(str(temp_audio), output_dir)
                
                if success:
                    # 歌詞識別（如果啟用）- do this BEFORE consolidate_stems which deletes the file
                    srt_subtitle = None
                    json_subtitle = None
                    if enable_lyrics:
                        result = self.recognize_lyrics_and_generate_srt(str(temp_audio), output_dir)
                        if result:
                            srt_subtitle, json_subtitle, _ = result
                            final_srt_path = Path(output_dir) / f"{video_stem}_KTV.srt"
                            final_json_path = Path(output_dir) / f"{video_stem}_KTV.json"
                            
                            # 強制重新命名 SRT
                            self.log(f"  📝 處理 SRT 字幕檔...")
                            try:
                                old_srt_path = Path(srt_subtitle)
                                self.log(f"  📝 原始檔案: {old_srt_path.name}")
                                self.log(f"  📝 目標檔案: {final_srt_path.name}")
                                
                                if old_srt_path.exists():
                                    if final_srt_path.exists():
                                        self.log(f"  📝 刪除舊的目標檔案")
                                        final_srt_path.unlink()
                                    self.log(f"  📝 執行重新命名...")
                                    shutil.move(str(old_srt_path), str(final_srt_path))
                                    srt_subtitle = str(final_srt_path)
                                    self.log(f"  ✅ SRT 已重新命名為: {final_srt_path.name}")
                            except Exception as e:
                                self.log(f"  ❌ SRT 重新命名失敗: {str(e)}")
                                import traceback
                                self.log(f"     {traceback.format_exc()}")
                            
                            # 強制重新命名 JSON
                            if json_subtitle:
                                self.log(f"  📝 處理 JSON 歌詞檔...")
                                try:
                                    old_json_path = Path(json_subtitle)
                                    self.log(f"  📝 原始檔案: {old_json_path.name}")
                                    self.log(f"  📝 目標檔案: {final_json_path.name}")
                                    
                                    if old_json_path.exists():
                                        if final_json_path.exists():
                                            self.log(f"  📝 刪除舊的目標檔案")
                                            final_json_path.unlink()
                                        self.log(f"  📝 執行重新命名...")
                                        shutil.move(str(old_json_path), str(final_json_path))
                                        self.log(f"  ✅ JSON 已重新命名為: {final_json_path.name}")
                                except Exception as e:
                                    self.log(f"  ❌ JSON 重新命名失敗: {str(e)}")
                                    import traceback
                                    self.log(f"     {traceback.format_exc()}")
                    
                    # 分隔完成，現在整理檔案
                    self.log("  > 正在整理產出檔案...")
                    voc_file, inst_file = self.consolidate_stems(str(temp_audio), video_path, output_dir)
                    
                    if voc_file and inst_file:
                        # 3. 合成 KTV 影片
                        vfmt = self.video_format_var.get()
                        self.update_progress(progress_base + int(progress_step * 0.8), f"正在合成 {vfmt.upper()} ({i+1}/{total})")
                        output_file = Path(output_dir) / f"{video_stem}_KTV.{vfmt}"
                        mkv_success = self.synthesize_mkv(video_path, voc_file, inst_file, str(output_file), subtitle_file=srt_subtitle)
                        
                        if mkv_success:
                            self.log(f"✅ 成功生成 {vfmt.upper()}: {output_file.name}")
                            # Whisper生成的字幕已经是 {video_stem}_KTV.srt，与最终视频文件名一致
                        else:
                            self.log(f"❌ {video_stem} MKV 合成失敗。")
                    else:
                        self.log(f"❌ {video_stem} 找不到分離後的必要檔案。")
                else:
                    self.log(f"❌ {video_stem} 音訊分離失敗。")

            self.update_progress(100, "批次處理完成")
            self.log("\n✨ 所有影片批次處理任務已結束！")
            messagebox.showinfo("完成", f"已完成 {total} 個影片的處理！\n檔案已儲存至: {output_dir}")
            if os.name == 'nt':
                os.startfile(output_dir)
                
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
        if self.is_processing: return

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在下載 YouTube 檔案...", "orange")
        
        threading.Thread(target=self.pure_download_process, args=(url,), daemon=True).start()

    def pure_download_process(self, url):
        try:
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            dl_type = self.dl_type_var.get()
            quality  = self.dl_quality_var.get()
            do_separate = self.dl_do_separate_var.get()

            self.log(f"🚀 開始下載任務 (格式: {dl_type.upper()}，畫質: {quality}，音訊分離: {'是' if do_separate else '否'})...")

            downloaded_mp3_files = []
            mp3_download_success = False

            if dl_type in ["both", "mp4"]:
                self.log("  > 正在下載 MP4...")
                self.pure_download_file(url, output_dir, "mp4", quality)

            if dl_type in ["both", "mp3"]:
                self.log("  > 正在下載 MP3...")
                mp3_download_success, downloaded_mp3_files = self.pure_download_file(url, output_dir, "mp3", quality)

            # 若勾選音訊分離且 MP3 下載成功，才對 MP3 進行 AI 分離
            if do_separate and dl_type in ["both", "mp3"]:
                if mp3_download_success and downloaded_mp3_files:
                    self.log("\n🎵 開始 AI 音訊分離（人聲／伴奏）...")
                    target_mp3 = str(downloaded_mp3_files[0])
                    self.log(f"  > 正在分離: {downloaded_mp3_files[0].name}")
                    original_format = self.output_format_var.get()
                    self.output_format_var.set(self.dl_sep_format_var.get())
                    sep_success = self.run_audio_separator(target_mp3, output_dir)
                    self.output_format_var.set(original_format)
                    if sep_success:
                        self.log("  ✅ 音訊分離完成！")
                        
                        # 如果勾選了「左伴奏/右人聲+伴奏」選項，則合成立體聲
                        if self.dl_sep_lr_var.get():
                            self.log("\n🎚️ 正在合成「左伴奏/右人聲+伴奏」立體聲...")
                            vocal_file, instrumental_file = self.find_separated_files(target_mp3, output_dir, self.dl_sep_format_var.get())
                            if vocal_file and instrumental_file:
                                output_lr_file = Path(output_dir) / f"{Path(target_mp3).stem}_左伴奏右人聲+伴奏.mp3"
                                if self.create_lr_stereo(instrumental_file, vocal_file, str(output_lr_file)):
                                    self.log(f"  ✅ 「左伴奏/右人聲+伴奏」立體聲完成: {output_lr_file.name}")
                                else:
                                    self.log("  ❌ 合成失敗。")
                            else:
                                self.log("  ❌ 找不到分離後的人聲或伴奏檔案。")
                    else:
                        self.log("  ❌ 音訊分離失敗，請檢查日誌。")
                else:
                    self.log("\n⚠️ MP3 下載失敗或未找到新檔案，跳過音訊分離。")
            elif do_separate and dl_type == "mp4":
                self.log("\n⚠️ 音訊分離需要 MP3 檔案。請選擇「MP3 + MP4」或「僅 MP3」以啟用分離。")

            self.log("\n✅ 所有任務已全部完成！")
            messagebox.showinfo("完成", "YouTube 下載" + ("及音訊分離" if do_separate else "") + "成功！")
            if os.name == 'nt' and os.path.exists(output_dir):
                os.startfile(output_dir)
        except Exception as e:
            self.log(f"❌ 下載過程中出錯: {str(e)}")
        finally:
            self.finish_processing()

    def pure_download_file(self, url, output_dir, file_type, quality="1080"):
        """純下載單一格式，完全不走 KTV/分離邏輯，檔名直接使用影片標題。
        回傳: (是否成功, 新下載的檔案路徑列表)
        """
        ytdlp_cmd_base = self._get_ytdlp_command_base()
        # 確保 yt_dlp 模組能從 lib_dir 和 ytdlp_dir 找到（--target 安裝後不在 site-packages 中）
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = os.pathsep.join([str(self.ytdlp_dir), str(self.common_lib_dir), str(self.lib_dir)])

        # 定義兩個 common_opts：一個有 cookie，一個沒有，加上重試和延遲來處理 429
        common_opts_with_cookie = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "2",
            "--sleep-interval", "3"
        ] + self._get_cookie_opts(force_no_cookie=False)
        
        common_opts_no_cookie = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "2",
            "--sleep-interval", "3"
        ] + self._get_cookie_opts(force_no_cookie=True)

        # 使用影片標題作為檔名，並限制長度（使用字元數 .100s 而非位元組 .60B 避免截斷造成亂碼）
        out_template = os.path.join(output_dir, "%(title).100s.%(ext)s")
        
        current_common_opts = common_opts_with_cookie
        try_no_cookie = True
        new_file_list = []
        
        while try_no_cookie:
            try_no_cookie = False
            
            if file_type == "mp3":
                cmd = ytdlp_cmd_base + current_common_opts + [
                    "-x",
                    "--audio-format", "mp3",
                    "--audio-quality", "320K",
                    "-o", out_template,
                    url
                ]
            else:
                # MP4 畫質映射
                if quality == "best":
                    fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                else:
                    fmt = (f"bestvideo[ext=mp4][height<={quality}]"
                           f"+bestaudio[ext=m4a]"
                           f"/best[ext=mp4][height<={quality}]"
                           f"/best[ext=mp4]/best")
                cmd = ytdlp_cmd_base + current_common_opts + [
                    "-f", fmt,
                    "--merge-output-format", "mp4",
                    "-o", out_template,
                    url
                ]

            self.log(f"    執行: {file_type.upper()} 下載中...")
            # 記錄下載前的輸出目錄檔案清單，供事後驗證
            ext = "mp3" if file_type == "mp3" else "mp4"
            files_before = set(Path(output_dir).glob(f"*.{ext}"))

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                creationflags=self.subp_flags, encoding='utf-8', errors='replace',
                env=ytdlp_env
            )
            self._current_process = process
            last_percent = -1
            has_cookie_error = False
            recent_errors = []

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    self.log("🛑 下載已取消。")
                    return False, []
                line = line.strip()
                if not line:
                    continue
                
                # 檢查是否有 Cookie 錯誤
                if "could not copy chrome cookie" in line.lower() or "cookie database" in line.lower():
                    has_cookie_error = True
                
                # 進度條：只在整數 % 變化時才輸出，避免洗版
                if "[download]" in line and "%" in line:
                    m = re.search(r"(\d+\.\d+)%", line)
                    if m:
                        pct = float(m.group(1))
                        if int(pct) > last_percent:
                            self.log(f"    {line}")
                            last_percent = int(pct)
                            self.update_progress(int(pct), f"下載 {file_type.upper()}",
                                                 step_text=f"下載 {file_type.upper()} {int(pct)}%")
                elif "ERROR" in line.upper():
                    self.log(f"  ❌ {line}")
                    recent_errors.append(line)
                    # 加入中文錯誤解釋
                    error_lower = line.lower()
                    if "this video is not available" in error_lower:
                        self.log(f"  💡 中文說明：此影片無法存取！可能原因：")
                        self.log(f"     1. 影片是私人影片")
                        self.log(f"     2. 影片已被刪除")
                        self.log(f"     3. 影片有地區鎖定（Geo-block）")
                        self.log(f"     4. 請確認您使用的是「單一影片」連結，不是播放列表或電台連結！")
                    elif "video unavailable" in error_lower:
                        self.log(f"  💡 中文說明：影片無法使用！")
                    elif "age restricted" in error_lower:
                        self.log(f"  💡 中文說明：此影片有年齡限制，建議使用 Cookie 選項！")
                    elif "sign in to confirm" in error_lower:
                        self.log(f"  💡 中文說明：需要登入確認年齡，請在設定中選擇瀏覽器 Cookie！")
                    elif "cookie database" in error_lower or "could not copy chrome" in error_lower:
                        self.log(f"  💡 中文說明：無法讀取瀏覽器 Cookie，正在自動重試而不用 Cookie...")
                    elif "429" in error_lower or "too many requests" in error_lower:
                        self.log(f"  💡 中文說明：YouTube 封鎖太多請求！")
                        self.log(f"     已自動開啟重試和延遲機制，請稍候...")
                        self.log(f"     如果還是失敗，請稍後再試或選擇瀏覽器 Cookie！")
                else:
                    # 顯示其他非進度條訊息（ffmpeg、警告、Destination 等）
                    self.log(f"    {line}")

            process.wait()
            self._current_process = None
            
            # 如果有 Cookie 錯誤且是第一次嘗試，切換到無 Cookie 模式
            if has_cookie_error and current_common_opts is common_opts_with_cookie:
                self.log("  ℹ️ 偵測到 Cookie 錯誤，正在切換到無 Cookie 模式重試...")
                current_common_opts = common_opts_no_cookie
                try_no_cookie = True
                continue

            # 驗證是否真的產出了新檔案
            files_after = set(Path(output_dir).glob(f"*.{ext}"))
            new_files = files_after - files_before
            new_file_list = sorted(list(new_files), key=lambda f: f.stat().st_mtime, reverse=True)
            
            if new_files:
                for nf in new_file_list:
                    self.log(f"  ✅ {file_type.upper()} 下載完成：{nf.name}")
                return True, new_file_list
            elif process.returncode == 0:
                self.log(f"  ⚠️ yt-dlp 回報成功但在輸出目錄找不到新的 {ext.upper()} 檔案。")
                self.log("     可能原因：ffmpeg 未安裝／路徑錯誤、格式合併失敗，請查看上方日誌。")
                return False, []
            else:
                self.log(f"  ❌ {file_type.upper()} 下載失敗（代碼: {process.returncode}）。")
                return False, []

    def quick_paste_url(self, event):
        """點擊輸入框時，若剪貼簿包含新的 YouTube 網址，則自動更新貼上"""
        try:
            clipboard = self.root.clipboard_get().strip()
            current_val = self.yt_url_var.get().strip()
            
            if clipboard and clipboard != current_val:
                # 簡單驗證是否為 YouTube 網址（涵蓋 shorts、嵌入、標準格式）
                if "youtube.com/" in clipboard or "youtu.be/" in clipboard:
                    self.yt_url_var.set(clipboard)
                    self.log(f"📋 已從剪貼簿更新網址: {clipboard}")
                    # 提示播放清單只下載第一支
                    if "list=" in clipboard and "watch?v=" not in clipboard and "/shorts/" not in clipboard:
                        self.log("⚠️ 偵測到播放清單連結，本工具僅會下載第一支影片（已加入 --no-playlist）。")
        except Exception:
            pass  # 剪貼簿為空或格式不支援

    def quick_paste_dl_url(self, event):
        """Tab 2 專屬：點擊輸入框時自動貼上剪貼簿中的 YouTube 網址"""
        try:
            clipboard = self.root.clipboard_get().strip()
            current_val = self.yt_dl_url_var.get().strip()
            if clipboard and clipboard != current_val:
                if "youtube.com/" in clipboard or "youtu.be/" in clipboard:
                    self.yt_dl_url_var.set(clipboard)
                    self.log(f"📋 [下載分頁] 已從剪貼簿貼上網址: {clipboard}")
                    if "list=" in clipboard and "watch?v=" not in clipboard and "/shorts/" not in clipboard:
                        self.log("⚠️ 偵測到播放清單，僅下載第一支影片（--no-playlist）。")
        except Exception:
            pass

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
        
        # 讓視窗顯示在中央
        contact_window.transient(self.root)
        contact_window.grab_set()
        
        # 計算置中位置
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (420 // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (380 // 2)
        contact_window.geometry(f"+{x}+{y}")

        main_frame = tk.Frame(contact_window, padx=30, pady=25)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 開發者資訊區
        tk.Label(main_frame, text="【開發者資訊】", font=("Arial", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        tk.Label(main_frame, text="作者：張書維", font=("Arial", 10)).pack(anchor=tk.W, padx=15)
        tk.Label(main_frame, text="Line ID：game76420", font=("Arial", 10)).pack(anchor=tk.W, padx=15)
        
        fb_frame = tk.Frame(main_frame)
        fb_frame.pack(anchor=tk.W, padx=15, pady=2)
        tk.Label(fb_frame, text="Facebook：", font=("Arial", 10)).pack(side=tk.LEFT)
        
        fb_link = tk.Label(fb_frame, text="www.facebook.com/changshuwei/", 
                          fg="blue", cursor="hand2", font=("Arial", 10, "underline"))
        fb_link.pack(side=tk.LEFT)
        
        # 綁定點擊事件
        fb_link.bind("<Button-1>", lambda e: webbrowser.open("https://www.facebook.com/changshuwei/"))

        tk.Label(main_frame, text="").pack(pady=5) # 間隔
        
        # 捐款資訊區
        tk.Label(main_frame, text="【捐款贊助】", font=("Arial", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        tk.Label(main_frame, text="若您覺得此工具好用，歡迎贊助支持開發者！", 
                 wraplength=350, justify=tk.LEFT, font=("Arial", 10)).pack(anchor=tk.W, padx=15)
        
        bank_frame = tk.Frame(main_frame)
        bank_frame.pack(anchor=tk.W, padx=15, pady=10)
        tk.Label(bank_frame, text="銀行代碼：822 (中國信託)", font=("Arial", 10)).pack(anchor=tk.W)
        tk.Label(bank_frame, text="帳號：159540291165", font=("Arial", 10, "bold"), fg="#D32F2F").pack(anchor=tk.W)
        tk.Label(bank_frame, text="戶名：張書維", font=("Arial", 10)).pack(anchor=tk.W)
        
        # 關閉按鈕
        tk.Button(main_frame, text="我知道了", command=contact_window.destroy, 
                  width=15, bg="#f0f0f0").pack(pady=(20, 0))

    def log(self, message):
        self.root.after(0, lambda: self._safe_log(message))

    def _safe_log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        # Update status bar with latest log
        if self.is_processing:
            # Clean up message for status (remove emojis or extra whitespace)
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
            for f in filenames:
                f_abs = str(Path(f).absolute())
                if f_abs not in self.file_list:
                    self.file_list.append(f_abs)
                    self.file_listbox.insert(tk.END, os.path.basename(f_abs))

    def remove_selected_file(self):
        selected = self.file_listbox.curselection()
        for index in reversed(selected):
            self.file_list.pop(index)
            self.file_listbox.delete(index)

    def clear_files(self):
        self.file_list =[]
        self.file_listbox.delete(0, tk.END)

    def load_config(self):
        """載入設定檔，若不存在則建立預設設定"""
        default_config = {
            "output_dir": str(self.app_dir / "output"),
            "open_folder_after_complete": False
        }
        
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                # 合併預設設定，確保所有欄位都存在
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                self.config = config
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
            # 更新模式說明文字
            if hasattr(self, 'ktv_mode_hint_label') and hasattr(self, 'ktv_color_mode_var'):
                mode = self.ktv_color_mode_var.get()
                if mode == "slide":
                    self.ktv_mode_hint_label.config(text="  💡 滑動漸變：使用 \\kf tag，顏色由左至右平滑掃過，視覺效果更流暢")
                else:
                    self.ktv_mode_hint_label.config(text="  💡 逐字變色：使用 \\k tag，每個字唱完後瞬間切換顏色")

            # Update color preview rectangles
            if hasattr(self, 'unplayed_color_preview'):
                self.unplayed_color_preview.config(bg=self.ktv_unplayed_color_var.get())
            if hasattr(self, 'played_color_preview'):
                self.played_color_preview.config(bg=self.ktv_played_color_var.get())
            if hasattr(self, 'border_color_preview'):
                self.border_color_preview.config(bg=self.ktv_border_color_var.get())
            
            # Update lyrics preview canvas
            if hasattr(self, 'ktv_preview_canvas'):
                canvas = self.ktv_preview_canvas
                canvas.delete("all")
                
                # Draw background (lighter dark gray)
                canvas.create_rectangle(0, 0, 760, 80, fill="#333333", outline="")
                
                # Sample lyrics
                lyrics_text = "哥哥爸爸真偉大"
                
                # Get selected font
                selected_font = getattr(self, 'ktv_font_var', tk.StringVar(value="微軟正黑體")).get()
                font = (selected_font, 28, "bold")
                
                # Step 1: Draw full unsung text first
                full_unsung_id = canvas.create_text(
                    380, 40,
                    text=lyrics_text,
                    font=font,
                    fill=self.ktv_unplayed_color_var.get(),
                    anchor=tk.CENTER
                )
                full_bbox = canvas.bbox(full_unsung_id)
                
                if full_bbox:
                    # Step 2: Draw border around entire text
                    canvas.create_rectangle(
                        full_bbox[0]-3, full_bbox[1]-3,
                        full_bbox[2]+3, full_bbox[3]+3,
                        outline=self.ktv_border_color_var.get(),
                        width=3,
                        fill=""
                    )
                    
                    # Step 3: Calculate midpoint (50% of width) for gradient
                    mid_x = full_bbox[0] + (full_bbox[2] - full_bbox[0]) // 2
                    
                    # Step 4: Draw sung text on top, but only show left half (with gradient effect)
                    # First, create a clip rectangle for the left half
                    clip_rect = canvas.create_rectangle(
                        full_bbox[0], full_bbox[1],
                        mid_x, full_bbox[3],
                        outline="",
                        tags="clip"
                    )
                    
                    # Now draw the sung text
                    sung_id = canvas.create_text(
                        380, 40,
                        text=lyrics_text,
                        font=font,
                        fill=self.ktv_played_color_var.get(),
                        anchor=tk.CENTER,
                        tags=("sung",)
                    )
                    
                    # Use a trick to clip the sung text to left half
                    # We'll draw a rectangle over the right half of sung text with background color
                    canvas.create_rectangle(
                        mid_x, full_bbox[1],
                        full_bbox[2], full_bbox[3],
                        fill="#333333",
                        outline=""
                    )
                    
                    # Now redraw the unsung text on the right half
                    # First, calculate the right half text
                    # To make it look like a gradient, we'll draw the unsung text again on the right
                    right_unsung_id = canvas.create_text(
                        380, 40,
                        text=lyrics_text,
                        font=font,
                        fill=self.ktv_unplayed_color_var.get(),
                        anchor=tk.CENTER
                    )
                    
                    # Now clip the right unsung text to show only right half
                    canvas.create_rectangle(
                        full_bbox[0], full_bbox[1],
                        mid_x, full_bbox[3],
                        fill="#333333",
                        outline=""
                    )
                    
                    # Finally, redraw the border on top
                    canvas.create_rectangle(
                        full_bbox[0]-3, full_bbox[1]-3,
                        full_bbox[2]+3, full_bbox[3]+3,
                        outline=self.ktv_border_color_var.get(),
                        width=3,
                        fill=""
                    )
                
        except Exception as e:
            pass
    
    def format_time(self, seconds):
        """格式化時間顯示為 MM:SS"""
        try:
            seconds = float(seconds)
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes:02d}:{secs:02d}"
        except:
            return "00:00"
    
    def get_video_duration(self, video_path):
        """使用 FFmpeg 取得影片長度"""
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            if not ffmpeg_exe.exists():
                return 0
            
            cmd = [
                str(ffmpeg_exe),
                "-i", video_path,
                "-f", "null",
                "-"
            ]
            
            result = subprocess.run(cmd, capture_output=True, creationflags=self.subp_flags)
            output = result.stderr.decode('utf-8', errors='replace')
            
            # 解析 Duration
            import re
            duration_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})", output)
            if duration_match:
                hours = int(duration_match.group(1))
                minutes = int(duration_match.group(2))
                seconds = int(duration_match.group(3))
                centiseconds = int(duration_match.group(4))
                total_seconds = hours * 3600 + minutes * 60 + seconds + centiseconds / 100
                return total_seconds
        except Exception as e:
            pass
        return 0
    
    def parse_lyrics(self, subtitle_path):
        """解析歌詞檔案（支援 SRT 和 JSON）"""
        lyrics = []
        try:
            if not subtitle_path or not os.path.exists(subtitle_path):
                return lyrics
            
            if subtitle_path.lower().endswith('.json'):
                # JSON 格式（KTV 逐字歌詞）
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
                # SRT 格式
                with open(subtitle_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                import re
                srt_pattern = re.compile(
                    r'(\d+)\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\n(.*?)(?=\n\n|\Z)',
                    re.DOTALL
                )
                
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
                        lyrics.append({
                            'start': start_time,
                            'end': end_time,
                            'text': text
                        })
        except Exception as e:
            pass
        return lyrics
    
    def reload_player(self):
        """重新載入影片和歌詞"""
        try:
            # 停止目前播放
            if self.is_playing:
                self.toggle_play()
            
            # 關閉 ffplay 行程
            if self.ffplay_process:
                try:
                    self.ffplay_process.terminate()
                except:
                    pass
                self.ffplay_process = None
            
            # 重置狀態
            self.current_time = 0
            self.lyrics_data = []
            
            # 載入影片
            video_path = self.merge_video_path_var.get().strip()
            subtitle_path = self.merge_subtitle_path_var.get().strip()
            
            # 更新顯示
            self.video_canvas.delete("all")
            self.video_canvas.create_text(320, 140, text="請選擇影片檔案", fill="#666666", font=("Arial", 14))
            self.video_canvas.create_text(320, 170, text="影片將在獨立視窗播放", fill="#888888", font=("Arial", 10))
            
            if video_path and os.path.exists(video_path):
                self.player_video_path = video_path
                # 取得影片長度
                self.video_duration = self.get_video_duration(video_path)
                if self.video_duration > 0:
                    self.timeline_scale.config(to=self.video_duration)
                    # 顯示影片預設畫面（這裡用黑色背景+提示）
                    self.video_canvas.delete("all")
                    self.video_canvas.create_rectangle(0, 0, 640, 320, fill="#000000", outline="")
                    self.video_canvas.create_text(320, 130, text=os.path.basename(video_path), fill="#FFFFFF", font=("Arial", 11))
                    self.video_canvas.create_text(320, 160, text="(點擊播放開始預覽)", fill="#888888", font=("Arial", 9))
                    self.video_canvas.create_text(320, 190, text="影片將在獨立視窗播放", fill="#666666", font=("Arial", 9))
            
            # 載入歌詞
            if subtitle_path and os.path.exists(subtitle_path):
                self.player_subtitle_path = subtitle_path
                self.lyrics_data = self.parse_lyrics(subtitle_path)
                # 除錯：印出載入的歌詞數量
                if hasattr(self, 'log') and self.lyrics_data:
                    self.log(f"✅ 已載入 {len(self.lyrics_data)} 句歌詞")
            else:
                # 如果沒有歌詞，使用測試歌詞讓你看到效果
                self.lyrics_data = [
                    {'start': 0, 'end': 5, 'text': '哥哥爸爸真偉大'},
                    {'start': 5, 'end': 10, 'text': '榮譽都屬於他'},
                    {'start': 10, 'end': 15, 'text': '為國家去打仗'},
                    {'start': 15, 'end': 20, 'text': '我們都愛他'}
                ]
                # 設定預設影片長度為20秒，方便測試
                if self.video_duration <= 0:
                    self.video_duration = 20
                    self.timeline_scale.config(to=self.video_duration)
            
            # 更新時間顯示
            self.update_time_display()
            
            # 更新歌詞顯示
            self.display_lyrics_at_time(0)
            
        except Exception as e:
            pass
    
    def toggle_play(self):
        """切換播放/暫停"""
        if not self.player_video_path or not os.path.exists(self.player_video_path):
            messagebox.showwarning("提示", "請先選擇影片檔案！")
            return
        
        if self.is_playing:
            # 停止播放
            self.is_playing = False
            self.play_btn.config(text="▶ 播放", bg=self.ui_colors['success'])
            self.stop_playback()
        else:
            # 開始播放
            self.is_playing = True
            self.play_btn.config(text="⏸ 暫停", bg=self.ui_colors['warning'])
            self.start_playback()
    
    def start_playback(self):
        """開始播放 - 使用 ffplay"""
        try:
            ffplay_exe = self.bin_dir / "ffplay.exe"
            
            if not ffplay_exe.exists():
                messagebox.showwarning("提示", "找不到 ffplay.exe，請確認 FFmpeg 已正確安裝！\n(我們會繼續顯示歌詞預覽)")
                # 繼續使用模擬播放
                if self.player_after_id:
                    self.root.after_cancel(self.player_after_id)
                self.update_playback()
                return
            
            # 關閉之前的 ffplay 行程
            if self.ffplay_process:
                try:
                    self.ffplay_process.terminate()
                except:
                    pass
            
            # 使用 ffplay 從指定時間開始播放
            cmd = [
                str(ffplay_exe),
                "-ss", str(self.current_time),
                "-autoexit",
                "-window_title", "KTV 影片預覽",
                str(self.player_video_path)
            ]
            
            self.ffplay_process = subprocess.Popen(cmd, creationflags=self.subp_flags)
            
            # 開始更新歌詞進度
            if self.player_after_id:
                self.root.after_cancel(self.player_after_id)
            self.update_playback()
            
        except Exception as e:
            messagebox.showerror("錯誤", f"無法啟動播放器：{str(e)}\n(我們會繼續顯示歌詞預覽)")
            # 繼續使用模擬播放
            if self.player_after_id:
                self.root.after_cancel(self.player_after_id)
            self.update_playback()
    
    def stop_playback(self):
        """停止播放"""
        # 關閉 ffplay 行程
        if self.ffplay_process:
            try:
                self.ffplay_process.terminate()
            except:
                pass
            self.ffplay_process = None
        
        # 停止更新
        if self.player_after_id:
            self.root.after_cancel(self.player_after_id)
            self.player_after_id = None
    
    def update_playback(self):
        """更新播放進度"""
        if not self.is_playing:
            return
        
        # 更新時間
        self.current_time += 0.1
        
        # 檢查是否超過長度
        if self.current_time >= self.video_duration:
            self.current_time = 0
            self.toggle_play()
            return
        
        # 更新時間顯示
        self.update_time_display()
        
        # 更新時間軸（避免觸發 seek）
        self.timeline_scale.set(self.current_time)
        
        # 更新歌詞顯示
        self.display_lyrics_at_time(self.current_time)
        
        # 繼續更新
        self.player_after_id = self.root.after(100, self.update_playback)
    
    def update_time_display(self):
        """更新時間顯示"""
        current_str = self.format_time(self.current_time)
        duration_str = self.format_time(self.video_duration)
        self.time_label_var.set(f"{current_str} / {duration_str}")
    
    def on_timeline_seek(self, event):
        """時間軸放開時的跳轉"""
        new_time = self.timeline_scale.get()
        self.current_time = new_time
        self.update_time_display()
        self.display_lyrics_at_time(self.current_time)
        
        # 如果正在播放，重新啟動 ffplay 到新位置
        if self.is_playing:
            self.restart_player_at_time(new_time)
    
    def on_timeline_drag(self, event):
        """時間軸拖曳中的即時更新"""
        new_time = self.timeline_scale.get()
        self.current_time = new_time
        self.update_time_display()
        self.display_lyrics_at_time(self.current_time)
    
    def restart_player_at_time(self, new_time):
        """在指定時間重新啟動播放器"""
        try:
            ffplay_exe = self.bin_dir / "ffplay.exe"
            
            if not ffplay_exe.exists():
                return
            
            # 關閉之前的 ffplay 行程
            if self.ffplay_process:
                try:
                    self.ffplay_process.terminate()
                except:
                    pass
            
            # 使用 ffplay 從新時間開始播放
            cmd = [
                str(ffplay_exe),
                "-ss", str(new_time),
                "-autoexit",
                "-window_title", "KTV 影片預覽",
                str(self.player_video_path)
            ]
            
            self.ffplay_process = subprocess.Popen(cmd, creationflags=self.subp_flags)
            
        except Exception as e:
            pass
    
    def display_lyrics_at_time(self, current_time):
        """根據時間顯示對應歌詞"""
        try:
            canvas = self.lyrics_display_canvas
            canvas.delete("all")
            
            # 背景
            canvas.create_rectangle(0, 0, 640, 70, fill="#333333", outline="")
            
            # 找當前時間的歌詞
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
                # 找最接近的歌詞
                for lyric in self.lyrics_data:
                    if float(lyric.get('start', 0)) <= current_time:
                        current_lyric = lyric
            
            # 顯示歌詞
            if current_lyric:
                text = current_lyric.get('text', '')
                
                # 取得設定的字體和顏色
                selected_font = "微軟正黑體"
                unplayed_color = "#FFFFFF"
                played_color = "#FFFF00"
                border_color = "#000000"
                if hasattr(self, 'ktv_font_var'):
                    selected_font = self.ktv_font_var.get()
                if hasattr(self, 'ktv_unplayed_color_var'):
                    unplayed_color = self.ktv_unplayed_color_var.get()
                if hasattr(self, 'ktv_played_color_var'):
                    played_color = self.ktv_played_color_var.get()
                if hasattr(self, 'ktv_border_color_var'):
                    border_color = self.ktv_border_color_var.get()
                
                font = (selected_font, 24, "bold")
                
                # 計算漸變進度
                start_time = float(current_lyric.get('start', 0))
                end_time = float(current_lyric.get('end', 0))
                duration = end_time - start_time if end_time > start_time else 1
                progress = min(max((current_time - start_time) / duration, 0), 1)
                
                # 最簡單的顯示方式：預設顯示未唱顏色
                # 如果進度超過 0.5，我們就用已唱顏色
                # 完全迴避比較運算符，直接用布林值
                display_color = unplayed_color
                
                # 計算進度平方，大於 0.25 (0.5 的平方) 就是超過一半
                p_squared = progress * progress
                threshold = 0.25
                is_over = False
                
                # 用加法和乘法來判斷
                # 當 p_squared - threshold &gt;= 0 時，is_over 為 True
                # 我們用一個 trick 來實現
                check_val = p_squared - threshold
                # 取絕對值的符號來判斷
                abs_val = abs(check_val)
                if abs_val == check_val:
                    is_over = True
                
                if is_over:
                    display_color = played_color
                
                canvas.create_text(
                    320, 35,
                    text=text,
                    font=font,
                    fill=display_color,
                    anchor=tk.CENTER
                )
            else:
                # 沒有歌詞時顯示提示
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
            # filter 說明：
            # [0:a] = 伴奏 stereo → mono → [inst_mono]
            # [inst_mono] asplit → [inst_a]（給 amix 用）、[inst_b]（給 amerge 用）
            #   ↑ 必須 asplit，因為 inst_mono 同時被 amix 和 amerge 使用，
            #     FFmpeg 不允許同一個輸出 fan-out 到兩個 filter
            # [1:a] = 人聲 stereo → mono → [voc_mono]
            # [inst_a][voc_mono] amix normalize=0 → [mixed]（右聲道：伴奏+人聲）
            # [inst_b][mixed] amerge → [lr]（左=inst_b 純伴奏, 右=mixed 伴奏+人聲）
            cmd = [
                str(ffmpeg_exe), "-y",
                "-i", str(instrumental_file),
                "-i", str(vocal_file),
                "-filter_complex",
                # 步驟1: 伴奏 stereo → mono
                # 步驟2: 人聲 stereo → mono
                # 步驟3: amix(伴奏mono + 人聲mono) → mixed（右聲道內容：伴奏+人聲）
                # 步驟4: amerge 把 inst_mono(左) 和 mixed(右) 合成真正的立體聲
                # 步驟5: pan 確保 L=inst_mono.c0, R=mixed.c0
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
        self.root.after(0, lambda: self._safe_update_status(text, color))

    def _safe_update_status(self, text, color):
        self.status_var.set(f"狀態: {text}")
        self.status_label.config(fg=color)

    def _get_target_ai_dir(self, install_mode="auto"):
        """根據安裝模式決定主要 AI 套件目錄。"""
        if install_mode == "cpu":
            return self.lib_dir
        if install_mode == "gpu":
            return self.gpu_lib_dir
        if install_mode == "both":
            return self.gpu_lib_dir if self._is_nvidia_gpu_present() else self.lib_dir
        return self.gpu_lib_dir if self._is_nvidia_gpu_present() else self.lib_dir

    def _get_runtime_ai_dir(self, device=None):
        target = device or self.device_var.get()
        if target == "gpu" or target == "cuda":
            return self.gpu_lib_dir
        elif target == "directml":
            return self.directml_lib_dir
        else:
            return self.lib_dir

    def _has_onnxruntime_package(self, target_dir):
        """檢查指定 AI 套件目錄是否已有 onnxruntime 核心包（非僅 metadata）。"""
        try:
            # 必須檢查實際的套件資料夾與 __init__.py 是否存在，而非僅檢查 dist-info
            # 這能有效過濾掉安裝不完整或僅殘留 metadata 的情況
            pkg_dir = target_dir / "onnxruntime"
            return pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
        except Exception:
            return False

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
        lib_dir_posix = str(lib_dir).replace("\\", "/")
        common_lib_dir_posix = str(self.common_lib_dir).replace("\\", "/")
        app_bin_dir_posix = str(self.bin_dir).replace("\\", "/")
        app_py_dir_posix = str(self.py_dir).replace("\\", "/")
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
            # 有時候錯誤會出現在 stderr，例如 Python/ORT 初始化失敗；另外也可能是 VC++ runtime 缺失導致 CPU 模式也無法執行
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
                        fallback_msg = f"{mode_name} 核心安裝失敗！\n\n是否要切換至 CPU 模式繼續？"
                        fallback = messagebox.askyesno(f"{mode_name} 安裝失敗", fallback_msg)
                        if fallback:
                            self.log(f"⚠️ 已切換至獨立 CPU 核心繼續執行。")
                            self.root.after(0, lambda: self.device_var.set("cpu"))
                            self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
                            return self._ensure_runtime_stack_ready("cpu")
                        else:
                            return False, device, runtime_lib_dir
                else:
                    self.log(f"❌ {mode_name} AI 核心安裝失敗。")
                    fallback_msg = f"{mode_name} 核心安裝失敗！\n\n是否要切換至 CPU 模式繼續？"
                    fallback = messagebox.askyesno(f"{mode_name} 安裝失敗", fallback_msg)
                    if fallback:
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

        # 先確認硬體層面是否有啟用的 NVIDIA GPU
        # 若無（例如筆電拔電源後切換至內顯），直接告知使用者，不進行後續修復流程
        if not self._is_nvidia_gpu_present():
            self.log("ℹ️ 系統目前未偵測到啟用的 NVIDIA 顯示卡。")
            self.log("💡 筆電使用者：請確認已插上電源，且系統已切換至獨立顯示卡（NVIDIA GPU）。")
            self.log("💡 若您的電腦沒有 NVIDIA 顯示卡，請使用 CPU 模式，這是正常狀態，無需修復。")
            messagebox.showinfo(
                "未偵測到 NVIDIA GPU",
                "目前系統未偵測到啟用的 NVIDIA 顯示卡。\n\n"
                "• 若您是筆電使用者，請插上電源後再試。\n"
                "• 若電腦沒有 NVIDIA 顯示卡，請直接使用 CPU 模式即可，不需要下載 GPU 組件。"
            )
            return

        if not self.local_python.exists():
            self.log("[ERROR] 內建 Python 核心尚未安裝，無法進行檢測。")
            if messagebox.askyesno("初始化環境", "偵測到環境尚未初始化，是否要現在開始下載並配置基礎環境？"):
                self.check_components(prompt=True)
            return

        gpu_lib_dir = self.gpu_lib_dir
        env = self._build_python_env(gpu_lib_dir, include_gpu_runtime=True)
        gpu_lib_dir_posix = str(gpu_lib_dir).replace("\\", "/")
        common_lib_dir_posix = str(self.common_lib_dir).replace("\\", "/")

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
            
            # 檢測是否有安裝必要的 Python 套件
            libs_installed = "onnxruntime" in stdout_str and "torch" in stdout_str
            
            # 檢測 CUDA 是否可用 (必須 ONNX 和 PyTorch 兩者都就緒才算完全 ready)
            # 針對 RTX 50 系列 (sm_120)，如果 stderr 含有不相容警告，也視為未就緒
            is_sm120_incompatible = "sm_120 is not compatible" in res.stderr
            
            cuda_ready = ("ONNX CUDA 提供者已就緒" in stdout_str) and \
                         ("PyTorch CUDA 是否可用: True" in stdout_str) and \
                         (not is_sm120_incompatible)
            
            if not cuda_ready:
                if not libs_installed:
                    self.log("\n💡 偵測到核心組件缺失 (Torch 或 ONNX)。")
                    msg = ("偵測到程式尚未安裝「AI 加速組件」或組件損壞。\n\n"
                           "程式需要下載約 1.7GB 的加速庫才能發揮 GPU 效能。\n\n"
                           "是否立即執行「一鍵全自動修復」？")
                    if messagebox.askyesno("一鍵修復", msg):
                        self._start_async_setup()
                    return
                else:
                    # 如果套件已安裝但無法使用 CUDA
                    self.log("\n💡 偵測到 CUDA 加速環境配置不完全或不相容。")
                    if is_sm120_incompatible:
                        msg = ("偵測到您的 GPU (RTX 50 系列) 與當前 PyTorch 版本不相容。\n\n"
                               "程式需要重新下載支援 Blackwell 架構的運算核心 (CUDA 12.6+)。\n\n"
                               "是否立即執行「一鍵修復」？")
                    elif "PyTorch CUDA 是否可用: True" in stdout_str:
                        msg = ("您的 PyTorch 運作正常，但 ONNX 引擎尚未完全對接。\n\n"
                               "是否讓程式自動嘗試修復 DLL 補丁？")
                    else:
                        if "運算失敗" in stdout_str:
                            msg = ("偵測到您的 GPU 與當前 AI 組件版本不相容。\n\n"
                                   "這通常是因為您的顯示卡太新，需要更新版本的運算核心。\n\n"
                                   "是否立即執行「一鍵修復」以下載最新的相容版本？")
                        else:
                            msg = ("偵測到您的系統 PyTorch 無法使用 GPU (當前可能是 CPU 版本)。\n\n"
                                   "是否立即執行「一鍵修復」以下載正確的 GPU 版本？")
                    
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
        # 只有必要且缺少的組件才預設打勾
        pre_checked = is_essential and is_missing
        var = tk.BooleanVar(value=pre_checked)
        self.repair_component_vars[comp_id] = var
        
        row_frame = tk.Frame(parent, bg=self.ui_colors['bg'])
        row_frame.pack(fill=tk.X, pady=2)
        
        left_frame = tk.Frame(row_frame, bg=self.ui_colors['bg'])
        left_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        cb = tk.Checkbutton(left_frame, text=comp_name, variable=var, 
                            font=("Arial", 11, "bold"), 
                            bg=self.ui_colors['bg'],
                            selectcolor=self.ui_colors['bg'])
        cb.pack(anchor="w")
        
        desc_label = tk.Label(left_frame, text=comp_desc, 
                              font=("Arial", 10), 
                              fg="#444",
                              bg=self.ui_colors['bg'])
        desc_label.pack(anchor="w", padx=(22, 0))
        
        # 狀態和版本
        right_frame = tk.Frame(row_frame, bg=self.ui_colors['bg'])
        right_frame.pack(side=tk.RIGHT)
        
        # 顯示版本（如果有的話）
        ver = component_versions[comp_id]
        if ver:
            ver_label = tk.Label(right_frame, text=f"v{ver}", 
                                font=("Arial", 10), 
                                fg="#555",
                                bg=self.ui_colors['bg'])
            ver_label.pack(side=tk.RIGHT, padx=3)
        
        if is_missing:
            status_label = tk.Label(right_frame, text="⚠️ 缺少", fg="red", font=("Arial", 10, "bold"), bg=self.ui_colors['bg'])
        else:
            status_label = tk.Label(right_frame, text="✅ 正常", fg="green", font=("Arial", 10, "bold"), bg=self.ui_colors['bg'])
        status_label.pack(side=tk.RIGHT)

    def check_components(self, prompt=True, show_list=True):
        """更新環境修復標籤頁的元件列表"""
        if self.is_processing: return
        
        # 如果是啟動檢查，不顯示列表
        if show_list:
            # 先清空之前的內容
            for widget in self.repair_components_frame.winfo_children():
                widget.destroy()
            
            self.repair_component_vars = {}
        
        # 偵測所有組件狀態
        has_gpu = self._is_nvidia_gpu_present()

        # --- 自動辨識建議的運算核心 ---
        # 優先順序：NVIDIA GPU → DirectML（AMD/Intel）→ CPU
        # 已安裝的核心不計入「建議安裝」；只有真正缺少時才標記為 essential=True
        has_nvidia_gpu_stack   = (self.gpu_lib_dir / "torch").exists() and self._has_onnxruntime_package(self.gpu_lib_dir)
        has_directml_stack     = self._has_onnxruntime_package(self.directml_lib_dir)
        has_cpu_stack          = (self.lib_dir / "torch").exists() and self._has_onnxruntime_package(self.lib_dir)

        # 偵測是否有 AMD / Intel 顯示卡（DirectML 的目標硬體）
        has_amd_or_intel_gpu = False
        try:
            import subprocess as _sp
            _wmic = _sp.run(
                ["wmic", "path", "win32_VideoController", "get", "Name"],
                capture_output=True, text=True, timeout=10,
                creationflags=self.subp_flags, encoding="utf-8", errors="replace"
            )
            if _wmic.returncode == 0:
                _out = _wmic.stdout.upper()
                if "AMD" in _out or "RADEON" in _out or "INTEL" in _out:
                    has_amd_or_intel_gpu = True
            else:
                raise RuntimeError("wmic failed")
        except Exception:
            try:
                _ps = _sp.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-PnpDevice -Class Display | Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=self.subp_flags, encoding="utf-8", errors="replace"
                )
                _out2 = _ps.stdout.upper()
                if "AMD" in _out2 or "RADEON" in _out2 or "INTEL" in _out2:
                    has_amd_or_intel_gpu = True
            except Exception:
                pass

        # 決定哪個 AI 核心應被標記為「必要」（essential=True → 預設打勾）
        # 若對應核心已安裝則不標必要（避免重複打勾提示更新）
        ai_cpu_essential       = False
        ai_gpu_essential       = False
        ai_directml_essential  = False

        if has_gpu and not has_nvidia_gpu_stack:
            # 有 NVIDIA GPU 且尚未安裝 GPU 核心 → 建議安裝 GPU 核心
            ai_gpu_essential = True
        elif not has_gpu and has_amd_or_intel_gpu and not has_directml_stack:
            # 確定沒有 NVIDIA GPU，但有 AMD/Intel GPU 且尚未安裝 DirectML 核心 → 建議安裝 DirectML 核心
            # 注意：有 NVIDIA + Intel 雙顯的機器，NVIDIA 優先，不需要 DirectML
            ai_directml_essential = True
        elif not has_gpu and not has_amd_or_intel_gpu and not has_cpu_stack:
            # 純 CPU 機器（或無法辨識顯卡），且 CPU 核心尚未安裝 → 建議安裝 CPU 核心
            ai_cpu_essential = True

        # ai_common 是所有 AI 核心的前提，與推薦核心一同標為必要
        ai_common_essential = ai_cpu_essential or ai_gpu_essential or ai_directml_essential

        # 定義所有可修復的組件 (id, name, description, is_missing, is_essential)
        all_components = [
            ("python", "內建 Python 核心", "程式運行的基礎環境，所有功能都需要它", not self.local_python.exists(), True),
            ("ffmpeg", "音訊引擎 FFmpeg", "處理音訊和影片的轉檔、分離等核心功能", not (self.bin_dir / "ffmpeg.exe").exists(), True),
            ("ytdlp", "YouTube 下載器 yt-dlp", "用於從 YouTube 下載影片和音訊", not self._is_ytdlp_installed(), True),
            ("ai_common", "AI 共用函式庫 (audio-separator)", "AI 人聲分離的核心函式庫，負責分離人聲和伴奏", not (self.common_lib_dir / "audio_separator").exists(), ai_common_essential),
            ("ai_cpu", "AI CPU 運算核心 (PyTorch + ONNX Runtime)", "使用 CPU 進行 AI 運算，相容性最高但速度較慢", not has_cpu_stack, ai_cpu_essential),
            ("whisper", "Whisper AI 歌詞識別模型", "用於自動識別歌詞並產生 SRT 字幕檔案", not (self.whisper_models_dir.exists() and any(self.whisper_models_dir.iterdir())), False),
            ("zhconv", "中文簡繁轉換庫 zhconv", "用於歌詞的簡體中文和繁體中文互相轉換", not self._is_zhconv_installed(), False),
        ]
        
        if has_gpu:
            all_components.extend([
                ("ai_gpu", "AI GPU 運算核心 (PyTorch + ONNX Runtime)", "使用 NVIDIA GPU 進行 AI 運算，速度最快", not has_nvidia_gpu_stack, ai_gpu_essential),
                ("ai_directml", "AI DirectML 運算核心", "使用 DirectML 進行 AI 運算，適用 AMD/Intel 顯卡", not has_directml_stack, ai_directml_essential),
            ])
        elif has_amd_or_intel_gpu:
            # 有 AMD/Intel GPU 但沒有 NVIDIA → 只顯示 DirectML 選項
            all_components.append(
                ("ai_directml", "AI DirectML 運算核心", "使用 DirectML 進行 AI 運算，適用 AMD/Intel 顯卡", not has_directml_stack, ai_directml_essential)
            )
        
        # 啟動時自動檢查 - 只有當 prompt=True 或 show_list=False 時才執行
        if not show_list:
            startup_ok = True
            if not self.local_python.exists() or not (self.bin_dir / "ffmpeg.exe").exists():
                startup_ok = False
            else:
                # 檢查是否有至少一組 AI 核心可用
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
        
        # 如果不需要顯示列表，直接返回（用於啟動檢查）
        if not show_list:
            return

        # --- 立即顯示 loading 畫面，不阻塞 UI ---
        loading_label = tk.Label(
            self.repair_components_frame,
            text="🔍 正在掃描元件狀態...",
            font=("Arial", 11), fg=self.ui_colors['primary'],
            bg=self.ui_colors['bg']
        )
        loading_label.pack(pady=30)
        self.repair_components_frame.update_idletasks()

        # 設定按鈕事件（先定義，讓背景執行緒完成後可以繫結）
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
            # 取版本（純讀檔，已不跑 subprocess，速度快）
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
                elif comp_id == "zhconv":
                    version = self._get_package_version(self.common_lib_dir, "zhconv")
                elif comp_id == "ai_directml":
                    version = self._get_package_version(self.directml_lib_dir, "onnxruntime-directml")
                component_versions[comp_id] = version

            # 切回主執行緒繪製 UI
            self.root.after(0, lambda: _render_component_list(all_components, component_versions))

        def _render_component_list(all_components, component_versions):
            """主執行緒：清除 loading，繪製元件列表"""
            # 清除 loading
            for widget in self.repair_components_frame.winfo_children():
                widget.destroy()
            self.repair_component_vars = {}

            has_versions = any(v is not None for v in component_versions.values())
            if has_versions:
                info_label = tk.Label(self.repair_components_frame,
                                    text="💡 部分元件已顯示目前版本，您可以勾選以重新安裝更新",
                                    font=("Arial", 11), fg="#1E88E5",
                                    bg=self.ui_colors['bg'])
                info_label.pack(pady=2, anchor=tk.W)

            # 顯示自動辨識的運算核心建議
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
            tk.Label(self.repair_components_frame,
                     text=detect_text,
                     font=("Arial", 11, "bold"), fg=detect_color,
                     bg=self.ui_colors['bg']).pack(pady=(0, 6), anchor=tk.W)

            columns_frame = tk.Frame(self.repair_components_frame, bg=self.ui_colors['bg'])
            columns_frame.pack(fill=tk.BOTH, expand=True)

            left_col = tk.Frame(columns_frame, bg=self.ui_colors['bg'])
            left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

            right_col = tk.Frame(columns_frame, bg=self.ui_colors['bg'])
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
                elif comp_id in ["whisper"]:
                    ai_model_components.append(comp)

            tk.Label(left_col, text="【 核心元件 】", font=("Arial", 12, "bold"),
                     fg=self.ui_colors['primary'], bg=self.ui_colors['bg']).pack(anchor=tk.W, pady=(5, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in core_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, left_col)

            tk.Label(left_col, text="【 下載工具 】", font=("Arial", 12, "bold"),
                     fg=self.ui_colors['primary'], bg=self.ui_colors['bg']).pack(anchor=tk.W, pady=(8, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in download_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, left_col)

            tk.Label(right_col, text="【 AI 運算核心 】", font=("Arial", 12, "bold"),
                     fg=self.ui_colors['primary'], bg=self.ui_colors['bg']).pack(anchor=tk.W, pady=(5, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in ai_core_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, right_col)

            tk.Label(right_col, text="【 AI 模型 】", font=("Arial", 12, "bold"),
                     fg=self.ui_colors['primary'], bg=self.ui_colors['bg']).pack(anchor=tk.W, pady=(8, 3))
            for comp_id, comp_name, comp_desc, is_missing, is_essential in ai_model_components:
                self._render_component_row(comp_id, comp_name, comp_desc, is_missing, is_essential, component_versions, right_col)

            # 重新繫結按鈕（vars 已重建）
            self.repair_select_all_btn.config(command=on_select_all)
            self.repair_select_none_btn.config(command=on_select_none)
            self.repair_start_btn.config(command=on_repair_click)

        threading.Thread(target=_build_component_list_bg, daemon=True).start()

    def _check_component_update(self, comp_id, comp_name, parent_dialog):
        """檢查指定元件是否有更新"""
        self.log(f"🔍 正在檢查 {comp_name} 的更新...")
        
        # 根據元件類型定義更新檢查邏輯
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
        
        # 模擬檢查（未來可連結真實API）
        # 目前我們提示使用者可以直接選擇重新安裝
        has_update_available = True  # 模擬有更新
        current_ver = update_info.get(comp_id, {}).get("current", "未知版本")
        
        msg = f"元件: {comp_name}\n"
        if current_ver:
            msg += f"目前版本: {current_ver}\n"
        msg += "\n是否要重新安裝/更新此元件？"
        
        if messagebox.askyesno(f"檢查更新 - {comp_name}", msg):
            # 關閉原對話框，並觸發修復
            parent_dialog.destroy()
            # 只選擇此元件來修復
            self.is_processing = True
            self.update_status(f"正在更新 {comp_name}...", "orange")
            threading.Thread(target=self._async_repair_components, args=([comp_id],), daemon=True).start()

    def _get_ytdlp_version(self):
        """取得 yt-dlp 版本（優先讀 dist-info，避免啟動 subprocess）"""
        try:
            # 先從 dist-info 讀取（快速，不跑 subprocess）
            ver = self._get_package_version(self.ytdlp_dir, "yt-dlp")
            if ver:
                return ver
            # fallback: 讀 __version__.py 或 version 模組
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
                    # fallback: 從資料夾名稱解析
                    parts = dist_info.stem.split("-")
                    if len(parts) >= 2:
                        return parts[1]
        except Exception:
            pass
        return None

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
        pip_cmd = [
            str(self.local_python), "-m", "pip", "install",
            "--target", str(self.common_lib_dir),
            "--upgrade",
            "--retries", "10",
            "--timeout", "100",
            "--no-warn-script-location",
            "audio-separator"
        ]
        
        process = subprocess.Popen(
            pip_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, creationflags=self.subp_flags, encoding='utf-8',
            errors='replace'
        )
        
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                clean_line = line.strip()
                if any(x in clean_line for x in ["Downloading", "Installing", "Collecting", "ERROR", "Exception", "Traceback", "Requirement already satisfied"]):
                    if "satisfied" in clean_line and len(clean_line) > 100:
                        clean_line = clean_line[:100] + "..."
                    self.log(f"  > {clean_line}")
        
        process.wait()  # 確保 returncode 已更新
        if process.returncode == 0:
            self.log("✅ AI 共用函式庫 (audio-separator) 安裝完成。")
            return True
        else:
            self.log("❌ AI 共用函式庫 (audio-separator) 安裝失敗。")
            return False

    def _install_component_ai_cpu(self):
        """安裝 AI CPU 運算核心"""
        has_gpu = self._is_nvidia_gpu_present()
        is_rtx50 = self._is_rtx_50_series()
        return self._install_ai_stack(self.lib_dir, "cpu", is_rtx50, clean=False)

    def _install_component_ai_gpu(self):
        """安裝 AI GPU 運算核心"""
        has_gpu = self._is_nvidia_gpu_present()
        if not has_gpu:
            self.log("❌ 未偵測到 NVIDIA GPU，跳過 GPU 核心安裝。")
            return False
        
        is_rtx50 = self._is_rtx_50_series()
        return self._install_ai_stack(self.gpu_lib_dir, "gpu", is_rtx50, clean=False)

    def _install_component_whisper(self):
        """安裝 Whisper AI 歌詞識別模型"""
        self.log("📥 正在安裝 Whisper 相關套件...")
        try:
            # 1. 安裝 openai-whisper 和 ffmpeg-python 到 common_lib_dir
            args = [
                str(self.local_python), "-m", "pip", "install",
                "--target", str(self.common_lib_dir),
                "openai-whisper", "ffmpeg-python"
            ]
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                encoding='utf-8', errors='replace',
                creationflags=self.subp_flags
            )
            self._current_process = process
            
            for line in process.stdout:
                clean_line = line.strip()
                if clean_line:
                    self.log(f"  > {clean_line}")
            
            process.wait()  # 確保 returncode 已更新
            if process.returncode != 0:
                self.log("❌ Whisper 套件安裝失敗。")
                return False
            
            self.log("✅ Whisper 套件安裝完成。")
            
            # 2. 建立模型目錄
            if not self.whisper_models_dir.exists():
                self.whisper_models_dir.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 模型目錄已建立: {self.whisper_models_dir}")
            
            # 3. 下載 small 模型（最常用）
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

    def _is_zhconv_installed(self):
        """檢查 zhconv 是否已安裝"""
        try:
            if str(self.common_lib_dir) not in sys.path:
                sys.path.insert(0, str(self.common_lib_dir))
            import zhconv
            return True
        except ImportError:
            return False

    def _install_component_zhconv(self):
        """安裝中文簡繁轉換庫 zhconv"""
        try:
            self.log("🚀 正在安裝中文簡繁轉換庫 zhconv...")
            
            result = subprocess.run(
                [str(self.local_python), '-m', 'pip', 'install', 
                 '--target', str(self.common_lib_dir), 'zhconv'],
                capture_output=True, text=True,
                creationflags=self.subp_flags
            )
            
            if result.returncode == 0:
                self.log("✅ 中文簡繁轉換庫 zhconv 已安裝完成。")
                return True
            else:
                self.log(f"❌ zhconv 安裝失敗: {result.stderr[:200]}")
                return False
        except Exception as e:
            self.log(f"❌ 安裝 zhconv 時發生錯誤: {str(e)}")
            return False

    def _install_component_ai_directml(self):
        """安裝 AI DirectML 運算核心"""
        try:
            self.log("🚀 正在安裝 AI DirectML 運算核心...")
            
            if not self.directml_lib_dir.exists():
                self.directml_lib_dir.mkdir(parents=True, exist_ok=True)
            
            result = subprocess.run(
                [str(self.local_python), '-m', 'pip', 'install', 
                 '--target', str(self.directml_lib_dir), 'onnxruntime-directml'],
                capture_output=True, text=True,
                creationflags=self.subp_flags
            )
            
            if result.returncode == 0:
                self.log("✅ AI DirectML 運算核心已安裝完成。")
                return True
            else:
                self.log(f"❌ DirectML 安裝失敗: {result.stderr[:200]}")
                return False
        except Exception as e:
            self.log(f"❌ 安裝 DirectML 時發生錯誤: {str(e)}")
            return False

    def _async_repair_components(self, selected_components):
        """修復選定的組件"""
        self.log(f"--- 開始修復選定的組件: {', '.join(selected_components)} ---")
        self.update_status("正在準備修復環境...", "orange")
        
        # 1. 建立所有需要的目錄
        setup_dirs = [
            ("音訊引擎", self.bin_dir),
            ("Python環境", self.py_dir),
            ("yt-dlp目錄", self.ytdlp_dir),
            ("共用AI函式庫", self.common_lib_dir),
            ("CPU AI函式庫", self.lib_dir),
            ("GPU AI函式庫", self.gpu_lib_dir),
            ("DirectML AI函式庫", self.directml_lib_dir),
            ("模型目錄", self.models_dir)
        ]

        for i, (name, d) in enumerate(setup_dirs):
            try:
                if not d.parent.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                d.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 目錄已就緒: {d.name}")
                self.update_status(f"正在準備目錄... ({i+1}/{len(setup_dirs)})", "orange")
            except Exception as e:
                self.log(f"❌ 無法建立 {name} 目錄: {d}")
                self.log(f"   錯誤訊息: {str(e)}")
                self.is_processing = False
                self.update_status("修復失敗", "red")
                return
        
        success = True
        total = len(selected_components)
        
        # 2. 逐個安裝選定的組件
        component_handlers = {
            "python": self._install_component_python,
            "ffmpeg": self._install_component_ffmpeg,
            "ytdlp": self._install_component_ytdlp,
            "ai_common": self._install_component_ai_common,
            "ai_cpu": self._install_component_ai_cpu,
            "ai_gpu": self._install_component_ai_gpu,
            "ai_directml": self._install_component_ai_directml,
            "whisper": self._install_component_whisper,
            "zhconv": self._install_component_zhconv,
        }
        
        component_names = {
            "python": "內建 Python 核心",
            "ffmpeg": "音訊引擎 FFmpeg",
            "ytdlp": "YouTube 下載器 yt-dlp",
            "ai_common": "AI 共用函式庫 (audio-separator)",
            "ai_cpu": "AI CPU 運算核心 (PyTorch + ONNX Runtime)",
            "ai_gpu": "AI GPU 運算核心 (PyTorch + ONNX Runtime)",
            "ai_directml": "AI DirectML 運算核心",
            "whisper": "Whisper AI 歌詞識別模型",
            "zhconv": "中文簡繁轉換庫 zhconv",
        }
        
        for idx, comp_id in enumerate(selected_components):
            if comp_id in component_handlers:
                comp_name = component_names.get(comp_id, comp_id)
                pct = int(idx / total * 90)
                step_txt = f"步驟 {idx+1}/{total}：{comp_name}"
                self.update_status(f"正在安裝 {comp_name}... ({idx+1}/{total})", "orange")
                self.update_progress(pct, step_text=step_txt)
                self.log(f"\n--- 正在安裝組件: {comp_id} ---")
                if not component_handlers[comp_id]():
                    success = False
        
        # 3. 如果有安裝 AI 組件，重新檢測環境
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
        
        # 修復完成後更新元件列表
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

        # 1. 建立所有需要的目錄
        setup_dirs = [
            ("音訊引擎", self.bin_dir),
            ("Python環境", self.py_dir),
            ("yt-dlp目錄", self.ytdlp_dir),
            ("共用AI函式庫", self.common_lib_dir),
            ("CPU AI函式庫", self.lib_dir),
            ("GPU AI函式庫", self.gpu_lib_dir),
            ("DirectML AI函式庫", self.directml_lib_dir),
            ("模型目錄", self.models_dir)
        ]

        for name, d in setup_dirs:
            try:
                if not d.parent.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                d.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 目錄已就緒: {d.name}")
            except Exception as e:
                self.log(f"❌ 無法建立 {name} 目錄: {d}")
                self.log(f"   錯誤訊息: {str(e)}")
                self.is_processing = False
                return

        # 2. 安裝 Python（約 10MB）
        self.update_progress(10, step_text="步驟 2/5：安裝內建 Python 核心 (約 10MB)...")
        if not self.local_python.exists():
            self.log("🚀 正在下載內建 Python 核心 (約 10MB)...")
            if not self.download_portable_python():
                self.log("❌ Python 下載失敗，請檢查網路連線。")
                self.is_processing = False
                return
        else:
            self.fix_python_pth()
        
        if self.local_python.exists():
            self.log("✅ 內建 Python 核心已就緒。")
        else:
            self.log("❌ Python 部署異常：路徑存在但找不到執行檔。")
            self.is_processing = False
            return

        # 3. 安裝 yt‑dlp（最小，約 10‑20MB）
        self.update_progress(25, step_text="步驟 3/5：安裝 YouTube 下載器 yt-dlp (約 10-20MB)...")
        if not self._is_ytdlp_installed():
            self.log("🚀 正在安裝 YouTube 下載器 yt-dlp（約 10‑20MB）...")
            self._install_ytdlp_silent()
        else:
            self.log("✅ YouTube 下載器 yt-dlp 已就緒。")

        # 4. 安裝 FFmpeg（最大，約 100MB+，放在最後！）
        self.update_progress(40, step_text="步驟 4/5：安裝音訊引擎 FFmpeg (約 100MB+)...")
        if not (self.bin_dir / "ffmpeg.exe").is_file():
            self.log("🚀 正在下載音訊引擎 FFmpeg (約 100MB+)...")
            if not self.download_ffmpeg():
                self.log("❌ FFmpeg 下載失敗。")
        else:
            self.log("✅ 音訊引擎 FFmpeg 已就緒。")

        # 5. 檢查與安裝共用套件和 AI 運算元件
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
            
            # 清理與規範化 lines
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
            
            # 動態偵測 Python zip 名稱（相容 3.10/3.11/3.12 等版本）
            py_zip = next((f.name for f in self.py_dir.glob("python*.zip")), "python310.zip")
            
            # 僅保留 Python 自身必要路徑，CPU/GPU 套件路徑改由執行時動態注入，
            # 避免 CPU / GPU 兩套 onnxruntime 互相污染。
            required = [
                py_zip, 
                ".", 
                "Lib/site-packages", 
                "import site"
            ]
            needs_update = removed_legacy
            
            for item in required:
                if item not in lines:
                    # 檢查是否被註解了
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
        # 多個備用載點，依序嘗試
        py_urls = [
            # 主要：Python 官方 FTP
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            # 備用 1：GitHub mirror (python-build-standalone)
            "https://github.com/indygreg/python-build-standalone/releases/download/20230826/cpython-3.10.13+20230826-x86_64-pc-windows-msvc-shared-pgo-full.tar.zst",
            # 備用 2：官方 FTP 另一版本
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            # 備用 3：官方 FTP 3.11
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
        ]
        # 只使用標準 embed zip 格式的載點（tar.zst 格式不同，移除）
        py_urls = [
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip",
        ]

        # ── 步驟 1：確保目標目錄存在並可寫入 ──────────────────────────────────
        # urlretrieve 本身不會建立父目錄，若目錄不存在會噴 [Errno 2]；
        # 此外 Windows 長路徑限制或權限問題也會讓 mkdir 靜默失敗，
        # 所以這裡分三步：建立 → 驗證存在 → 實際寫入測試。
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

        # 實際寫入測試，確認磁碟空間與權限正常
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
        # 清除上次可能殘留的不完整檔案
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass

        ssl_context = ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
        opener.addheaders = [("User-agent", "Mozilla/5.0")]
        urllib.request.install_opener(opener)

        for attempt, url in enumerate(py_urls, 1):
            self.log(f"🚀 正在下載 Python 核心 (來源 {attempt}/{len(py_urls)})...")
            try:
                self._last_log_percent = -1
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)

                # 驗證 zip 完整性
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
                
                # 安裝 pip
                self.log("📦 正在安裝 pip 套件管理工具...")
                get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
                get_pip_path = self.py_dir / "get-pip.py"
                try:
                    urllib.request.urlretrieve(get_pip_url, str(get_pip_path))
                    subprocess.run(
                        [str(self.local_python), str(get_pip_path)],
                        capture_output=True, text=True, creationflags=self.subp_flags,
                        timeout=180
                    )
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

    def download_ffmpeg(self):
        # 主要來源：BtbN GitHub Release；備用來源：gyan.dev essentials
        primary_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
        fallback_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = self.bin_dir / "ffmpeg.zip"

        # 確保目錄存在且可寫入
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
                # 建立不驗證 SSL 的 context，解決 CERTIFICATE_VERIFY_FAILED 問題
                ssl_context = ssl._create_unverified_context()
                
                self._last_log_percent = -1
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
                opener.addheaders = [('User-agent', 'Mozilla/5.0')]
                urllib.request.install_opener(opener)
                
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)
                self.log("📦 正在提取 FFmpeg 引擎與共享函式庫 (DLLs)...")
                with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                    for file in zip_ref.namelist():
                        # 統一將路徑改為正斜線進行判斷
                        normalized_file = file.replace('\\', '/')
                        # 提取 bin 目錄下的所有 exe 和 dll
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
        patterns = [
            "torch*", "torchvision*", "torchaudio*",
            "onnxruntime*", "onnxruntime_gpu*", "onnxruntime-directml*",
            "audio_separator*",
            "nvidia*"
        ]
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
            torch_ver = "2.5.1+cpu"
            tv_ver = "0.20.1+cpu"
            ta_ver = "2.5.1+cpu"
            install_steps = [
                ["setuptools", "wheel", "pip", "msvc-runtime>=14.40"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime==1.18.0", "audio-separator"]
            ]
        elif target_mode == "directml":
            self.log("📦 正在部署獨立 DirectML AI 核心...")
            torch_index = "https://download.pytorch.org/whl/cpu"
            torch_ver = "2.5.1+cpu"
            tv_ver = "0.20.1+cpu"
            ta_ver = "2.5.1+cpu"
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
            is_rtx50 = False
            if has_nvidia_gpu:
                try:
                    res = subprocess.run(
                        ["nvidia-smi", "-L"],
                        capture_output=True, text=True,
                        creationflags=self.subp_flags, timeout=10,
                        encoding="utf-8", errors="replace"
                    )
                    if res.returncode == 0 and "RTX 50" in res.stdout:
                        is_rtx50 = True
                except Exception:
                    pass

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
        透過 wmic 實際查詢系統是否有 NVIDIA GPU（已啟用）。
        只查硬體，不依賴 CUDA/PyTorch，避免「GPU 停用但 CUDA driver 仍在」的誤判。
        注意：wmic 輸出的 Name 與 Status 是不同欄位（不在同一行），
              只需確認 Name 欄位中有 NVIDIA 字樣即可，停用的裝置不會出現在列表中。
        備援方案：若 wmic 失敗（部分 Windows 11 已移除），改用 PowerShell。
        """
        # 方法一：wmic（Windows 10 / 部分 Windows 11）
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "Name"],
                capture_output=True, text=True, timeout=10,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
            )
            if result.returncode == 0:
                output = result.stdout.upper()
                if "NVIDIA" in output:
                    return True
                # wmic 成功執行但沒有 NVIDIA -> 確定沒有
                return False
        except Exception:
            pass  # wmic 不存在時繼續嘗試備援

        # 方法二：PowerShell（Windows 11 wmic 已移除時的備援）
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Display | Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=15,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
            )
            if "NVIDIA" in result.stdout.upper():
                return True
        except Exception:
            pass

        return False

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

            # 1. 優先檢查 NVIDIA GPU
            if has_nvidia_gpu and (self.gpu_lib_dir / "torch").exists():
                gpu_out = self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True)
                if gpu_out == "ORT_OK_GPU":
                    self.log("✅ 偵測到獨立 NVIDIA GPU 核心已就緒，自動切換至 GPU 模式。")
                    self.root.after(0, lambda: self.device_var.set("gpu"))
                    self._reset_ort_fix_prompt_state(clear_history=False)
                    return

            # 2. 其次檢查 DirectML
            if (self.directml_lib_dir / "onnxruntime").exists():
                directml_out = self._probe_onnxruntime_stack(self.directml_lib_dir, expect_gpu=False)
                if directml_out == "ORT_OK_CPU":
                    self.log("✅ 偵測到 DirectML 核心已就緒，自動切換至 DirectML 模式。")
                    self.root.after(0, lambda: self.device_var.set("directml"))
                    self._reset_ort_fix_prompt_state(clear_history=False)
                    return

            # 3. 最後檢查 CPU
            cpu_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
            if cpu_out == "ORT_OK_CPU":
                self.log("✅ 基礎環境已就緒（CPU 模式）。")
                if self.device_var.get() in ["gpu", "directml"]:
                    self.root.after(0, lambda: self.device_var.set("cpu"))
                self._reset_ort_fix_prompt_state(clear_history=False)
            else:
                self.log(f"ℹ️ CPU 核心檢測結果: {cpu_out}")

            # 顯示檢測結果
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

        # 先確認系統確實有啟用的 NVIDIA GPU，否則直接回傳 False
        # 這樣可避免筆電拔掉電源、GPU 被停用時，CUDA driver 仍存在而誤判為可用
        if not self._is_nvidia_gpu_present():
            return False
        if not (self.gpu_lib_dir / "torch").exists():
            return False
        return self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True) == "ORT_OK_GPU"

    def start_separation(self):
        if not self.file_list:
            messagebox.showwarning("警告", "請先加入音檔！")
            return
        if self.is_processing: return

        # 如果選用 GPU 但環境尚未檢測或不完全，先提示檢測
        if self.device_var.get() == "gpu":
            self.log("🚀 啟動前檢查 GPU 環境...")
            # 這裡不彈出視窗，直接執行背景檢測
            if not self._quick_check_gpu():
                if messagebox.askyesno("環境未就緒", "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？\n(若不修復將改用 CPU 運行，速度較慢)"):
                    self.check_gpu_env()
                    return
                else:
                    self.log("⚠️ 使用者選擇忽略，將嘗試改用 CPU 模式。")
                    self.device_var.set("cpu")

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在處理中...", "orange")
        
        # 啟動批次處理線程
        threading.Thread(target=self.batch_process, daemon=True).start()

    def start_yt_process(self):
        url = self.yt_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "請輸入 YouTube 網址！")
            return
        if self.is_processing: return

        # 如果選用 GPU 但環境尚未檢測或不完全，先提示檢測
        if self.device_var.get() == "gpu":
            if not self._quick_check_gpu():
                if messagebox.askyesno("環境未就緒", "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？"):
                    self.check_gpu_env()
                    return
                else:
                    self.device_var.set("cpu")

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在從 YouTube 下載並處理...", "orange")
        
        threading.Thread(target=self.yt_process, args=(url,), daemon=True).start()

    def yt_process(self, url):
        import time
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
        
        # 1. 僅下載 MP4 (影像+音訊)，節省頻寬
        video_file = self.download_youtube(
            url,
            output_dir,
            mode="mp4",
            download_subtitles=(subtitle_mode in ("srt_only", "mux"))
        )
        
        if not video_file:
            self.log("❌ YouTube 影片下載失敗。")
            self.finish_processing()
            return

        # 2. 從 MP4 中擷取 MP3 音訊進行分離，避免二次下載
        self.update_progress(30, "正在從影片擷取音訊", step_text="步驟 2/5：從影片擷取音訊")
        self.log("  > 正在從下載的影片中擷取音訊...")
        video_path = Path(video_file)
        audio_file = str(video_path.parent / f"{video_path.stem}_audio.mp3")
        
        ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
        extract_cmd = [
            str(ffmpeg_exe), "-y", "-i", video_file,
            "-vn", "-acodec", "libmp3lame", "-ab", "320k", audio_file
        ]
        
        try:
            subprocess.run(extract_cmd, check=True, creationflags=self.subp_flags)
            self.log(f"  ✅ 音訊擷取完成: {os.path.basename(audio_file)}")
        except Exception as e:
            self.log(f"  ❌ 音訊擷取失敗: {str(e)}")
            self.finish_processing()
            return

        self.update_progress(40, "正在分離人聲與伴奏", step_text="步驟 3/5：AI 人聲分離中")
        
        # 3. 執行分離
        success = self.run_audio_separator(audio_file, output_dir)
        
        if success:
            # 歌詞識別（如果啟用）- do this BEFORE consolidate_stems which deletes the file
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
                    
                    # 強制重新命名 SRT
                    self.log(f"  📝 處理 SRT 字幕檔...")
                    try:
                        old_srt_path = Path(srt_subtitle)
                        self.log(f"  📝 原始檔案: {old_srt_path.name}")
                        self.log(f"  📝 目標檔案: {final_srt_path.name}")
                        
                        if old_srt_path.exists():
                            if final_srt_path.exists():
                                self.log(f"  📝 刪除舊的目標檔案")
                                final_srt_path.unlink()
                            self.log(f"  📝 執行重新命名...")
                            shutil.move(str(old_srt_path), str(final_srt_path))
                            srt_subtitle = str(final_srt_path)
                            self.log(f"  ✅ SRT 已重新命名為: {final_srt_path.name}")
                    except Exception as e:
                        self.log(f"  ❌ SRT 重新命名失敗: {str(e)}")
                        import traceback
                        self.log(f"     {traceback.format_exc()}")
                    
                    # 強制重新命名 JSON
                    if json_subtitle:
                        self.log(f"  📝 處理 JSON 歌詞檔...")
                        try:
                            old_json_path = Path(json_subtitle)
                            self.log(f"  📝 原始檔案: {old_json_path.name}")
                            self.log(f"  📝 目標檔案: {final_json_path.name}")
                            
                            if old_json_path.exists():
                                if final_json_path.exists():
                                    self.log(f"  📝 刪除舊的目標檔案")
                                    final_json_path.unlink()
                                self.log(f"  📝 執行重新命名...")
                                shutil.move(str(old_json_path), str(final_json_path))
                                self.log(f"  ✅ JSON 已重新命名為: {final_json_path.name}")
                        except Exception as e:
                            self.log(f"  ❌ JSON 重新命名失敗: {str(e)}")
                            import traceback
                            self.log(f"     {traceback.format_exc()}")
            
            # 分隔完成，現在整理檔案
            self.log("📦 正在整理並重新命名產出檔案...")
            voc_file, inst_file = self.consolidate_stems(audio_file, video_file, output_dir)
            
            if voc_file and inst_file:
                # 3. 合成 KTV 影片
                vfmt = self.video_format_var.get()
                self.update_progress(80, f"正在合成 {vfmt.upper()} 伴唱帶", step_text=f"步驟 5/5：合成 {vfmt.upper()} 伴唱帶")
                output_file = Path(output_dir) / f"{Path(video_file).stem}_KTV.{vfmt}"
                
                subtitle_for_mux = self._last_downloaded_subtitle if subtitle_mode == "mux" else None
                if srt_subtitle and not subtitle_for_mux:
                    subtitle_for_mux = srt_subtitle
                
                mkv_success = self.synthesize_mkv(
                    video_file,
                    voc_file,
                    inst_file,
                    str(output_file),
                    subtitle_file=subtitle_for_mux
                )
                
                if mkv_success:
                    self.log(f"✅ 成功生成 {vfmt.upper()} 伴唱帶: {output_file.name}")
                    # 确保所有字幕文件都与最终KTV视频文件名一致
                    if self._last_downloaded_subtitle:
                        self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, str(output_file))
                    # Whisper生成的字幕已经是 {video_stem}_KTV.srt，不需要再调用align_subtitle_filename
                    
                    self.update_progress(100, "處理完成", step_text="✅ 完成！")
                    elapsed_time = time.time() - start_time
                    self.log(f"⏱️ YouTube 處理完成，總花費時間: {elapsed_time:.2f} 秒")
                    messagebox.showinfo("成功", f"YouTube 處理完成！\n總花費時間: {elapsed_time:.2f} 秒\n檔案已儲存至: {output_dir}")
                    if os.name == 'nt' and os.path.exists(output_dir):
                        os.startfile(output_dir)
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
        audio_stem = Path(input_audio).stem
        video_stem = Path(reference_video).stem
        out_path = Path(output_dir)
        
        def safe_stem(stem):
            """清除非法字元並截短，避免 Windows 路徑過長"""
            return AudioSeparatorApp.sanitize_filename(stem, max_len=60)

        voc_final = out_path / f"{safe_stem(video_stem)}_人聲.{fmt}"
        inst_final = out_path / f"{safe_stem(video_stem)}_伴奏.{fmt}"
        
        # 1. 尋找人聲
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and "(Vocals)" in f.name and f.suffix == f".{fmt}":
                if voc_final.exists(): os.remove(voc_final)
                f.rename(voc_final)
                break
        
        # 2. 尋找伴奏 (MDX 模式)
        found_inst = False
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and any(x in f.name for x in["(Instrumental)", "(No Vocals)"]) and f.suffix == f".{fmt}":
                if inst_final.exists(): os.remove(inst_final)
                f.rename(inst_final)
                found_inst = True
                break
        
        # 3. 如果沒找到伴奏，檢查是否為 Demucs 多音軌模式
        if not found_inst:
            stems_to_merge =[]
            # Demucs 標籤通常包含這些；6s 模型還會多拆出 Guitar / Piano
            tags = [
                "(Bass)", "(Drums)", "(Other)", "(Guitar)", "(Piano)",
                "_Bass", "_Drums", "_Other", "_Guitar", "_Piano"
            ]
            for f in out_path.iterdir():
                if f.name.startswith(audio_stem) and any(tag in f.name for tag in tags) and f.suffix == f".{fmt}":
                    stems_to_merge.append(f)
            
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

        # 4. 清理所有相關暫存檔
        self.log("🧹 正在清理暫存檔案...")
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and not f.suffix.lower() in ['.srt', '.json'] and '_karaoke.mp3' not in f.name:
                try: f.unlink()
                except Exception as e:
                    self.log(f"  ⚠️ 清理暫存檔失敗: {f.name} ({str(e)})")
        if Path(input_audio).exists():
            try: os.remove(input_audio)
            except Exception as e:
                self.log(f"  ⚠️ 清理原始音檔失敗: {str(e)}")
            
        return (str(voc_final) if voc_final.exists() else None, 
                str(inst_final) if inst_final.exists() else None)
        # 注意：不在此處呼叫 finish_processing()，由呼叫端負責

    def finish_processing(self):
        self.is_processing = False
        self.cancel_event.clear()
        self._current_process = None
        self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.cancel_btn.config(state=tk.DISABLED))
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
        # 移除 Windows 路徑非法字元
        illegal = r'\/:*?"<>|'
        for ch in illegal:
            title = title.replace(ch, '_')
        
        # 移除控制字元與不可見字元，避免檔名出現亂碼感
        title = "".join(char for char in title if char.isprintable())
        
        # 合併連續空白/底線
        title = re.sub(r'[\s_]+', '_', title).strip('_')
        
        # 截短：優先考慮字元數，但也要注意 Windows 的位元組限制
        if len(title) > max_len:
            title = title[:max_len]
        
        # 確保 UTF-8 編碼後的長度不會過長 (Windows 單個檔名限制約 255 bytes)
        while len(title.encode('utf-8', errors='replace')) > 180:
            title = title[:-1]
            
        return title.strip() or 'video'

    def download_youtube(self, url, output_dir, mode="both", download_subtitles=False):
        """使用 yt-dlp 下載影片與音訊"""
        self.log("🚀 正在下載 YouTube 內容...")
        self._last_downloaded_subtitle = None
        
        # 提取影片 ID 作為可靠的檔案追蹤標記（涵蓋標準、Shorts、嵌入、youtu.be 格式）
        video_id = self.extract_youtube_video_id(url) or "temp_id"

        ytdlp_cmd_base = self._get_ytdlp_command_base()
        # 確保 yt_dlp 模組能從 lib_dir 和 ytdlp_dir 找到
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = os.pathsep.join([str(self.ytdlp_dir), str(self.common_lib_dir), str(self.lib_dir)])
        js_runtime_opts = self._get_ytdlp_js_runtime_opts() if download_subtitles else []

        # 定義兩個 common_opts：一個有 cookie，一個沒有，加上重試和延遲來處理 429
        common_opts_with_cookie =[
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "2",
            "--sleep-interval", "3"
        ] + js_runtime_opts + self._get_cookie_opts(force_no_cookie=False)
        
        common_opts_no_cookie =[
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "exp=1:5",
            "--sleep-requests", "2",
            "--sleep-interval", "3"
        ] + js_runtime_opts + self._get_cookie_opts(force_no_cookie=True)

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
                
                # 檢查是否有 Cookie 錯誤
                if "could not copy chrome cookie" in line.lower() or "cookie database" in line.lower():
                    has_cookie_error = True
                
                # 顯示進度資訊
                if "[download]" in line and "%" in line:
                    match = re.search(r"(\d+\.\d+)%", line)
                    if match:
                        percent = float(match.group(1))
                        if int(percent) > last_percent:
                            self.log(f"    {line}")
                            last_percent = int(percent)
                            # 把下載百分比映射到整體 5–28% 區間，並顯示步驟說明
                            mapped = 5 + int(percent * 0.23)
                            self.update_progress(mapped, f"正在下載 {step_name}",
                                                 step_text=f"步驟 1/5：下載 {step_name} {int(percent)}%")
                elif any(x in line for x in ["[ffmpeg]", "Merging", "Extracting", "Destination"]):
                    self.log(f"    {line}")
                elif "ERROR" in line.upper():
                    self.log(f"  ❌ {line}")
                    # 加入中文錯誤解釋
                    error_lower = line.lower()
                    if "this video is not available" in error_lower:
                        self.log(f"  💡 中文說明：此影片無法存取！可能原因：")
                        self.log(f"     1. 影片是私人影片")
                        self.log(f"     2. 影片已被刪除")
                        self.log(f"     3. 影片有地區鎖定（Geo-block）")
                        self.log(f"     4. 請確認您使用的是「單一影片」連結，不是播放列表或電台連結！")
                    elif "video unavailable" in error_lower:
                        self.log(f"  💡 中文說明：影片無法使用！")
                    elif "age restricted" in error_lower:
                        self.log(f"  💡 中文說明：此影片有年齡限制，建議使用 Cookie 選項！")
                    elif "sign in to confirm" in error_lower:
                        self.log(f"  💡 中文說明：需要登入確認年齡，請在設定中選擇瀏覽器 Cookie！")
                    elif "cookie database" in error_lower or "could not copy chrome" in error_lower:
                        self.log(f"  💡 中文說明：無法讀取瀏覽器 Cookie，正在自動重試而不用 Cookie...")
                    elif "429" in error_lower or "too many requests" in error_lower:
                        self.log(f"  💡 中文說明：YouTube 封鎖太多請求！")
                        self.log(f"     已自動開啟重試和延遲機制，請稍候...")
                        self.log(f"     如果還是失敗，請稍後再試或選擇瀏覽器 Cookie！")
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
                # 根據修改時間排序，取最新的
                files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return str(files[0])
            return None

        video_file = None
        audio_file = None

        # --- 取得安全短檔名（強制使用 UTF-8 編碼解決 Windows 亂碼問題）---
        safe_name = video_id  # fallback
        try:
            # 先試有 cookie，失敗就試無 cookie
            for try_cookie in [True, False]:
                ytdlp_env_info = ytdlp_env.copy()
                ytdlp_env_info["PYTHONIOENCODING"] = "utf-8"
                title_cmd = ytdlp_cmd_base + ["--no-playlist"] + js_runtime_opts + self._get_cookie_opts(force_no_cookie=not try_cookie) + ["--print", "%(title)s", url]
                title_result = subprocess.run(
                    title_cmd,
                    capture_output=True, text=True, creationflags=self.subp_flags,
                    timeout=30, encoding='utf-8', errors='replace', env=ytdlp_env_info
                )
                raw_title = title_result.stdout.strip().splitlines()[0] if title_result.stdout.strip() else ""
                if raw_title:
                    # 額外清理：移除標題中可能導致檔名解析問題的 [ 或 ]
                    raw_title = raw_title.replace('[', '(').replace(']', ')')
                    safe_name = self.sanitize_filename(raw_title, max_len=80)
                    self.log(f"  📝 影片標題: {raw_title}")
                    self.log(f"  📝 安全檔名: {safe_name}")
                    break
        except Exception as e:
            self.log(f"  ⚠️ 取得標題失敗，使用影片 ID 作為檔名: {str(e)}")

        if download_subtitles and mode in ["both", "mp4"]:
            self._last_downloaded_subtitle = self.download_youtube_subtitle(url, output_dir, video_id)

        # 下載 MP4
        if mode in ["both", "mp4"]:
            mp4_out = os.path.join(output_dir, f"{safe_name}_{video_id}.mp4")
            video_format = "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            
            # 先嘗試有 cookie 的版本
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

        # 下載 MP3
        if mode in ["both", "mp3"]:
            self.log("  > 正在準備 MP3 音訊...")
            mp3_out = os.path.join(output_dir, f"{safe_name}_{video_id}_audio.mp3")
            
            current_common_opts_mp3 = common_opts_with_cookie
            mp3_ok = False
            
            while True:
                mp3_cmd = ytdlp_cmd_base + current_common_opts_mp3 + [
                    "-x", "--audio-format", "mp3",
                    "--audio-quality", "320K",
                    "-o", mp3_out,
                    url
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

        # 使用 PYTHONIOENCODING 確保 yt-dlp 內部處理與輸出均為 UTF-8
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)
        ytdlp_env["PYTHONIOENCODING"] = "utf-8"
        js_runtime_opts = self._get_ytdlp_js_runtime_opts()
        cookie_opts = self._get_cookie_opts()

        subtitle_out = os.path.join(output_dir, f"{video_id}.%(ext)s")
        subtitle_patterns = [
            f"{video_id}*.srt",
            f"{video_id}*.vtt",
            f"{video_id}*.ass",
            f"{video_id}*.srv3",
        ]
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
                
                # 建立語言清單，包含顯示名稱和類型
                langs_with_info = []
                
                # 優先加入手動字幕
                for lang in sorted(manual):
                    display_name = lang_display_map.get(lang, lang)
                    langs_with_info.append((lang, display_name, True))
                
                # 再加入自動字幕
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
            
            # 將語言加入 Listbox
            for i, (lang_code, display_name, is_manual) in enumerate(langs_with_info):
                tag = "手動字幕" if is_manual else "自動字幕"
                listbox.insert(tk.END, f"{display_name} ({lang_code}) - {tag}")
                if is_manual:
                    listbox.itemconfig(i, {'fg': 'blue'})
            
            # 預設選取第一個
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

        # 取得所有可用語言
        langs_with_info = get_available_langs_from_metadata()
        
        if not langs_with_info:
            self.log("  ℹ️ 這支影片沒有可用的 YouTube CC 字幕。")
            return None
        
        self.log(f"  ℹ️ 找到 {len(langs_with_info)} 種可用字幕語言")
        
        # 顯示語言選擇對話框
        selected_lang = None
        try:
            selected_lang = show_language_selection_dialog(langs_with_info)
        except Exception as e:
            self.log(f"  ⚠️ 顯示語言選擇對話框失敗：{str(e)}")
        
        if not selected_lang:
            self.log("  ℹ️ 使用者取消字幕下載。")
            return None
        
        self.log(f"  > 已選擇字幕語言：{selected_lang}")
        
        # 下載選擇的語言
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

    def align_subtitle_filename(self, subtitle_file, target_media_file):
        """將字幕檔改名成與目標媒體檔完全同主檔名，副檔名固定為 .srt。"""
        try:
            subtitle_path = Path(subtitle_file)
            target_path = Path(target_media_file)
            if not subtitle_path.exists() or not target_path.exists():
                return subtitle_file

            # 統一改成和最終媒體檔同名，只保留 .srt
            desired_path = target_path.parent / f"{target_path.stem}.srt"

            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)

            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass

            # 使用 shutil.move 替代 rename，跨磁碟機移動較穩健
            import shutil
            shutil.move(str(subtitle_path), str(desired_path))
            self.log(f"  📝 字幕檔已對齊命名：{desired_path.name}")
            return str(desired_path)
        except Exception as e:
            self.log(f"  ⚠️ 字幕檔重新命名失敗，保留原檔名：{str(e)}")
            return subtitle_file

    def synthesize_mkv(self, video_file, vocal_file, instrumental_file, output_file, subtitle_file=None):
        """合成 KTV 伴唱帶：支援雙音軌模式與左伴唱/右人聲單音軌模式"""
        vfmt = self.video_format_var.get().upper()
        track_mode = self.audio_track_mode_var.get()  # "dual" or "lr"
        vocal_mix = max(0.0, min(1.0, float(self.vocal_mix_var.get()) / 100.0))
        instrumental_mix = max(0.0, 1.0 - vocal_mix)
        vocal_pct = int(round(vocal_mix * 100))
        inst_pct = int(round(instrumental_mix * 100))
        force_1080p = self.force_1080p_var.get()
        
        self.log(f"🎬 正在合成 {vfmt} 伴唱帶（音軌模式：{'雙音軌' if track_mode == 'dual' else '左伴唱/右人聲+伴奏'}）...")
        if track_mode == "dual":
            self.log(f"🎚️ 導唱混合比例：人聲 {vocal_pct}% / 伴奏 {inst_pct}%")
        else:
            self.log("🎚️ 目前為左伴唱／右人聲+伴奏模式，混合比例設定不套用於此模式。")
        if force_1080p:
            self.log("🖼️ 已啟用強制等比輸出 1080p，必要時會補黑邊。")
        # 字幕不在此封裝（AI 辨識字幕錯字多，由使用者自行後製），忽略傳入的 subtitle_file
        subtitle_file = None
        
        # 檢查輸入檔案是否存在
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
            # 先製作「左伴奏 / 右（人聲+伴奏）」立體聲 MP3，再合入影片
            import tempfile, os as _os
            lr_tmp = Path(tempfile.mktemp(suffix="_lr_stereo.mp3"))
            self.log("🎚️ 正在製作「左伴奏／右人聲+伴奏」立體聲音軌...")
            if not self.create_lr_stereo(str(instrumental_file), str(vocal_file), str(lr_tmp)):
                self.log("  ❌ 無法製作 LR 立體聲音軌，合成中止。")
                return False

            # 用製作好的 LR MP3 直接合入影片（單音軌，不需 filter_complex 做音訊處理）
            cmd += ["-i", str(lr_tmp)]   # 1: 已完成的 LR 立體聲

            audio_filter = None          # 不需要音訊 filter
            audio_maps = [
                "-map", "0:v",
                "-map", "1:a",
                "-metadata:s:a:0", "title=左伴唱／右人聲+伴奏",
            ]
        else:
            # 預設：雙音軌 (音軌1=伴唱+人聲混合, 音軌2=純伴奏)
            cmd += [
                "-i", str(vocal_file),           # 1: 人聲
                "-i", str(instrumental_file),    # 2: 伴奏
            ]
            audio_filter = f"[1:a][2:a]amix=inputs=2:duration=first:weights='{vocal_mix:.2f} {instrumental_mix:.2f}'[mix]"
            audio_maps = [
                "-map", "0:v",
                "-map", "[mix]",                 # 音軌 1: 導唱 (人聲+伴奏)
                "-map", "2:a",                   # 音軌 2: 純伴奏
                "-metadata:s:a:0", f"title=導唱 (人聲{vocal_pct}% + 伴奏{inst_pct}%)",
                "-metadata:s:a:1", "title=伴唱 (純伴奏)",
            ]

        # force_1080p 時影片縮放必須整合進 filter_complex，
        # 因為 FFmpeg 不允許同時使用 -filter_complex 和 -vf
        # audio_filter=None 代表 LR 模式已預先製作好音訊，不需要音訊 filter
        if force_1080p:
            scale_filter = (
                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )
            if audio_filter is not None:
                full_filter = f"[0:v]{scale_filter}[vout];{audio_filter}"
            else:
                full_filter = f"[0:v]{scale_filter}[vout]"
            video_map = "[vout]"
        else:
            full_filter = audio_filter  # 可能為 None（LR 模式）
            video_map = "0:v"

        # 修正 audio_maps 中的影片 map（部分模式寫死 "0:v"）
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
            subprocess.run(
                cmd, check=True, creationflags=self.subp_flags,
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
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
            # 清除 LR 模式產生的暫存音訊檔
            if track_mode == "lr":
                try:
                    if lr_tmp.exists():
                        lr_tmp.unlink()
                except Exception:
                    pass

    def batch_process(self):
        import time
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
        self.is_processing = False
        self.start_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        messagebox.showinfo("成功", f"批次處理完成！\n已處理 {total} 個檔案。\n總花費時間: {elapsed_time:.2f} 秒")
        if os.name == 'nt' and os.path.exists(output_dir):
            os.startfile(output_dir)

    def run_audio_separator(self, input_file, output_dir):
        import time
        self.fix_python_pth()
        
        fmt = self.output_format_var.get()
        device_val = self.device_var.get()
        if device_val == "gpu":
            device = "cuda"
        elif device_val == "directml":
            device = "directml"
        else:
            device = "cpu"
        runtime_ready, device, runtime_lib_dir = self._ensure_runtime_stack_ready(device)
        if not runtime_ready:
            return False

        env = self._build_python_env(runtime_lib_dir, include_gpu_runtime=(device == "cuda"))
        start_time = time.time()
        runtime_lib_dir_posix = str(runtime_lib_dir).replace("\\", "/")
        common_lib_dir_posix = str(self.common_lib_dir).replace("\\", "/")
        app_bin_dir_posix = str(self.bin_dir).replace("\\", "/")
        app_py_dir_posix = str(self.py_dir).replace("\\", "/")
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
        
        # 取得選取的模型名稱
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
                # Demucs 專用參數（htdemucs 系列使用 --demucs_ 前綴參數）
                command.extend([
                    "--demucs_segment_size", "None",
                    "--demucs_shifts", "2",
                    "--demucs_overlap", "0.25",
                ])
            else:
                # MDX 專用參數
                command.extend([
                    "--mdx_overlap", str(self.overlap_var.get()),
                    "--mdx_segment_size", "256",
                    "--mdx_hop_length", "1024"
                ])
                if self.denoise_var.get():
                    command.append("--mdx_enable_denoise")

            # 根據裝置選擇
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

            # 注意：不能用 glob，因為檔名含 [ ] 等字元在 glob 語法中是特殊字元，
            # 會導致匹配失敗。改用 os.listdir 直接字串比對。
            keywords = ["_(Vocals)", "_(Instrumental)", "_(No Vocals)",
                        "_(Bass)", "_(Drums)", "_(Other)"]
            
            found_files = []
            try:
                self.log(f"  🔍 正在檢查輸出目錄: {out_path}")
                self.log(f"  🔍 預期前綴: {input_stem}")
                
                for fname in os.listdir(str(out_path)):
                    # 更寬鬆的檢查：只要包含部分前綴和正確副檔名，且有關鍵字即可
                    if fname.endswith(f".{fmt}"):
                        found_files.append(fname)
                        if any(kw in fname for kw in keywords):
                            # 只要有包含任何一個關鍵字且副檔名正確，就算成功
                            self.log(f"  ✅ 找到輸出檔案: {fname}")
                            return True
                            
                self.log(f"  ⚠️ 找到的檔案數量: {len(found_files)}")
                for f in found_files[:5]:  # 只顯示前5個
                    self.log(f"    - {f}")
            except Exception as e:
                self.log(f"  ❌ 檢查輸出檔案時出錯: {str(e)}")
                import traceback
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

                        # 嘗試解析進度 (audio-separator 輸出通常包含百分比)
                        if "%" in line:
                            try:
                                match = re.search(r"(\d+)%", line)
                                if match:
                                    pct = int(match.group(1))
                                    # 把分離進度映射到整體進度的 40–68% 區間
                                    mapped = 40 + int(pct * 0.28)
                                    self.update_progress(mapped, "正在分離人聲與伴奏",
                                                         step_text=f"步驟 3/5：AI 人聲分離中 {pct}%")
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

        success, reason = run_model(selected_model, is_retry=False)
        if success:
            elapsed_time = time.time() - start_time
            device_display = "NVIDIA GPU" if device == "cuda" else ("DirectML" if device == "directml" else "CPU")
            self.log(f"⏱️ 音訊分離完成，總花費時間: {elapsed_time:.2f} 秒 (裝置: {device_display})")
            return True

        if reason == "retry_with_demucs":
            self.log(f"🎯 回退模型: {selected_model} → {fallback_model}")
            retry_success, retry_reason = run_model(fallback_model, is_retry=True)
            if retry_success:
                elapsed_time = time.time() - start_time
                device_display = "NVIDIA GPU" if device == "cuda" else ("DirectML" if device == "directml" else "CPU")
                self.log(f"⏱️ 音訊分離完成，總花費時間: {elapsed_time:.2f} 秒 (裝置: {device_display})")
                self.log(f"✅ 已改用 `{fallback_model}` 完成音訊分離。")
                self.log("💡 若想恢復使用原本的 MDX 模型，請刪除舊的 .onnx 後重新下載。")
                return True

            if retry_reason == "unsupported_model":
                self.log("❌ 備援模型也無法載入，請執行「一鍵修復/初始化環境」，或手動清理模型目錄後再試。")
            else:
                self.log("❌ 已嘗試自動切換備援模型，但仍未成功完成分離。")

        return False

    def browse_merge_video(self):
        file_path = filedialog.askopenfilename(
            title="選擇影片檔案",
            filetypes=[("影片檔案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有檔案", "*.*")]
        )
        if file_path:
            self.merge_video_path_var.set(file_path)
            self.player_video_path = file_path
            self.reload_player()
    
    def browse_merge_subtitle(self):
        file_path = filedialog.askopenfilename(
            title="選擇字幕檔案",
            filetypes=[("字幕檔案", "*.srt *.ass *.ssa *.json"), ("所有檔案", "*.*")]
        )
        if file_path:
            self.merge_subtitle_path_var.set(file_path)
            self.player_subtitle_path = file_path
            is_json = file_path.lower().endswith('.json')
            is_srt  = file_path.lower().endswith('.srt')
            if is_json or is_srt:
                # 顯示樣式設定框
                self.ktv_color_frame.pack(fill=tk.X, pady=5)
                if is_json:
                    # JSON：完整顯示，標題和顏色標籤為 KTV 模式
                    self.ktv_color_frame.config(text="KTV 逐字漸變顏色設定")
                    self.unplayed_label.config(text="未唱顏色:")
                    self.played_row.pack(fill=tk.X, pady=5)
                    self.mode_row.pack(fill=tk.X, pady=5)
                    self.mode_hint_row.pack(fill=tk.X, pady=(0, 3))
                else:
                    # SRT：隱藏已唱顏色和變色模式，標題和標籤為一般字幕模式
                    self.ktv_color_frame.config(text="字幕樣式設定")
                    self.unplayed_label.config(text="字幕顏色:")
                    self.played_row.pack_forget()
                    self.mode_row.pack_forget()
                    self.mode_hint_row.pack_forget()
            else:
                self.ktv_color_frame.pack_forget()
            self.reload_player()
    
    def browse_rec_video(self):
        file_path = filedialog.askopenfilename(
            title="選擇影片檔案",
            filetypes=[("影片檔案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有檔案", "*.*")]
        )
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
        
        self.is_processing = True
        self.cancel_event.clear()
        
        threading.Thread(target=self.recognize_lyrics_process, args=(video_path,), daemon=True).start()
    
    def recognize_lyrics_process(self, video_path):
        try:
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            import tempfile
            temp_dir = tempfile.gettempdir()
            video_stem = Path(video_path).stem
            original_video_stem = video_stem
            temp_audio = os.path.join(temp_dir, f"{video_stem}_temp_audio.mp3")
            
            # Step 1: 從影片提取音訊
            self.update_status("正在提取音訊...", "orange")
            self.log("\n🎤 開始從影片提取音訊...")
            
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            ffmpeg_cmd = [
                str(ffmpeg_exe),
                "-i", video_path,
                "-vn",
                "-acodec", "libmp3lame",
                "-ab", "192k",
                "-ar", "44100",
                "-y",
                temp_audio
            ]
            
            self.log(f"  > 執行: {' '.join(ffmpeg_cmd)}")
            
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=self.subp_flags
            )
            self._current_process = process
            
            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    self.log("🛑 已取消操作")
                    self.is_processing = False
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
                self.is_processing = False
                return
            
            self.log("✅ 音訊提取完成")
            
            # Step 2: 決定要辨識的音訊來源
            separate_first = self.rec_separate_first_var.get()
            audio_for_recognition = temp_audio  # 預設直接用全音軌

            if separate_first:
                self.update_status("正在分離人聲...", "orange")
                self.log("\n🎵 先進行人聲分離以提升辨識準確度...")

                # 直接分離到 output_dir，讓人聲與伴奏永久保留
                sep_success = self.run_audio_separator(temp_audio, output_dir)

                if sep_success:
                    # 在 output_dir 中尋找分離出來的人聲檔與伴奏檔，並重新命名為易讀檔名
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

                    # 重新命名為 {原始影片名}_人聲 / _伴奏
                    safe_stem = AudioSeparatorApp.sanitize_filename(original_video_stem, max_len=60)
                    if found_vocal:
                        vocal_final = Path(output_dir) / f"{safe_stem}_人聲{found_vocal.suffix}"
                        try:
                            found_vocal.rename(vocal_final)
                            found_vocal = vocal_final
                        except Exception:
                            pass  # 重新命名失敗就沿用原名
                        self.log(f"  ✅ 人聲檔案已儲存: {found_vocal.name}")
                        audio_for_recognition = str(found_vocal)
                    else:
                        self.log("  ⚠️ 找不到分離後的人聲檔，改用原始音訊辨識")

                    if found_inst:
                        inst_final = Path(output_dir) / f"{safe_stem}_伴奏{found_inst.suffix}"
                        try:
                            found_inst.rename(inst_final)
                        except Exception:
                            pass
                        self.log(f"  ✅ 伴奏檔案已儲存: {inst_final.name}")
                else:
                    self.log("  ⚠️ 人聲分離失敗，改用原始音訊辨識")

            # Step 3: Whisper 辨識歌詞
            self.update_status("正在進行 AI 歌詞識別...", "orange")
            rec_lang = self.rec_lyrics_language_var.get() if hasattr(self, 'rec_lyrics_language_var') else "traditional"
            result = self.recognize_lyrics_and_generate_srt(audio_for_recognition, output_dir, original_video_stem, override_language=rec_lang)

            # 只清理從影片提取的暫存全音軌（分離後的人聲/伴奏已保留在 output_dir）
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
                messagebox.showinfo("完成", f"歌詞辨識完成！\n\nSRT 字幕: {Path(srt_file).name}\nJSON 歌詞: {Path(json_file).name}\n\n檔案已儲存至: {output_dir}")
                if os.name == 'nt' and os.path.exists(output_dir):
                    os.startfile(output_dir)
            else:
                self.log("❌ 歌詞辨識失敗")
                messagebox.showerror("錯誤", "歌詞辨識失敗，請查看日誌！")
            
        except Exception as e:
            self.log(f"❌ 發生錯誤: {str(e)}")
            import traceback
            self.log(f"   {traceback.format_exc()}")
            messagebox.showerror("錯誤", f"發生錯誤: {str(e)}")
        finally:
            self.is_processing = False
            self.update_status("就緒", "black")
    
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
        
        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED, text="處理中...")
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在合併字幕與影片...", "orange")
        
        threading.Thread(target=self.merge_subtitle_video_process, args=(video_path, subtitle_path), daemon=True).start()
    
    def merge_subtitle_video_process(self, video_path, subtitle_path):
        try:
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            video_stem = Path(video_path).stem
            vfmt = self.merge_video_format_var.get()
            output_file = Path(output_dir) / f"{video_stem}_含字幕.{vfmt}"
            
            self.log(f"--- 正在合併: {os.path.basename(video_path)} + {os.path.basename(subtitle_path)} ---")
            self.update_progress(10, "準備合併...")
            
            # 檢查字幕類型
            is_json_subtitle = subtitle_path.lower().endswith('.json')
            
            is_srt_subtitle = subtitle_path.lower().endswith('.srt')
            if is_json_subtitle:
                self.log("🎤 偵測到 JSON 字幕，將處理為 KTV 逐字效果...")
                success = self.process_json_subtitle_to_video(video_path, subtitle_path, str(output_file))
            elif is_srt_subtitle:
                self.log("📝 偵測到 SRT 字幕，將套用字幕樣式後燒錄...")
                success = self.process_srt_subtitle_to_video(video_path, subtitle_path, str(output_file))
            else:
                self.log("📝 正在使用 FFmpeg 合併字幕...")
                success = self.merge_subtitle_with_ffmpeg(video_path, subtitle_path, str(output_file), vfmt)
            
            if success:
                self.update_progress(100, "合併完成")
                self.log(f"✅ 成功生成影片: {output_file.name}")
                messagebox.showinfo("完成", f"合併完成！\n檔案已儲存至: {output_file}")
                if os.name == 'nt' and os.path.exists(output_dir):
                    os.startfile(output_dir)
            else:
                self.log("❌ 合併失敗。")
                messagebox.showerror("錯誤", "合併失敗，請查看日誌！")
                
        except Exception as e:
            self.log(f"❌ 合併過程中出錯: {str(e)}")
            import traceback
            self.log(f"   {traceback.format_exc()}")
        finally:
            self.finish_processing()
    
    def merge_subtitle_with_ffmpeg(self, video_path, subtitle_path, output_file, vfmt):
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            cmd = [str(ffmpeg_exe), "-y", "-i", str(video_path), "-i", str(subtitle_path)]
            
            if vfmt == "mp4":
                cmd += [
                    "-map", "0:v:0",
                    "-map", "0:a?",
                    "-map", "1:s:0",
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-c:s", "mov_text",
                    "-disposition:s:0", "default",
                    "-metadata:s:s:0", "title=字幕"
                ]
            else:
                cmd += [
                    "-map", "0:v:0",
                    "-map", "0:a?",
                    "-map", "1:s:0",
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-c:s", "srt",
                    "-disposition:s:0", "default",
                    "-metadata:s:s:0", "title=字幕"
                ]
            
            cmd += [str(output_file)]
            
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
            
            # 取得 KTV 顏色設定
            unplayed_color = self.ktv_unplayed_color_var.get().strip() or "#FFFFFF"
            played_color = self.ktv_played_color_var.get().strip() or "#FFFF00"
            border_color = self.ktv_border_color_var.get().strip() or "#000000"
            
            self.log(f"  🎤 正在生成 KTV 逐字變色字幕...")
            self.log(f"    - 未唱顏色: {unplayed_color}")
            self.log(f"    - 已唱顏色: {played_color}")
            self.log(f"    - 邊框顏色: {border_color}")
            
            # 轉換為 ASS 字幕格式（支援逐字變色）
            selected_font = "微軟正黑體"
            if hasattr(self, 'ktv_font_var'):
                selected_font = self.ktv_font_var.get()
            try:
                font_size = int(self.ktv_font_size_var.get())
                if font_size < 10 or font_size > 200:
                    font_size = 60
            except Exception:
                font_size = 60
            self.log(f"    - 字體: {selected_font}  大小: {font_size}pt")
            try:
                margin_v_offset = int(self.subtitle_margin_var.get())
            except Exception:
                margin_v_offset = 0
            ass_file = Path(output_file).parent / f"{Path(json_path).stem}.ass"
            color_mode = "slide" if hasattr(self, 'ktv_color_mode_var') and self.ktv_color_mode_var.get() == "slide" else "char"
            self.log(f"    - 變色模式: {'滑動漸變 (kf)' if color_mode == 'slide' else '逐字變色 (k)'}")
            self._json_to_ass(lyrics_data, str(ass_file), unplayed_color, played_color, border_color, selected_font, margin_v_offset, color_mode, font_size)
            
            # 使用 FFmpeg 燒錄 ASS 字幕到影片
            self.log(f"  🎬 正在使用 FFmpeg 燒錄字幕...")
            return self.burn_ass_subtitle_to_video(video_path, str(ass_file), output_file)
            
        except Exception as e:
            self.log(f"  ❌ JSON 字幕處理失敗: {str(e)}")
            import traceback
            self.log(f"     {traceback.format_exc()}")
            return False
    
    def process_srt_subtitle_to_video(self, video_path, srt_path, output_file):
        """將 SRT 字幕套用字幕樣式後燒錄進影片"""
        try:
            # 取得字幕樣式設定（與 JSON 共用同一組 UI 變數）
            text_color   = self.ktv_unplayed_color_var.get().strip() or "#FFFFFF"
            border_color = self.ktv_border_color_var.get().strip()   or "#000000"
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

            self.log(f"  📝 字幕樣式: 顏色={text_color}  邊框={border_color}  字體={selected_font} {font_size}pt")

            # 把 SRT 轉成帶樣式的 ASS
            ass_file = str(Path(output_file).parent / (Path(srt_path).stem + "_styled.ass"))
            self._srt_to_ass(srt_path, ass_file, text_color, border_color, selected_font, font_size, margin_v_offset)

            # 燒錄 ASS 字幕進影片
            self.log(f"  🎬 正在燒錄字幕樣式...")
            return self.burn_ass_subtitle_to_video(video_path, ass_file, output_file)

        except Exception as e:
            self.log(f"  ❌ SRT 字幕處理失敗: {str(e)}")
            import traceback
            self.log(f"     {traceback.format_exc()}")
            return False

    def _srt_to_ass(self, srt_path, ass_file, text_color, border_color, font_name, font_size, margin_v_offset):
        """將 SRT 字幕轉換為帶樣式的 ASS 格式（單一顏色，無逐字效果）"""
        try:
            def hex_to_bgr(hex_color):
                hex_color = hex_color.lstrip('#')
                if len(hex_color) == 3:
                    hex_color = ''.join([c * 2 for c in hex_color])
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f"&H{b:02X}{g:02X}{r:02X}"

            def srt_time_to_ass(srt_time):
                # 00:00:00,000 → 0:00:00.00
                srt_time = srt_time.strip().replace(',', '.')
                h, m, rest = srt_time.split(':', 2)
                s, ms = rest.split('.')
                cs = int(ms[:3]) // 10
                return f"{int(h)}:{int(m):02d}:{int(s):02d}.{cs:02d}"

            text_bgr   = hex_to_bgr(text_color)
            border_bgr = hex_to_bgr(border_color)
            base_margin_v = 50
            margin_v = max(0, base_margin_v + margin_v_offset)

            # 解析 SRT
            srt_content = Path(srt_path).read_text(encoding='utf-8', errors='ignore')
            block_re = re.compile(
                r'\d+\s*\n'
                r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n'
                r'(.*?)(?=\n\n|\Z)',
                re.DOTALL
            )

            with open(ass_file, 'w', encoding='utf-8-sig') as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n\n")
                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                f.write(f"Style: Default,{font_name},{font_size},{text_bgr},{text_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,10,10,{margin_v},1\n\n")
                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

                for m in block_re.finditer(srt_content):
                    start = srt_time_to_ass(m.group(1))
                    end   = srt_time_to_ass(m.group(2))
                    text  = m.group(3).strip().replace('\n', '\\N')
                    # 移除 HTML 標籤（<i>, <b>, <font>…）
                    text = re.sub(r'<[^>]+>', '', text)
                    f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")

            self.log(f"  ✅ 已生成樣式化 ASS 字幕: {Path(ass_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ SRT 轉 ASS 失敗: {str(e)}")
            import traceback
            self.log(f"     {traceback.format_exc()}")

    def _json_to_srt(self, lyrics_data, srt_file):
        try:
            def format_timestamp(seconds):
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                millis = int((seconds - int(seconds)) * 1000)
                return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
            
            with open(srt_file, 'w', encoding='utf-8-sig') as f:
                if 'segments' in lyrics_data:
                    for i, segment in enumerate(lyrics_data['segments'], 1):
                        start_time = format_timestamp(segment['start'])
                        end_time = format_timestamp(segment['end'])
                        text = segment.get('text', '').strip()
                        if text:
                            f.write(f"{i}\n")
                            f.write(f"{start_time} --> {end_time}\n")
                            f.write(f"{text}\n\n")
            self.log(f"  ✅ 已轉換為 SRT: {Path(srt_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ JSON 轉 SRT 失敗: {str(e)}")
    
    def _json_to_ass(self, lyrics_data, ass_file, unplayed_color, played_color, border_color, font_name="微軟正黑體", margin_v_offset=0, color_mode="char", font_size=60):
        try:
            def hex_to_bgr(hex_color):
                hex_color = hex_color.lstrip('#')
                if len(hex_color) == 3:
                    hex_color = ''.join([c * 2 for c in hex_color])
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f"&H{hex(b)[2:].zfill(2)}{hex(g)[2:].zfill(2)}{hex(r)[2:].zfill(2)}"
            
            def format_ass_time(seconds):
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                centis = int((seconds - int(seconds)) * 100)
                return f"{hours:01d}:{minutes:02d}:{secs:02d}.{centis:02d}"
            
            unplayed_bgr = hex_to_bgr(unplayed_color)
            played_bgr = hex_to_bgr(played_color)
            border_bgr = hex_to_bgr(border_color)

            # 預設 MarginV = 50，正數往上（增加 margin），負數往下（減少 margin，最小 0）
            base_margin_v = 50
            margin_v = max(0, base_margin_v + margin_v_offset)
            
            with open(ass_file, 'w', encoding='utf-8-sig') as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("YCbCr Matrix: TV.709\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n\n")
                
                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                f.write(f"Style: Default,{font_name},{font_size},{played_bgr},{unplayed_bgr},{border_bgr},&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,10,10,{margin_v},1\n\n")
                
                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                
                if 'segments' in lyrics_data:
                    for segment in lyrics_data['segments']:
                        start_time = segment.get('start', 0)
                        end_time = segment.get('end', 0)
                        text = segment.get('text', '').strip()
                        
                        if not text:
                            continue
                        
                        ass_start = format_ass_time(start_time)
                        ass_end = format_ass_time(end_time)
                        
                        words = segment.get('words', [])
                        
                        # 優先使用 words 的逐字時間戳記
                        if words:
                            karaoke_text = ''
                            seg_start = start_time
                            for w in words:
                                w_start = w.get('start', seg_start)
                                w_end   = w.get('end',   w_start)
                                w_text  = w.get('word', w.get('text', '')).strip()
                                if not w_text:
                                    continue
                                # 每個字（或詞）的持續時間，單位 centiseconds
                                w_cs = max(1, int(round((w_end - w_start) * 100)))
                                tag = "kf" if color_mode == "slide" else "k"
                                karaoke_text += f"{{\\{tag}{w_cs}}}{w_text}"
                        else:
                            # 無 words 資料時，退回平均分配
                            chars = list(text)
                            duration = end_time - start_time
                            if duration <= 0:
                                duration = 1.0
                            total_cs = int(round(duration * 100))
                            n = len(chars)
                            base_cs = total_cs // n
                            remainder = total_cs - base_cs * n
                            karaoke_text = ''
                            for i, char in enumerate(chars):
                                char_cs = base_cs + (1 if i >= n - remainder else 0)
                                tag = "kf" if color_mode == "slide" else "k"
                                karaoke_text += f"{{\\{tag}{char_cs}}}{char}"
                        
                        f.write(f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{karaoke_text}\n")
            
            mode_desc = "平滑滑動漸變" if color_mode == "slide" else "逐字變色"
            self.log(f"  ✅ 已生成 KTV {mode_desc}字幕: {Path(ass_file).name}")
        except Exception as e:
            self.log(f"  ⚠️ JSON 轉 ASS 失敗: {str(e)}")
            import traceback
            self.log(f"     {traceback.format_exc()}")
    
    def burn_ass_subtitle_to_video(self, video_path, ass_path, output_file):
        try:
            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            
            import tempfile
            import shutil
            
            temp_dir = tempfile.mkdtemp()
            try:
                temp_video = os.path.join(temp_dir, "video_input" + Path(video_path).suffix)
                temp_ass = os.path.join(temp_dir, "subtitle.ass")
                temp_output = os.path.join(temp_dir, "output" + Path(output_file).suffix)
                
                shutil.copy2(video_path, temp_video)
                shutil.copy2(ass_path, temp_ass)
                
                cmd = [
                    str(ffmpeg_exe),
                    "-y",
                    "-i", temp_video,
                    "-vf", "subtitles=subtitle.ass",
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
            import traceback
            self.log(f"     {traceback.format_exc()}")
            return False
    
    def recognize_lyrics_and_generate_srt(self, audio_file, output_dir, output_stem=None, override_language=None):
        """使用 Whisper 模型識別歌詞並生成 SRT 字幕檔案"""
        import time
        import tempfile
        import shutil
        self.update_status("正在進行 AI 歌詞識別...", "orange")
        self.log("\n🎤 開始 AI 歌詞識別...")
        
        # 如果沒有指定輸出前綴，則使用音訊檔名
        if output_stem is None:
            output_stem = Path(audio_file).stem
        
        try:
            device_val = self.device_var.get()
            if device_val == "gpu":
                device = "cuda"
            elif device_val == "directml":
                device = "directml"
            else:
                device = "cpu"
            
            runtime_ready, actual_device, runtime_lib_dir = self._ensure_runtime_stack_ready(device)
            if not runtime_ready:
                self.log("⚠️ AI 環境就緒失敗，跳過歌詞識別。")
                return None
            
            env = self._build_python_env(runtime_lib_dir, include_gpu_runtime=(actual_device == "cuda"))
            
            runtime_lib_dir_posix = str(runtime_lib_dir).replace("\\", "/")
            common_lib_dir_posix = str(self.common_lib_dir).replace("\\", "/")
            app_bin_dir_posix = str(self.bin_dir).replace("\\", "/")
            app_py_dir_posix = str(self.py_dir).replace("\\", "/")
            whisper_models_dir_posix = str(self.whisper_models_dir).replace("\\", "/")

            # 解析語言代碼（去掉括號說明，例如 "zh (中文)" → "zh"）
            # 優先讀取 Tab 1 專屬設定；若不存在（例如從 Tab 5 呼叫）則讀取 Tab 5 的設定
            if hasattr(self, 'yt_whisper_language_var') and override_language is None:
                raw_lang = self.yt_whisper_language_var.get()
            elif hasattr(self, 'rec_language_var'):
                raw_lang = self.rec_language_var.get()
            else:
                raw_lang = "zh"
            whisper_lang = raw_lang.split()[0] if raw_lang else "zh"

            # 取得模型大小：同樣優先 Tab 1 設定
            if hasattr(self, 'yt_whisper_model_var') and override_language is None:
                whisper_model_size = self.yt_whisper_model_var.get()
            elif hasattr(self, 'whisper_model_var'):
                whisper_model_size = self.whisper_model_var.get()
            else:
                whisper_model_size = "medium"

            self.log(f"  > 語言: {whisper_lang}，模型: {whisper_model_size}")
            
            # Create a temporary file with ASCII name to avoid encoding issues
            temp_dir = tempfile.gettempdir()
            temp_audio_path = os.path.join(temp_dir, "temp_whisper_audio.mp3")
            shutil.copy2(audio_file, temp_audio_path)
            self.log(f"  > 已建立暫存檔案: {temp_audio_path}")
            
            temp_audio_posix = temp_audio_path.replace("\\", "/")
            output_dir_posix = str(output_dir).replace("\\", "/")
            
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

os.environ['WHISPER_MODELS_DIR'] = whisper_models_dir

audio_file = r'{temp_audio_posix}'
output_dir = r'{output_dir_posix}'
original_stem = r'{output_stem}'
whisper_lang = r'{whisper_lang}'
model_size = r'{whisper_model_size}'

# 載入模型
print(f"[INFO] Loading Whisper model: {{model_size}}...")
model = whisper.load_model(model_size, download_root=whisper_models_dir)

print("[INFO] Transcribing audio (silence-based segmentation)...")
import numpy as np

# load audio
audio = whisper.load_audio(audio_file)
audio = whisper.pad_or_trim(audio, length=len(audio))

RATE     = whisper.audio.SAMPLE_RATE   # 16000
MAX_CHUNK = whisper.audio.CHUNK_LENGTH  # 30s max


def find_silence_boundaries(audio, rate,
                             silence_db=-38,    # dB threshold for silence（人聲分離後殘留噪音較低）
                             min_silence=0.4,   # min silence duration (sec)（避免顫音被誤切）
                             min_phrase=1.2,    # min phrase duration (sec)（過短片段通常是噪音）
                             max_phrase=25.0):  # max phrase duration (sec)（縮短減少幻覺機率）
    # 改進版切割:
    # 1. RMS 加 5-frame 移動平均平滑，避免短暫爆音誤判
    # 2. 在靜音區找能量最低點切割（而非靜音中點），切點更自然
    # 3. 強制切割時也找後半段最低點，避免切在強音上
    frame = int(rate * 0.02)          # 20ms analysis frame
    threshold = 10 ** (silence_db / 20)

    # compute RMS per frame
    n_frames = len(audio) // frame
    rms = np.array([
        np.sqrt(np.mean(audio[i*frame:(i+1)*frame] ** 2))
        for i in range(n_frames)
    ])

    # 平滑 RMS：5-frame 移動平均，消除短暫爆音誤判
    smooth_len = 5
    rms_smooth = np.convolve(rms, np.ones(smooth_len) / smooth_len, mode='same')

    is_silence = rms_smooth < threshold
    min_sil_frames = int(min_silence / 0.02)
    min_phr_frames = int(min_phrase  / 0.02)
    max_phr_frames = int(max_phrase  / 0.02)

    boundaries = [0]
    i = 0
    while i < n_frames:

        sil_start = None
        j = i
        while j < n_frames:
            if is_silence[j]:
                if sil_start is None:
                    sil_start = j
                sil_len = j - sil_start + 1

                if sil_len >= min_sil_frames:
                    phrase_len = sil_start - i
                    if phrase_len >= min_phr_frames:
                        # ✨ 改進：在靜音區找能量最低點切割，而非靜音中點
                        sil_region = rms_smooth[sil_start:sil_start + sil_len]
                        cut = sil_start + int(np.argmin(sil_region))
                        boundaries.append(cut)
                        i = cut
                        break
            else:
                sil_start = None

                if j - i >= max_phr_frames:
                    # ✨ 改進：強制切割時在後半段找最低能量點，避免切在強音上
                    region = rms_smooth[i:j]
                    half = len(region) // 2
                    local_min = i + half + int(np.argmin(region[half:]))
                    boundaries.append(local_min)
                    i = local_min
                    break
            j += 1
        else:
            break

    boundaries.append(n_frames)
    # convert to sample index
    return [(boundaries[k] * frame, boundaries[k+1] * frame)
            for k in range(len(boundaries) - 1)
            if (boundaries[k+1] - boundaries[k]) >= min_phr_frames]


print("[INFO] Detecting silence boundaries...")
phrases = find_silence_boundaries(audio, RATE)
print(f"[INFO] Found {{len(phrases)}} phrase segments")

all_segments = []
seg_id = 0
prev_text = ""  # ✨ 改進：記錄前文，讓 Whisper 保持跨句語境連貫

for phrase_idx, (phrase_start, phrase_end) in enumerate(phrases):
    chunk_audio = audio[phrase_start:phrase_end]
    if len(chunk_audio) < RATE // 2:
        continue

    offset_sec = phrase_start / RATE

    # truncate if over Whisper limit
    if len(chunk_audio) > MAX_CHUNK * RATE:
        chunk_audio = chunk_audio[:MAX_CHUNK * RATE]

    # ✨ 改進：首句用引導 prompt，後續句子帶入前文上下文（最近兩句）
    if prev_text:
        prompt = prev_text
    else:
        prompt = "以下是一首中文歌曲的歌詞："

    try:
        chunk_result = model.transcribe(
            chunk_audio,
            language=whisper_lang,
            word_timestamps=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,            # 避免間奏/尾奏產生幻覺
            compression_ratio_threshold=1.8,    # 更積極過濾重複性幻覺
            temperature=(0.0, 0.2, 0.4),        # fallback 溫度，辨識失敗時自動升溫重試
            suppress_blank=True,
            suppress_tokens="-1",
            fp16=False,
            initial_prompt=prompt,              # ✨ 改進：動態前文提示，提升跨句連貫性
        )

        chunk_texts = []
        for seg in chunk_result.get("segments", []):
            text = seg["text"].strip()
            if not text:
                continue

            # 過濾幻覺片段：no_speech_prob 過高代表 Whisper 自己認為沒有人聲
            no_speech_prob = seg.get("no_speech_prob", 0)
            if no_speech_prob > 0.7:
                print(f"[SKIP] no_speech_prob={{no_speech_prob:.2f}} 過高，跳過: {{text}}")
                continue

            # 過濾低信心片段：avg_logprob 過低代表辨識信心很差，通常是幻覺
            avg_logprob = seg.get("avg_logprob", 0)
            if avg_logprob < -1.0:
                print(f"[SKIP] avg_logprob={{avg_logprob:.2f}} 過低，跳過: {{text}}")
                continue

            # ✨ 改進：黑名單過濾 — 過濾版權/製作人員幻覺（Whisper 在純音樂段落常腦補這類文字）
            HALLUCINATION_PATTERNS = [
                "詞曲", "作詞", "作曲", "編曲", "監製", "出品", "版權所有",
                "製作人", "發行", "唱片", "music by", "lyrics by",
                "produced by", "written by",
            ]
            text_lower = text.lower()
            if any(pat in text_lower for pat in HALLUCINATION_PATTERNS):
                print(f"[SKIP] 黑名單幻覺，跳過: {{text}}")
                continue

            # 近重複過濾已移除：歌詞副歌本來就會重複，不應跳過

            seg_id += 1
            all_segments.append({{
                "id":    seg_id,
                "start": seg["start"] + offset_sec,
                "end":   seg["end"]   + offset_sec,
                "text":  text,
                "words": [
                    {{**w, "start": w["start"] + offset_sec,
                           "end":   w["end"]   + offset_sec}}
                    for w in seg.get("words", [])
                ],
            }})
            chunk_texts.append(text)

        # ✨ 改進：更新前文（保留最近兩句，避免 prompt 過長）
        if chunk_texts:
            prev_text = " ".join(chunk_texts[-2:])

    except Exception as e:
        print(f"[WARN] phrase at {{offset_sec:.1f}}s failed: {{e}}")

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

with open(srt_file, 'w', encoding='utf-8-sig') as f:
    for i, segment in enumerate(result['segments'], 1):
        start_time = format_timestamp(segment['start'])
        end_time = format_timestamp(segment['end'])
        text = segment['text'].strip()
        if not text:
            continue
        f.write(str(i) + chr(10))
        f.write(start_time + " --> " + end_time + chr(10))
        f.write(text + chr(10) + chr(10))

print(f"[SUCCESS] {{srt_file}}")
print(f"[JSON] {{output_json}}")
"""

            self.log("  > 正在載入 Whisper 模型（首次使用會自動下載）...")
            start_time = time.time()
            
            process = subprocess.Popen(
                [str(self.local_python), "-c", script],
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
                    # Clean up temp file before exiting
                    if os.path.exists(temp_audio_path):
                        try:
                            os.remove(temp_audio_path)
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
                        # ✨ 模型載入中 → 步驟 4 開始，進度 60%
                        if "Loading Whisper model" in line:
                            self.update_progress(60, "正在識別歌詞",
                                                 step_text="步驟 4/5：載入 Whisper 模型中...")
                        # ✨ 靜音偵測開始 → 進度 65%
                        elif "Transcribing audio" in line or "Detecting silence" in line:
                            self.update_progress(65, "正在識別歌詞",
                                                 step_text="步驟 4/5：偵測句子邊界中...")
                        # ✨ Found N phrase segments → 進度 68%
                        elif "Found" in line and "phrase segments" in line:
                            self.update_progress(68, "正在識別歌詞",
                                                 step_text=f"步驟 4/5：{line.split(chr(93)+chr(32))[-1].strip()}")
                        else:
                            # 逐句辨識進度 [INFO] phrase X/Y
                            m = re.search(r"phrase\s+(\d+)\s*/\s*(\d+)", line)
                            if m:
                                cur, total = int(m.group(1)), int(m.group(2))
                                pct = 70 + int(cur / total * 9)  # 映射到 70–79%
                                self.update_progress(pct, "正在識別歌詞",
                                                     step_text=f"步驟 4/5：AI 歌詞辨識中 {cur}/{total}")
            
            process.wait()
            self._current_process = None
            
            # Clean up the temporary file
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                    self.log("  > 已清除暫存檔案")
                except Exception as e:
                    self.log(f"  ⚠️ 清除暫存檔案失敗: {str(e)}")
            if process.returncode == 0 and srt_output_file:
                # 根據設定轉換歌詞語言（override_language 優先，其次 lyrics_language_var）
                target_lang = override_language if override_language is not None else self.lyrics_language_var.get()
                if target_lang != "original" and json_output_file and os.path.exists(json_output_file):
                    try:
                        import json
                        self.log(f"  > 正在轉換歌詞語言為: {'繁體中文' if target_lang == 'traditional' else '簡體中文'}")
                        
                        # 讀取 JSON
                        with open(json_output_file, 'r', encoding='utf-8') as f:
                            lyrics_data = json.load(f)
                        
                        # 轉換每個段落的歌詞
                        if 'segments' in lyrics_data:
                            for segment in lyrics_data['segments']:
                                if 'text' in segment:
                                    segment['text'] = self.convert_lyrics_language(segment['text'], target_lang)
                                if 'words' in segment:
                                    for word in segment['words']:
                                        if 'word' in word:
                                            word['word'] = self.convert_lyrics_language(word['word'], target_lang)
                        
                        # 重新寫入 JSON
                        with open(json_output_file, 'w', encoding='utf-8') as f:
                            json.dump(lyrics_data, f, ensure_ascii=False, indent=2)
                        
                        # 重新產生 SRT
                        def format_timestamp(seconds):
                            hours = int(seconds // 3600)
                            minutes = int((seconds % 3600) // 60)
                            secs = int(seconds % 60)
                            millis = int((seconds - int(seconds)) * 1000)
                            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
                        
                        with open(srt_output_file, 'w', encoding='utf-8-sig') as f:
                            for i, segment in enumerate(lyrics_data['segments'], 1):
                                seg_start = format_timestamp(segment['start'])
                                seg_end = format_timestamp(segment['end'])
                                text = segment['text'].strip()
                                f.write(f"{i}\n")
                                f.write(f"{seg_start} --> {seg_end}\n")
                                f.write(f"{text}\n\n")
                        
                        self.log("  ✅ 歌詞語言轉換完成")
                    except Exception as e:
                        self.log(f"  ⚠️ 歌詞語言轉換失敗: {str(e)}")
                        import traceback
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
            import traceback
            self.log(f"   {traceback.format_exc()}")
            return None

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioSeparatorApp(root)
    root.mainloop()
