import os
import sys
import json
import time
import math
import uuid
import random
import socket
import struct
import shutil
import hashlib
import threading
import subprocess
import webbrowser
import urllib.request
import tkinter as tk
from tkinter import filedialog
from concurrent.futures import ThreadPoolExecutor, as_completed

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageEnhance, ImageTk
import dns.resolver
import minecraft_launcher_lib as mll

# ==============================================================================
# PATCH DE COMPATIBILIDADE UNIVERSAL TLAUNCHER <-> MOJANG
# ==============================================================================
# O TLauncher modifica arquivos .json em versions/ (como 1.21.1.json e cobble.json)
# trocando a chave padrão Mojang "value" por "values". Este patch em memória garante
# que o minecraft_launcher_lib nunca dê KeyError: 'value' em nenhum PC!
_orig_get_arguments = mll.command.get_arguments
_orig_replace_arguments = mll.command.replace_arguments


def _patched_replace_arguments(argstr, versionData, path, options, classpath):
    res = _orig_replace_arguments(argstr, versionData, path, options, classpath)
    res = res.replace("${clientid}", "0").replace("${auth_xuid}", "0")
    if res == "-Djava.net.preferIPv4Stack=true":
        res = "-Djava.net.preferIPv6Addresses=system"
    return res


def _patched_get_arguments(data, versionData, path, options, classpath):
    normalized = []
    for item in data:
        if isinstance(item, dict) and "value" not in item:
            item = dict(item)
            item["value"] = item.get("values", [])
        normalized.append(item)
    return _orig_get_arguments(normalized, versionData, path, options, classpath)


mll.command.replace_arguments = _patched_replace_arguments
mll.command.get_arguments = _patched_get_arguments

# ==============================================================================
# CONFIGURAÇÕES GLOBAIS E CONSTANTES DO LIMBO LAUNCHER
# ==============================================================================
APP_NAME = "LIMBO LAUNCHER"
APP_VERSION = "1.2.2"
MC_VERSION = "1.21.1"
NEOFORGE_VERSION = "21.1.262"
MODPACK_FOLDER_NAME = "cobble"
JVM_RUNTIME = "java-runtime-delta"  # Java 21 Oficial Mojang/TLauncher
MANIFEST_URL = "https://raw.githubusercontent.com/RanzimzimDev/limbo-modpack/main/manifest.json"
CORE_SCRIPT_URL = "https://raw.githubusercontent.com/RanzimzimDev/limbo-modpack/main/launcher_core.py"
DEFAULT_SERVER_IPV6 = "2804:1468:8816:4e00:dfab:e4cc:6779:9622"
DEFAULT_SERVER_IP = "practically-currently.tun.ply.gg"
SKINS_GALLERY_URL = "https://laby.net/pt/skins?order=trending"

APPDATA_DIR = os.getenv("APPDATA") or os.path.expanduser("~")
LAUNCHER_CONFIG_DIR = os.path.join(APPDATA_DIR, ".tlauncher")
CONFIG_FILE = os.path.join(LAUNCHER_CONFIG_DIR, "limbo_launcher_config.json")
LOG_FILE = os.path.join(LAUNCHER_CONFIG_DIR, "limbo_launcher.log")
LOCAL_CORE_SCRIPT = os.path.join(LAUNCHER_CONFIG_DIR, "limbo_core.py")

# Paleta de Cores (Opção A - Dark Neon Cyberpunk/Ethereal)
COLOR_BG_MAIN = "#0f0f17"
COLOR_SIDEBAR = "#13131f"
COLOR_CARD = "#1b1b2c"
COLOR_CARD_HOVER = "#25253c"
COLOR_PURPLE = "#9d4edd"
COLOR_PURPLE_HOVER = "#7b2cbf"
COLOR_PURPLE_DARK = "#3c096c"
COLOR_CYAN = "#00f5d4"
COLOR_TEXT = "#f8f9fa"
COLOR_TEXT_MUTED = "#9a9ab0"
COLOR_ONLINE = "#00e676"
COLOR_OFFLINE = "#ff5252"


def parse_version_tuple(ver_str: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(ver_str).strip().lstrip("v").split(".") if p.isdigit())
    except Exception:
        return (0, 0, 0)


def resource_path(relative_path: str) -> str:
    """Resolve caminho de assets tanto em desenvolvimento quanto empacotado no PyInstaller."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def detect_tlauncher_minecraft_dir() -> str:
    """
    Detecta automaticamente a pasta .minecraft configurada no TLauncher
    lendo %APPDATA%\\.tlauncher\\tlauncher-2.0.properties (ex: D:\\Minecraft\\.minecraft).
    Caso não encontre, verifica D:\\Minecraft\\.minecraft ou usa %APPDATA%\\.minecraft.
    """
    tl_props = os.path.join(APPDATA_DIR, ".tlauncher", "tlauncher-2.0.properties")
    if os.path.exists(tl_props):
        try:
            with open(tl_props, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("minecraft.gamedir="):
                        raw_path = line.split("=", 1)[1].strip()
                        # Desfazer escape do Java Properties (D\:\\Minecraft\\.minecraft -> D:\Minecraft\.minecraft)
                        clean_path = raw_path.replace("\\:", ":").replace("\\\\", "\\")
                        if os.path.exists(clean_path):
                            return os.path.normpath(clean_path)
        except Exception:
            pass

    if os.path.exists(r"D:\Minecraft\.minecraft"):
        return r"D:\Minecraft\.minecraft"

    return os.path.join(APPDATA_DIR, ".minecraft")


def detect_tlauncher_nick() -> str:
    """Tenta recuperar o último nick usado ou salvo pelo jogador, se disponível."""
    tl_cfg = os.path.join(detect_tlauncher_minecraft_dir(), "TlauncherProfiles.json")
    if os.path.exists(tl_cfg):
        try:
            with open(tl_cfg, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                accs = data.get("accounts", {})
                for _, acc in accs.items():
                    if acc.get("username"):
                        return acc["username"]
        except Exception:
            pass
    return ""


def get_offline_uuid(username: str) -> str:
    """
    Gera exatamente o mesmo UUID Offline (Version 3 MD5) que o servidor Minecraft gera
    com UUID.nameUUIDFromBytes(("OfflinePlayer:" + username).getBytes(UTF_8)).
    Garante que Pokémon da party/PC, inventário e conquistas nunca sejam perdidos.
    """
    data = f"OfflinePlayer:{username}".encode("utf-8")
    md5_bytes = bytearray(hashlib.md5(data).digest())
    md5_bytes[6] = (md5_bytes[6] & 0x0F) | 0x30  # Version 3
    md5_bytes[8] = (md5_bytes[8] & 0x3F) | 0x80  # Variant IETF
    return str(uuid.UUID(bytes=bytes(md5_bytes)))


def sha1_file(filepath: str) -> str:
    h = hashlib.sha1()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def pack_varint(val: int) -> bytes:
    out = b""
    while True:
        b = val & 0x7F
        val >>= 7
        out += struct.pack("B", b | (0x80 if val > 0 else 0))
        if val == 0:
            break
    return out


def resolve_minecraft_srv(host: str) -> tuple[str, int, str]:
    """
    Resolve o registro SRV (_minecraft._tcp.<host>) tentando primeiro o DNS padrão do Windows,
    e caso o roteador da operadora bloqueie/falhe, usa automaticamente Google DNS (8.8.8.8)
    e Cloudflare DNS (1.1.1.1). Suporta IPv6 direto e localhost.
    Retorna: (target_host, port, dns_used)
    """
    clean_host = host.strip("[]")
    if ":" in clean_host or clean_host in ("127.0.0.1", "localhost"):
        return clean_host, 25565, "Conexão Direta"

    dns_candidates = [
        ("DNS Padrão", None),
        ("Google DNS (8.8.8.8)", ["8.8.8.8", "8.8.4.4"]),
        ("Cloudflare DNS (1.1.1.1)", ["1.1.1.1", "1.0.0.1"]),
    ]
    for label, nameservers in dns_candidates:
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = 2.5
            resolver.lifetime = 2.5
            if nameservers:
                resolver.nameservers = nameservers
            srv_records = resolver.resolve(f"_minecraft._tcp.{clean_host}", "SRV")
            for r in srv_records:
                target = str(r.target).rstrip(".")
                port = int(r.port)
                return target, port, label
        except Exception:
            continue
    return clean_host, 25565, "Fallback Direto"


def ping_minecraft_server(host: str) -> tuple[bool, int, int, int]:
    """
    Realiza Server List Ping real (compatível com IPv4, IPv6 e túneis playit.gg)
    Retorna: (online: bool, ping_ms: int, players_online: int, players_max: int)
    """
    target, port, _ = resolve_minecraft_srv(host)

    def read_varint(sock: socket.socket) -> int:
        val, shift = 0, 0
        while True:
            b = sock.recv(1)
            if not b:
                raise EOFError()
            byte = b[0]
            val |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        return val

    try:
        t0 = time.time()
        with socket.create_connection((target, port), timeout=3.0) as s:
            s.settimeout(3.0)
            host_b = host.encode("utf-8")
            handshake = (
                pack_varint(0x00)
                + pack_varint(767)  # Protocolo 1.21.1
                + pack_varint(len(host_b))
                + host_b
                + struct.pack(">H", port)
                + pack_varint(1)
            )
            s.sendall(pack_varint(len(handshake)) + handshake)
            s.sendall(b"\x01\x00")  # Status Request

            _ = read_varint(s)
            packet_id = read_varint(s)
            if packet_id != 0x00:
                return False, 0, 0, 0

            str_len = read_varint(s)
            buf = b""
            while len(buf) < str_len:
                chunk = s.recv(str_len - len(buf))
                if not chunk:
                    break
                buf += chunk

            ping_ms = int((time.time() - t0) * 1000)
            data = json.loads(buf.decode("utf-8"))
            players = data.get("players", {})
            return True, ping_ms, players.get("online", 0), players.get("max", 10)
    except Exception:
        return False, 0, 0, 0


# ==============================================================================
# INTERFACE PRINCIPAL DO LAUNCHER (OPÇÃO A: SIDEBAR + HERO ANIMADO)
# ==============================================================================
class LimboLauncherApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        self.title(f"{APP_NAME} v{APP_VERSION} • Cobblemon {MC_VERSION}")
        self.geometry("1040x620")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG_MAIN)

        # Centralizar janela na tela
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 1040) // 2
        y = (sh - 620) // 2
        self.geometry(f"1040x620+{x}+{y}")

        # Ícone transparente da janela
        icon_path = resource_path(os.path.join("assets", "icon.ico"))
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        os.makedirs(LAUNCHER_CONFIG_DIR, exist_ok=True)
        self.config_data = self.load_config()
        self.mc_root_dir = self.config_data.get("mc_root_dir") or detect_tlauncher_minecraft_dir()
        self.modpack_dir = os.path.join(self.mc_root_dir, "versions", MODPACK_FOLDER_NAME)
        os.makedirs(self.modpack_dir, exist_ok=True)

        self.is_launching = False
        self.is_updating_launcher = False
        self.reload_requested = False
        self.server_is_online = False
        self.server_is_starting = False
        self.local_server_proc = None
        self.force_repair = False
        self.active_tab = "home"
        self.log_buffer = []

        # Estado da animação do Hero Canvas
        self.anim_tick = 0
        self.bg_frames_tk = []
        self.sakura_petals = []
        self.fireflies = []
        self.shooting_star = None

        self.setup_ui()
        self.init_hero_animation()
        self.log(f"Limbo Launcher v{APP_VERSION} iniciado.")
        self.log(f"Pasta .minecraft detectada: {self.mc_root_dir}")
        self.log(f"Pasta do Modpack vinculada: {self.modpack_dir}")
        self.start_server_status_loop()
        self.start_auto_update_check()

    # --------------------------------------------------------------------------
    # Persistência de Configuração Local
    # --------------------------------------------------------------------------
    def load_config(self) -> dict:
        default_mc = detect_tlauncher_minecraft_dir()
        default_nick = detect_tlauncher_nick()
        defaults = {
            "username": default_nick,
            "ram_gb": 6,
            "auto_connect": False,
            "mc_root_dir": default_mc,
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    defaults.update(saved)
            except Exception:
                pass
        return defaults

    def save_config(self):
        if hasattr(self, "nick_entry"):
            self.config_data["username"] = self.nick_entry.get().strip()
        if hasattr(self, "ram_slider"):
            self.config_data["ram_gb"] = int(round(self.ram_slider.get()))
        if hasattr(self, "auto_connect_var"):
            self.config_data["auto_connect"] = bool(self.auto_connect_var.get())
        self.config_data["mc_root_dir"] = self.mc_root_dir
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # Construção do Layout (Sidebar Esquerda + Hero Canvas Animado Direita)
    # --------------------------------------------------------------------------
    def setup_ui(self):
        self.grid_columnconfigure(0, weight=0)  # Sidebar (270px)
        self.grid_columnconfigure(1, weight=1)  # Main Area (770px)
        self.grid_rowconfigure(0, weight=1)

        # ==================== 1. BARRA LATERAL (SIDEBAR) ====================
        self.sidebar = ctk.CTkFrame(
            self, width=270, corner_radius=0, fg_color=COLOR_SIDEBAR
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # Cabeçalho com Logo Transparente Limbo
        header_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(22, 16))

        logo_path = resource_path(os.path.join("assets", "logo.png"))
        if os.path.exists(logo_path):
            raw_logo = Image.open(logo_path).convert("RGBA").resize((62, 62), Image.Resampling.LANCZOS)
            # Compor sobre a cor exata da sidebar (#13131f) para bordas circulares 100% suaves sem fundo branco
            bg_patch = Image.new("RGBA", (62, 62), (19, 19, 31, 255))
            composited_logo = Image.alpha_composite(bg_patch, raw_logo)
            self.logo_img = ctk.CTkImage(light_image=composited_logo, dark_image=composited_logo, size=(62, 62))
            logo_lbl = ctk.CTkLabel(header_frame, image=self.logo_img, text="", fg_color="transparent")
            logo_lbl.pack(side="left", padx=(0, 12))

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            title_box,
            text="LIMBO",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            title_box,
            text="COBBLEMON 1.21.1",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=COLOR_CYAN,
            anchor="w",
        ).pack(fill="x")

        # Card de Perfil Pirata (Offline Mode)
        profile_card = ctk.CTkFrame(
            self.sidebar,
            fg_color=COLOR_CARD,
            corner_radius=12,
            border_width=1,
            border_color="#2d2d44",
        )
        profile_card.pack(fill="x", padx=18, pady=(2, 18))

        ctk.CTkLabel(
            profile_card,
            text="👤 PERFIL DO TREINADOR (OFFLINE)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=COLOR_CYAN,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 6))

        self.nick_entry = ctk.CTkEntry(
            profile_card,
            placeholder_text="Digite seu Nick...",
            height=38,
            corner_radius=8,
            fg_color="#10101a",
            border_color=COLOR_PURPLE,
            border_width=2,
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
        )
        self.nick_entry.pack(fill="x", padx=14, pady=(0, 6))
        if self.config_data.get("username"):
            self.nick_entry.insert(0, self.config_data["username"])
        self.nick_entry.bind("<KeyRelease>", self.on_nick_change)

        self.uuid_label = ctk.CTkLabel(
            profile_card,
            text=self.get_uuid_preview_text(),
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.uuid_label.pack(fill="x", padx=14, pady=(0, 10))

        # Menu de Navegação
        ctk.CTkLabel(
            self.sidebar,
            text="NAVEGAÇÃO",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=24, pady=(2, 4))

        self.btn_nav_home = ctk.CTkButton(
            self.sidebar,
            text="🎮   Início / Jogar",
            anchor="w",
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLOR_PURPLE,
            hover_color=COLOR_PURPLE_HOVER,
            command=lambda: self.switch_tab("home"),
        )
        self.btn_nav_home.pack(fill="x", padx=18, pady=2)

        self.btn_start_server = ctk.CTkButton(
            self.sidebar,
            text="🚀   Ligar Servidor",
            anchor="w",
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="#1b3a2b",
            text_color=COLOR_ONLINE,
            border_width=1,
            border_color=COLOR_ONLINE,
            hover_color="#234d39",
            command=self.on_start_server_clicked,
        )
        self.btn_start_server.pack(fill="x", padx=18, pady=2)

        self.btn_nav_settings = ctk.CTkButton(
            self.sidebar,
            text="⚙️   Configurações & RAM",
            anchor="w",
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_CARD_HOVER,
            command=lambda: self.switch_tab("settings"),
        )
        self.btn_nav_settings.pack(fill="x", padx=18, pady=2)

        self.btn_nav_logs = ctk.CTkButton(
            self.sidebar,
            text="📋   Logs / Diagnóstico",
            anchor="w",
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_CARD_HOVER,
            command=lambda: self.switch_tab("logs"),
        )
        self.btn_nav_logs.pack(fill="x", padx=18, pady=2)

        self.btn_update_launcher = ctk.CTkButton(
            self.sidebar,
            text="🔄   Atualizar Launcher",
            anchor="w",
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="transparent",
            text_color=COLOR_CYAN,
            hover_color=COLOR_CARD_HOVER,
            command=self.on_update_launcher_clicked,
        )
        self.btn_update_launcher.pack(fill="x", padx=18, pady=2)

        self.btn_nav_skins = ctk.CTkButton(
            self.sidebar,
            text="🎨   Galeria de Skins (Laby.net)",
            anchor="w",
            height=34,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_CARD_HOVER,
            command=self.open_skins_gallery,
        )
        self.btn_nav_skins.pack(fill="x", padx=18, pady=2)

        self.btn_nav_folder = ctk.CTkButton(
            self.sidebar,
            text="📂   Abrir Pasta do Modpack",
            anchor="w",
            height=34,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_CARD_HOVER,
            command=self.open_game_folder,
        )
        self.btn_nav_folder.pack(fill="x", padx=18, pady=2)

        # Rodapé da Sidebar
        footer_card = ctk.CTkFrame(
            self.sidebar, fg_color="#181826", corner_radius=10
        )
        footer_card.pack(side="bottom", fill="x", padx=18, pady=12)
        ctk.CTkLabel(
            footer_card,
            text=f"💡 Limbo Launcher v{APP_VERSION}",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=COLOR_CYAN,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(6, 1))
        ctk.CTkLabel(
            footer_card,
            text="Use /skin no Discord do servidor\npara trocar sua skin no jogo!",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLOR_TEXT_MUTED,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 6))

        # ==================== 2. ÁREA CENTRAL + RODAPÉ ====================
        self.main_area = ctk.CTkFrame(
            self, corner_radius=0, fg_color=COLOR_BG_MAIN
        )
        self.main_area.grid(row=0, column=1, sticky="nsew")
        self.main_area.grid_rowconfigure(0, weight=1)  # Hero Canvas (485px)
        self.main_area.grid_rowconfigure(1, weight=0)  # Bottom Bar (135px)
        self.main_area.grid_columnconfigure(0, weight=1)

        # ---- 2A. HERO CANVAS ANIMADO (770x485) ----
        self.hero_container = ctk.CTkFrame(
            self.main_area, corner_radius=0, fg_color=COLOR_BG_MAIN
        )
        self.hero_container.grid(row=0, column=0, sticky="nsew")

        self.hero_canvas = tk.Canvas(
            self.hero_container,
            width=770,
            height=485,
            bg=COLOR_BG_MAIN,
            highlightthickness=0,
            bd=0,
        )
        self.hero_canvas.place(x=0, y=0, width=770, height=485)

        # Painel de Configurações (visível quando a aba "settings" está selecionada)
        self.settings_panel = ctk.CTkFrame(
            self.hero_container,
            fg_color="#141424",
            corner_radius=16,
            border_width=2,
            border_color=COLOR_PURPLE,
            width=700,
            height=415,
        )
        self.settings_panel.pack_propagate(False)

        ctk.CTkLabel(
            self.settings_panel,
            text="⚙️  CONFIGURAÇÕES DE DESEMPENHO E PASTA DO TLAUNCHER",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=COLOR_CYAN,
            anchor="w",
        ).pack(fill="x", padx=24, pady=(18, 10))

        # Controle de Memória RAM
        ram_frame = ctk.CTkFrame(self.settings_panel, fg_color=COLOR_CARD, corner_radius=12)
        ram_frame.pack(fill="x", padx=24, pady=6)

        ram_top = ctk.CTkFrame(ram_frame, fg_color="transparent")
        ram_top.pack(fill="x", padx=16, pady=(10, 4))

        ctk.CTkLabel(
            ram_top,
            text="Memória RAM Alocada para o Minecraft:",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(side="left")

        self.ram_value_label = ctk.CTkLabel(
            ram_top,
            text=f"{self.config_data.get('ram_gb', 6)} GB (Recomendado: 6 a 8 GB)",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=COLOR_CYAN,
        )
        self.ram_value_label.pack(side="right")

        self.ram_slider = ctk.CTkSlider(
            ram_frame,
            from_=4,
            to=16,
            number_of_steps=12,
            button_color=COLOR_CYAN,
            button_hover_color=COLOR_PURPLE,
            progress_color=COLOR_PURPLE,
            command=self.on_ram_change,
        )
        self.ram_slider.set(self.config_data.get("ram_gb", 6))
        self.ram_slider.pack(fill="x", padx=16, pady=(4, 12))

        # Configuração da Pasta .minecraft / TLauncher versions
        dir_frame = ctk.CTkFrame(self.settings_panel, fg_color=COLOR_CARD, corner_radius=12)
        dir_frame.pack(fill="x", padx=24, pady=6)

        ctk.CTkLabel(
            dir_frame,
            text="📁 Pasta do Modpack (Integrada ao .minecraft\\versions do TLauncher):",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLOR_CYAN,
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 4))

        dir_row = ctk.CTkFrame(dir_frame, fg_color="transparent")
        dir_row.pack(fill="x", padx=16, pady=(0, 12))

        self.dir_path_label = ctk.CTkLabel(
            dir_row,
            text=self.modpack_dir,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLOR_TEXT,
            fg_color="#10101a",
            corner_radius=6,
            padx=10,
            pady=6,
            anchor="w",
        )
        self.dir_path_label.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            dir_row,
            text="Alterar .minecraft",
            width=130,
            height=32,
            corner_radius=8,
            fg_color=COLOR_PURPLE,
            hover_color=COLOR_PURPLE_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.choose_minecraft_dir,
        ).pack(side="right")

        # Opções adicionais
        opts_frame = ctk.CTkFrame(self.settings_panel, fg_color=COLOR_CARD, corner_radius=12)
        opts_frame.pack(fill="x", padx=24, pady=6)

        self.auto_connect_var = ctk.BooleanVar(value=self.config_data.get("auto_connect", False))
        self.chk_auto_connect = ctk.CTkCheckBox(
            opts_frame,
            text=f"Entrar direto no servidor ({DEFAULT_SERVER_IP}) ao abrir o Minecraft",
            variable=self.auto_connect_var,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color=COLOR_PURPLE,
            hover_color=COLOR_PURPLE_HOVER,
            command=self.save_config,
        )
        self.chk_auto_connect.pack(anchor="w", padx=16, pady=(12, 12))

        # Botão de Reparo / Forçar Reinstalação
        repair_frame = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        repair_frame.pack(fill="x", padx=24, pady=(8, 12))

        self.btn_repair = ctk.CTkButton(
            repair_frame,
            text="🔧 Forçar Re-verificação Completa (NeoForge + Mods + Configs)",
            height=36,
            corner_radius=8,
            fg_color="#2b2b44",
            hover_color=COLOR_PURPLE_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.trigger_force_repair,
        )
        self.btn_repair.pack(side="left")

        # Painel de Logs / Diagnóstico (visível quando a aba "logs" está selecionada)
        self.logs_panel = ctk.CTkFrame(
            self.hero_container,
            fg_color="#141424",
            corner_radius=16,
            border_width=2,
            border_color=COLOR_CYAN,
            width=700,
            height=415,
        )
        self.logs_panel.pack_propagate(False)

        logs_header = ctk.CTkFrame(self.logs_panel, fg_color="transparent")
        logs_header.pack(fill="x", padx=20, pady=(14, 8))

        ctk.CTkLabel(
            logs_header,
            text="📋  LOGS DE DIAGNÓSTICO E REDE (TEMPO REAL)",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=COLOR_CYAN,
            anchor="w",
        ).pack(side="left")

        ctk.CTkButton(
            logs_header,
            text="🗑️ Limpar",
            width=80,
            height=28,
            corner_radius=6,
            fg_color="#2b2b44",
            hover_color="#3e3e5e",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self.clear_logs,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            logs_header,
            text="📂 Abrir .log",
            width=95,
            height=28,
            corner_radius=6,
            fg_color="#2b2b44",
            hover_color="#3e3e5e",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self.open_log_file,
        ).pack(side="right", padx=(6, 0))

        self.btn_copy_logs = ctk.CTkButton(
            logs_header,
            text="📋 Copiar Logs",
            width=115,
            height=28,
            corner_radius=6,
            fg_color=COLOR_PURPLE,
            hover_color=COLOR_PURPLE_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self.copy_logs_to_clipboard,
        )
        self.btn_copy_logs.pack(side="right")

        self.logs_textbox = ctk.CTkTextbox(
            self.logs_panel,
            fg_color="#0b0b12",
            text_color="#d8f3dc",
            border_width=1,
            border_color="#25253c",
            corner_radius=10,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self.logs_textbox.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.logs_textbox.configure(state="disabled")

        # ---- 2B. BARRA INFERIOR DE CONTROLE E BOTÃO JOGAR ----
        self.bottom_bar = ctk.CTkFrame(
            self.main_area, height=135, corner_radius=0, fg_color="#12121e"
        )
        self.bottom_bar.grid(row=1, column=0, sticky="nsew")
        self.bottom_bar.grid_propagate(False)
        self.bottom_bar.grid_columnconfigure(0, weight=1)
        self.bottom_bar.grid_columnconfigure(1, weight=0)

        # Área de Progresso (Esquerda)
        prog_frame = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        prog_frame.grid(row=0, column=0, sticky="nsew", padx=(26, 16), pady=22)

        self.status_title = ctk.CTkLabel(
            prog_frame,
            text="Pronto para iniciar sua jornada!",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
        )
        self.status_title.pack(fill="x", pady=(2, 2))

        self.status_detail = ctk.CTkLabel(
            prog_frame,
            text=f"Modpack vinculado em: {self.modpack_dir}",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.status_detail.pack(fill="x", pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(
            prog_frame,
            height=12,
            corner_radius=6,
            fg_color="#1f1f33",
            progress_color=COLOR_CYAN,
        )
        self.progress_bar.set(1.0)
        self.progress_bar.pack(fill="x")

        # Botão Gigante JOGAR (Direita)
        self.play_btn = ctk.CTkButton(
            self.bottom_bar,
            text="▶  JOGAR",
            width=220,
            height=72,
            corner_radius=14,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            fg_color=COLOR_PURPLE,
            hover_color=COLOR_PURPLE_HOVER,
            text_color="#ffffff",
            border_width=2,
            border_color=COLOR_CYAN,
            command=self.on_play_clicked,
        )
        self.play_btn.grid(row=0, column=1, padx=(8, 26), pady=22)

    # --------------------------------------------------------------------------
    # Motor de Animação do Hero Banner (Aurora + Pétalas de Sakura + Vagalumes)
    # --------------------------------------------------------------------------
    def init_hero_animation(self):
        bg_path = resource_path(os.path.join("assets", "hero_bg.png"))
        if not os.path.exists(bg_path):
            return

        base_img = (
            Image.open(bg_path)
            .convert("RGBA")
            .resize((770, 485), Image.Resampling.LANCZOS)
        )

        # Criar versão com aurora/lanternas iluminadas para efeito de pulsação suave
        glow_img = ImageEnhance.Brightness(base_img).enhance(1.18)
        glow_img = ImageEnhance.Color(glow_img).enhance(1.30)

        # Desenhar os cards Glassmorphism translúcidos diretamente nas camadas RGBA
        # (Zero cantos quadrados e transparência real!)
        def apply_glass_overlays(img: Image.Image, pulse_ratio: float) -> Image.Image:
            overlay = Image.new("RGBA", (770, 485), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            # 1. Badge Superior Esquerdo (NeoForge)
            draw.rounded_rectangle(
                (22, 20, 315, 52),
                radius=10,
                fill=(15, 15, 28, 195),
                outline=(0, 245, 212, int(150 + 80 * pulse_ratio)),
                width=1,
            )

            # 2. Badge Superior Direito (Status do Servidor)
            draw.rounded_rectangle(
                (400, 20, 748, 52),
                radius=10,
                fill=(15, 15, 28, 205),
                outline=(157, 78, 221, int(160 + 85 * pulse_ratio)),
                width=1,
            )

            # 3. Card Glassmorphism de Boas-Vindas (Inferior Esquerdo - largura 545px sem cortar texto!)
            draw.rounded_rectangle(
                (22, 326, 568, 462),
                radius=14,
                fill=(14, 14, 26, 205),
                outline=(157, 78, 221, int(165 + 85 * pulse_ratio)),
                width=2,
            )
            return Image.alpha_composite(img, overlay).convert("RGB")

        # Pré-renderizar 24 quadros suaves do ciclo de respiração da Aurora/Lanternas
        for i in range(24):
            alpha = (math.sin(i * 2 * math.pi / 24) + 1.0) / 2.0
            blended = Image.blend(base_img, glow_img, alpha)
            framed_rgb = apply_glass_overlays(blended, alpha)
            self.bg_frames_tk.append(ImageTk.PhotoImage(framed_rgb))

        self.canvas_bg_id = self.hero_canvas.create_image(
            0, 0, image=self.bg_frames_tk[0], anchor="nw"
        )

        # Inicializar 36 Pétalas de Cerejeira (Sakura) caindo pelo vale
        sakura_colors = ["#ffb7c5", "#ff8fab", "#f48fb1", "#e0aaff", "#ffc2d1"]
        for _ in range(36):
            x = random.uniform(0, 770)
            y = random.uniform(0, 485)
            size = random.uniform(2.5, 5.5)
            speed_y = random.uniform(0.6, 1.5)
            speed_x = random.uniform(-0.8, -0.2)
            phase = random.uniform(0, 6.28)
            color = random.choice(sakura_colors)
            pid = self.hero_canvas.create_oval(
                x, y, x + size * 1.4, y + size, fill=color, outline=""
            )
            self.sakura_petals.append({
                "id": pid,
                "x": x,
                "y": y,
                "size": size,
                "vy": speed_y,
                "vx": speed_x,
                "phase": phase,
            })

        # Inicializar 24 Vagalumes / Orbes de Energia Limbo (Ciano & Roxo) subindo suavemente
        orb_colors = [
            ("#00f5d4", "#008b76"),
            ("#c77dff", "#5a189a"),
            ("#90e0ef", "#0077b6"),
        ]
        for _ in range(24):
            x = random.uniform(20, 750)
            y = random.uniform(60, 470)
            r = random.uniform(1.8, 3.6)
            vy = random.uniform(-0.55, -0.20)
            phase = random.uniform(0, 6.28)
            core_col, halo_col = random.choice(orb_colors)
            hid = self.hero_canvas.create_oval(
                x - r * 2, y - r * 2, x + r * 2, y + r * 2, fill="", outline=halo_col
            )
            cid = self.hero_canvas.create_oval(
                x - r, y - r, x + r, y + r, fill=core_col, outline=""
            )
            self.fireflies.append({
                "cid": cid,
                "hid": hid,
                "x": x,
                "y": y,
                "r": r,
                "vy": vy,
                "phase": phase,
            })

        # Linha para estrela cadente no céu
        self.shooting_star_id = self.hero_canvas.create_line(
            -10, -10, -10, -10, fill="#00f5d4", width=2, capstyle="round"
        )

        # Textos Nativos Nítidos sobre os Cards Glassmorphism
        self.hero_canvas.create_text(
            36,
            36,
            text=f"⚡ NEOFORGE {NEOFORGE_VERSION}   •   AUTO-SYNC ATIVO",
            fill=COLOR_CYAN,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )

        # Texto interativo de Status do Servidor (Canto Superior Direito)
        self.server_status_dot_id = self.hero_canvas.create_oval(
            416, 31, 426, 41, fill="#ffb703", outline=""
        )
        self.server_status_text_id = self.hero_canvas.create_text(
            434,
            36,
            text=f"VERIFICANDO SERVIDOR • {DEFAULT_SERVER_IP}",
            fill=COLOR_TEXT,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )

        # Zona clicável para copiar IP do servidor no canto superior direito
        self.hero_canvas.tag_bind(
            self.server_status_text_id, "<Button-1>", lambda _e: self.copy_server_ip()
        )
        self.hero_canvas.bind("<Motion>", self.on_canvas_motion)
        self.hero_canvas.bind("<Button-1>", self.on_canvas_click)

        # Textos do Card de Boas-Vindas (Inferior Esquerdo - sem corte!)
        self.hero_canvas.create_text(
            40,
            352,
            text="✨ BEM-VINDO AO SERVIDOR LIMBO COBBLEMON",
            fill=COLOR_CYAN,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        )
        self.hero_canvas.create_text(
            40,
            382,
            text="• Modpack sincronizado automaticamente via Nuvem (GitHub + Modrinth)",
            fill=COLOR_TEXT,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.hero_canvas.create_text(
            40,
            406,
            text="• Applied Energistics 2, Oritech, Mega Showdown, Lendários & Shaders",
            fill=COLOR_TEXT,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.hero_canvas.create_text(
            40,
            430,
            text="• Integrado direto à pasta .minecraft\\versions\\cobble do seu TLauncher!",
            fill="#d8b4fe",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )

        # Iniciar loop de 30 FPS
        self.animate_hero()

    def animate_hero(self):
        self.anim_tick += 1

        # 1. Alternar quadro de iluminação da Aurora e Lanternas (suave a cada 2 ticks)
        if self.bg_frames_tk:
            frame_idx = (self.anim_tick // 2) % len(self.bg_frames_tk)
            self.hero_canvas.itemconfig(self.canvas_bg_id, image=self.bg_frames_tk[frame_idx])

        t = self.anim_tick * 0.06

        # 2. Animar Pétalas de Cerejeira (Sakura) com brisa senoidal
        for p in self.sakura_petals:
            sway = math.sin(t + p["phase"]) * 0.65
            p["x"] += p["vx"] + sway
            p["y"] += p["vy"]

            if p["y"] > 490:
                p["y"] = -8
                p["x"] = random.uniform(40, 820)
            if p["x"] < -15:
                p["x"] = 778
            elif p["x"] > 785:
                p["x"] = -8

            s = p["size"]
            # Leve rotação aparente mudando proporção oval
            stretch = 1.0 + 0.45 * math.cos(t * 1.5 + p["phase"])
            self.hero_canvas.coords(
                p["id"],
                p["x"],
                p["y"],
                p["x"] + s * stretch,
                p["y"] + s,
            )

        # 3. Animar Vagalumes / Orbes Etéreos do Limbo
        for f in self.fireflies:
            drift = math.cos(t * 0.8 + f["phase"]) * 0.4
            f["x"] += drift
            f["y"] += f["vy"]

            if f["y"] < 40:
                f["y"] = 475
                f["x"] = random.uniform(30, 740)

            pulse = 1.0 + 0.35 * math.sin(t * 2.0 + f["phase"])
            r = f["r"] * pulse
            x, y = f["x"], f["y"]
            self.hero_canvas.coords(f["cid"], x - r, y - r, x + r, y + r)
            self.hero_canvas.coords(
                f["hid"], x - r * 2.1, y - r * 2.1, x + r * 2.1, y + r * 2.1
            )

        # 4. Estrela Cadente ocasional no céu estrelado
        if self.shooting_star is None:
            if random.random() < 0.012:
                self.shooting_star = {
                    "x": random.uniform(180, 680),
                    "y": random.uniform(15, 110),
                    "vx": -14.0,
                    "vy": 5.5,
                    "life": 0,
                    "max_life": 16,
                }
        else:
            st = self.shooting_star
            st["x"] += st["vx"]
            st["y"] += st["vy"]
            st["life"] += 1
            if st["life"] >= st["max_life"]:
                self.hero_canvas.coords(self.shooting_star_id, -10, -10, -10, -10)
                self.shooting_star = None
            else:
                tail_x = st["x"] - st["vx"] * 2.4
                tail_y = st["y"] - st["vy"] * 2.4
                self.hero_canvas.coords(
                    self.shooting_star_id, st["x"], st["y"], tail_x, tail_y
                )

        self.after(33, self.animate_hero)

    def on_canvas_motion(self, event):
        if 400 <= event.x <= 748 and 20 <= event.y <= 52:
            self.hero_canvas.config(cursor="hand2")
        else:
            self.hero_canvas.config(cursor="")

    def on_canvas_click(self, event):
        if 400 <= event.x <= 748 and 20 <= event.y <= 52:
            self.copy_server_ip()

    # --------------------------------------------------------------------------
    # Eventos e Navegação da UI
    # --------------------------------------------------------------------------
    def get_uuid_preview_text(self) -> str:
        nick = (
            self.nick_entry.get().strip()
            if hasattr(self, "nick_entry")
            else self.config_data.get("username", "")
        )
        if not nick:
            return "UUID Offline: aguardando nick..."
        u = get_offline_uuid(nick)
        return f"UUID: {u[:18]}..."

    def on_nick_change(self, _event=None):
        self.uuid_label.configure(text=self.get_uuid_preview_text())
        self.save_config()

    def on_ram_change(self, val):
        gb = int(round(val))
        self.ram_value_label.configure(text=f"{gb} GB (Recomendado: 6 a 8 GB)")
        self.save_config()

    def choose_minecraft_dir(self):
        chosen = filedialog.askdirectory(
            title="Selecione a pasta .minecraft do seu TLauncher",
            initialdir=self.mc_root_dir,
        )
        if chosen:
            chosen = os.path.normpath(chosen)
            # Se o usuário selecionou a pasta 'cobble' ou 'versions' por engano, subir para a raiz .minecraft
            if os.path.basename(chosen).lower() == MODPACK_FOLDER_NAME.lower():
                chosen = os.path.dirname(os.path.dirname(chosen))
            elif os.path.basename(chosen).lower() == "versions":
                chosen = os.path.dirname(chosen)

            self.mc_root_dir = chosen
            self.modpack_dir = os.path.join(self.mc_root_dir, "versions", MODPACK_FOLDER_NAME)
            os.makedirs(self.modpack_dir, exist_ok=True)
            self.dir_path_label.configure(text=self.modpack_dir)
            self.status_detail.configure(text=f"Modpack vinculado em: {self.modpack_dir}")
            self.save_config()

    def switch_tab(self, tab_name: str):
        self.active_tab = tab_name
        self.btn_nav_home.configure(
            fg_color=COLOR_PURPLE if tab_name == "home" else "transparent"
        )
        self.btn_nav_settings.configure(
            fg_color=COLOR_PURPLE if tab_name == "settings" else "transparent"
        )
        self.btn_nav_logs.configure(
            fg_color=COLOR_PURPLE if tab_name == "logs" else "transparent"
        )

        self.settings_panel.place_forget()
        self.logs_panel.place_forget()

        if tab_name == "settings":
            self.settings_panel.place(x=35, y=35)
        elif tab_name == "logs":
            self.logs_panel.place(x=35, y=35)

    # --------------------------------------------------------------------------
    # Sistema de Logs de Diagnóstico em Tempo Real
    # --------------------------------------------------------------------------
    def log(self, message: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self.log_buffer.append(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

        def _append_ui():
            if hasattr(self, "logs_textbox"):
                self.logs_textbox.configure(state="normal")
                self.logs_textbox.insert("end", line)
                self.logs_textbox.see("end")
                self.logs_textbox.configure(state="disabled")

        self.after(0, _append_ui)

    def clear_logs(self):
        self.log_buffer.clear()
        if hasattr(self, "logs_textbox"):
            self.logs_textbox.configure(state="normal")
            self.logs_textbox.delete("1.0", "end")
            self.logs_textbox.configure(state="disabled")
        self.log("Logs limpos pelo usuário.")

    def open_log_file(self):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            self.log("Arquivo de log ainda não foi criado.")

    def copy_logs_to_clipboard(self):
        full_text = "".join(self.log_buffer)
        self.clipboard_clear()
        self.clipboard_append(full_text)
        self.btn_copy_logs.configure(text="✅ Copiado!", fg_color="#1b4332")
        self.after(
            2000,
            lambda: self.btn_copy_logs.configure(
                text="📋 Copiar Logs", fg_color=COLOR_PURPLE
            ),
        )

    # --------------------------------------------------------------------------
    # Auto-Updater do Limbo Launcher (.exe) via GitHub
    # --------------------------------------------------------------------------
    def start_auto_update_check(self):
        threading.Thread(
            target=self._check_launcher_update_worker, args=(False,), daemon=True
        ).start()

    def on_update_launcher_clicked(self):
        if self.is_updating_launcher or self.is_launching:
            return
        self.log("[UPDATER] Verificação manual de atualização do Launcher solicitada.")
        threading.Thread(
            target=self._check_launcher_update_worker, args=(True,), daemon=True
        ).start()

    def _check_launcher_update_worker(self, manual: bool = False):
        try:
            if manual:
                self.set_ui_status(
                    "🔄 Verificando atualizações do Limbo Launcher...",
                    "Consultando última versão publicada no GitHub...",
                    0.2,
                )

            req = urllib.request.Request(
                f"{MANIFEST_URL}?t={int(time.time())}",
                headers={"User-Agent": f"LimboLauncher/{APP_VERSION}", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                manifest = json.loads(resp.read().decode("utf-8"))

            remote_ver = manifest.get("launcher_version", APP_VERSION)
            remote_script_sha = manifest.get("launcher_script_sha1", "")
            script_url = manifest.get("launcher_script_url") or CORE_SCRIPT_URL

            local_script_sha = (
                sha1_file(LOCAL_CORE_SCRIPT) if os.path.isfile(LOCAL_CORE_SCRIPT) else ""
            )

            needs_update = False
            if parse_version_tuple(remote_ver) > parse_version_tuple(APP_VERSION):
                needs_update = True
                self.log(
                    f"[UPDATER] Nova versão detectada! Local=v{APP_VERSION} | GitHub=v{remote_ver}"
                )
            elif remote_script_sha and local_script_sha and remote_script_sha.lower() != local_script_sha.lower():
                needs_update = True
                self.log(
                    f"[UPDATER] Nova atualização de código detectada! Local={local_script_sha[:8]} | GitHub={remote_script_sha[:8]}"
                )
            else:
                self.log(f"[UPDATER] Launcher está na versão mais recente (v{APP_VERSION}).")

            if not needs_update and not manual:
                return

            if not needs_update and manual:
                # Se clicou manualmente e ainda não tinha o script local sincronizado, verificar se o hash difere
                if remote_script_sha and not local_script_sha:
                    self._perform_self_update(script_url, remote_ver, remote_script_sha, silent_if_same=True)
                    return
                self.set_ui_status(
                    f"✅ Seu Limbo Launcher já está atualizado (v{APP_VERSION})!",
                    "Nenhuma atualização pendente no GitHub.",
                    1.0,
                )
                return

            self._perform_self_update(script_url, remote_ver, remote_script_sha)

        except Exception as e:
            self.log(f"[UPDATER] Erro ao verificar atualização do Launcher: {e}")
            if manual:
                self.set_ui_status(
                    "❌ Erro ao verificar atualização do Launcher:",
                    str(e)[:90],
                    0.0,
                )

    def _perform_self_update(self, script_url: str, remote_ver: str, expected_sha: str = "", silent_if_same: bool = False):
        if self.is_updating_launcher:
            return
        self.is_updating_launcher = True
        self.after(
            0,
            lambda: self.btn_update_launcher.configure(
                text="⏳   Atualizando...", fg_color="#3c096c", text_color=COLOR_CYAN
            ),
        )
        try:
            os.makedirs(LAUNCHER_CONFIG_DIR, exist_ok=True)
            tmp_script = LOCAL_CORE_SCRIPT + ".tmp"

            self.log(f"[UPDATER] Sincronizando núcleo do Launcher (v{remote_ver}) de: {script_url}")
            self.set_ui_status(
                f"⬇️ Atualizando Limbo Launcher para v{remote_ver}...",
                "Baixando módulo de atualização instantânea do GitHub...",
                0.5,
            )

            req = urllib.request.Request(
                f"{script_url}?t={int(time.time())}",
                headers={"User-Agent": f"LimboLauncher/{APP_VERSION}", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()

            text_code = data.decode("utf-8")
            if "class LimboLauncherApp" not in text_code:
                raise RuntimeError("Módulo baixado inválido.")

            with open(tmp_script, "wb") as out_f:
                out_f.write(data)
            os.replace(tmp_script, LOCAL_CORE_SCRIPT)

            if silent_if_same and f'APP_VERSION = "{APP_VERSION}"' in text_code:
                self.is_updating_launcher = False
                self.after(
                    0,
                    lambda: self.btn_update_launcher.configure(
                        text="🔄   Atualizar Launcher", fg_color="transparent", text_color=COLOR_CYAN
                    ),
                )
                self.set_ui_status(
                    f"✅ Seu Limbo Launcher já está atualizado (v{APP_VERSION})!",
                    "Módulo principal verificado com sucesso no GitHub.",
                    1.0,
                )
                return

            self.log("[UPDATER] Módulo atualizado! Recarregando interface instantaneamente...")
            self.set_ui_status(
                "✅ Atualização concluída! Recarregando Limbo Launcher...",
                f"Abrindo versão v{remote_ver}...",
                1.0,
            )
            self.reload_requested = True
            self.after(300, self.destroy)

        except Exception as e:
            self.is_updating_launcher = False
            self.log(f"[UPDATER] Falha ao atualizar o Launcher: {e}")
            self.set_ui_status(
                "❌ Erro ao atualizar o Launcher:",
                f"{str(e)[:85]} (Veja a aba Logs)",
                0.0,
            )
            self.after(
                0,
                lambda: self.btn_update_launcher.configure(
                    text="🔄   Atualizar Launcher", fg_color="transparent", text_color=COLOR_CYAN
                ),
            )

    def open_skins_gallery(self):
        webbrowser.open(SKINS_GALLERY_URL)

    def open_game_folder(self):
        os.makedirs(self.modpack_dir, exist_ok=True)
        os.startfile(self.modpack_dir)

    def copy_server_ip(self):
        self.clipboard_clear()
        self.clipboard_append(DEFAULT_SERVER_IP)
        old_txt = self.hero_canvas.itemcget(self.server_status_text_id, "text")
        self.hero_canvas.itemconfig(
            self.server_status_text_id,
            text="✅ IP COPIADO PARA A ÁREA DE TRANSFERÊNCIA!",
            fill=COLOR_CYAN,
        )
        self.after(
            2000,
            lambda: self.hero_canvas.itemconfig(
                self.server_status_text_id, text=old_txt, fill=COLOR_TEXT
            ),
        )

    def trigger_force_repair(self):
        self.force_repair = True
        self.log("[SISTEMA] Reparo completo agendado para o próximo clique em JOGAR.")
        self.btn_repair.configure(
            text="✅ Reparo agendado! Clique em JOGAR para verificar tudo.",
            fg_color=COLOR_PURPLE,
        )

    def set_ui_status(self, title: str, detail: str = "", progress: float | None = None):
        def _update():
            self.status_title.configure(text=title)
            if detail:
                self.status_detail.configure(text=detail)
            if progress is not None:
                self.progress_bar.set(max(0.0, min(1.0, progress)))
        self.after(0, _update)

    # --------------------------------------------------------------------------
    # Loop Assíncrono de Status do Servidor + Botão de Ligar Servidor
    # --------------------------------------------------------------------------
    def start_server_status_loop(self):
        def _worker():
            first_check = True
            while True:
                was_starting = self.server_is_starting
                was_online = self.server_is_online

                if os.path.exists(r"D:\ServidorCobblemon\user_jvm_args.txt"):
                    online, ping_ms, p_on, p_max = ping_minecraft_server("127.0.0.1")
                else:
                    online, ping_ms, p_on, p_max = ping_minecraft_server(DEFAULT_SERVER_IPV6)
                    if not online:
                        online, ping_ms, p_on, p_max = ping_minecraft_server(DEFAULT_SERVER_IP)
                self.server_is_online = online

                if online:
                    self.server_is_starting = False
                    txt = f"SERVIDOR ONLINE  •  {p_on}/{p_max} Jogadores ({ping_ms}ms)"
                    dot_col = COLOR_ONLINE
                    btn_txt = "🟢   Servidor Online!"
                    btn_fg = "#1b4332"
                    btn_tc = COLOR_ONLINE

                    if first_check or not was_online:
                        self.log(
                            f"[SERVIDOR] Status ONLINE detectado! Ping: {ping_ms}ms | Jogadores: {p_on}/{p_max}"
                        )

                    # Se estava iniciando (remotamente ou localmente) e acabou de ficar Online,
                    # atualizar imediatamente a mensagem da barra inferior!
                    if was_starting and not self.is_launching and not self.is_updating_launcher:
                        self.set_ui_status(
                            "🟢 Servidor ONLINE e pronto para jogar!",
                            f"Conectado ({ping_ms}ms • {p_on}/{p_max} jogadores) — Clique em ▶ JOGAR!",
                            1.0,
                        )
                elif self.server_is_starting:
                    txt = "INICIANDO SERVIDOR...  •  Aguarde ~45s"
                    dot_col = "#ffb703"
                    btn_txt = "⏳   Iniciando Servidor..."
                    btn_fg = "#3c096c"
                    btn_tc = COLOR_CYAN
                else:
                    txt = f"SERVIDOR OFFLINE  •  {DEFAULT_SERVER_IP}"
                    dot_col = COLOR_OFFLINE
                    btn_txt = "🚀   Ligar Servidor"
                    btn_fg = "#1b3a2b"
                    btn_tc = COLOR_ONLINE
                    if first_check or was_online:
                        self.log(f"[SERVIDOR] Status OFFLINE ({DEFAULT_SERVER_IP}).")

                first_check = False

                def _upd(t=txt, c=dot_col, bt=btn_txt, bf=btn_fg, btc=btn_tc):
                    if hasattr(self, "server_status_text_id"):
                        self.hero_canvas.itemconfig(self.server_status_text_id, text=t)
                        self.hero_canvas.itemconfig(self.server_status_dot_id, fill=c)
                    if hasattr(self, "btn_start_server"):
                        self.btn_start_server.configure(
                            text=bt, fg_color=bf, text_color=btc
                        )

                self.after(0, _upd)
                # Verificar mais rápido (a cada 4s) enquanto o servidor está iniciando!
                time.sleep(4 if self.server_is_starting else 10)

        threading.Thread(target=_worker, daemon=True).start()

    def on_start_server_clicked(self):
        if self.server_is_online:
            self.set_ui_status(
                "🟢 O Servidor já está ONLINE!",
                "Basta clicar no botão ▶ JOGAR para entrar no mundo!",
                1.0,
            )
            return

        if self.server_is_starting:
            self.set_ui_status(
                "⏳ O Servidor já está iniciando...",
                "Aguarde cerca de 40 a 50 segundos até o status ficar VERDE (Online).",
                0.5,
            )
            return

        username = self.nick_entry.get().strip() or "Treinador"
        self.server_is_starting = True
        self.btn_start_server.configure(
            text="⏳   Iniciando Servidor...", fg_color="#3c096c", text_color=COLOR_CYAN
        )
        self.set_ui_status(
            "🚀 Enviando sinal para ligar o Servidor...",
            "Conectando ao Limbo Bot para acordar o servidor Cobblemon...",
            0.35,
        )
        threading.Thread(
            target=self.wake_or_start_server_worker, args=(username,), daemon=True
        ).start()

    def wake_or_start_server_worker(self, username: str):
        self.log(f"[WAKE] Iniciando pedido para ligar servidor (Treinador: {username})...")

        # Resolver SRV com fallback multi-DNS (Padrão -> Google 8.8.8.8 -> Cloudflare 1.1.1.1)
        srv_host, srv_port, dns_label = resolve_minecraft_srv(DEFAULT_SERVER_IP)
        self.log(
            f"[WAKE] Resolução SRV ({dns_label}): {DEFAULT_SERVER_IP} -> {srv_host}:{srv_port}"
        )

        # Lista de alvos:
        # 1) Localhost (caso seja o próprio PC do Host com o Bot aberto)
        # 2) IPv6 Direto (amigos com IPv6 - Ultra Rápido)
        # 3) Túnel Playit.gg remoto (usando pacote Minecraft Handshake válido para passar pelo filtro NoRawIp!)
        targets = [
            ("Localhost (Host PC)", "127.0.0.1", 25565, False),
            (f"IPv6 Direto ([{DEFAULT_SERVER_IPV6}]:25565)", DEFAULT_SERVER_IPV6, 25565, False),
            (f"Túnel Playit.gg ({srv_host}:{srv_port})", srv_host, srv_port, True),
        ]

        woke_via_bot = False
        for label, host, port, use_mc_handshake in targets:
            try:
                connect_ip = socket.gethostbyname(host)
                self.log(f"[WAKE] Tentando conectar a {label} [{connect_ip}:{port}]...")
                with socket.create_connection((connect_ip, port), timeout=4.5) as s:
                    s.settimeout(4.5)
                    if use_mc_handshake:
                        # IMPORTANTE: O túnel minecraft-java do Playit.gg possui filtro 'NoRawIp' e exige
                        # que o 1º pacote seja um Minecraft Handshake (0x00) válido contendo o domínio
                        # 'practically-currently.tun.ply.gg'. Embutimos '\x00LIMBO_WAKE:<nick>\n' no campo
                        # de endereço do Handshake (padrão FML/Bungee), que o Playit encaminha integralmente!
                        host_b = f"{DEFAULT_SERVER_IP}\x00LIMBO_WAKE:{username}\n".encode("utf-8")
                        handshake = (
                            pack_varint(0x00)
                            + pack_varint(767)
                            + pack_varint(len(host_b))
                            + host_b
                            + struct.pack(">H", port)
                            + pack_varint(1)
                        )
                        pkt = pack_varint(len(handshake)) + handshake
                        s.sendall(pkt)
                    else:
                        s.sendall(f"LIMBO_WAKE:{username}\n".encode("utf-8"))

                    resp = s.recv(128)
                    self.log(f"[WAKE] Resposta de {label}: {resp!r}")
                    if resp and b"OK_STARTING" in resp:
                        woke_via_bot = True
                        self.log(f"[WAKE] Sucesso! O Limbo Bot confirmou a inicialização via {label}!")
                        break
            except Exception as e:
                self.log(f"[WAKE] Falha em {label}: {type(e).__name__}: {e}")
                continue

        if woke_via_bot:
            self.set_ui_status(
                "🚀 Servidor iniciando! Aguarde ~45s...",
                "O Limbo Bot já está abrindo o servidor. O aviso ficará VERDE assim que abrir!",
                0.65,
            )
            return

        # 2. Fallback Local: Se estamos no PC do próprio dono (D:\ServidorCobblemon existe) e o bot estava fechado
        local_srv_dir = r"D:\ServidorCobblemon"
        if os.path.exists(os.path.join(local_srv_dir, "user_jvm_args.txt")):
            self.log("[WAKE] Limbo Bot não respondeu na porta 25565, mas D:\\ServidorCobblemon existe. Iniciando localmente...")
            try:
                playit_exe = os.path.join(local_srv_dir, "playit.exe")
                creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                if os.path.exists(playit_exe):
                    subprocess.Popen(
                        [playit_exe, "-s", "start"],
                        cwd=local_srv_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )

                java_exec = self.find_or_install_java21()
                cmd = [
                    java_exec,
                    "@user_jvm_args.txt",
                    "@libraries/net/neoforged/neoforge/21.1.262/win_args.txt",
                    "nogui",
                ]
                self.local_server_proc = subprocess.Popen(
                    cmd,
                    cwd=local_srv_dir,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creation_flags,
                )

                def _watch_local_srv(proc):
                    for line in iter(proc.stdout.readline, ""):
                        if not line:
                            break
                        if 'Done (' in line and 'For help, type "help"' in line:
                            try:
                                proc.stdin.write("gamerule mobTargetInBattle false\n")
                                proc.stdin.write("gamerule battleInvulnerability true\n")
                                proc.stdin.flush()
                            except Exception:
                                pass
                            self.server_is_online = True
                            self.server_is_starting = False
                            self.log("[SERVIDOR LOCAL] Servidor terminou de carregar (Done)!")
                            self.set_ui_status(
                                "🟢 Servidor Local ONLINE!",
                                "O servidor terminou de iniciar no seu PC! Clique em ▶ JOGAR.",
                                1.0,
                            )

                threading.Thread(
                    target=_watch_local_srv, args=(self.local_server_proc,), daemon=True
                ).start()
                self.set_ui_status(
                    "🚀 Iniciando Servidor Localmente (~45s)...",
                    "Subindo Playit.gg + Servidor NeoForge em segundo plano...",
                    0.75,
                )
                return
            except Exception as e:
                self.server_is_starting = False
                self.log(f"[ERRO] Falha ao iniciar servidor local: {e}")
                self.set_ui_status("❌ Erro ao iniciar servidor local:", str(e)[:100], 0.0)
                return

        # 3. Se não é o PC do host e o bot do host não respondeu
        self.server_is_starting = False
        self.log("[WAKE] Não foi possível acordar o servidor (sem resposta de OK_STARTING). Veja os detalhes acima.")
        self.set_ui_status(
            "⚠️ Não foi possível acordar o servidor remotamente!",
            "Veja a aba '📋 Logs / Diagnóstico' na esquerda ou use /iniciar no Discord.",
            0.0,
        )

    # --------------------------------------------------------------------------
    # Fluxo Principal de Sincronização + Instalação + Execução na Pasta TLauncher
    # --------------------------------------------------------------------------
    def on_play_clicked(self):
        if self.is_launching:
            return

        username = self.nick_entry.get().strip()
        if not username or len(username) < 3 or len(username) > 16 or " " in username:
            self.set_ui_status(
                "⚠️ Nickname inválido!",
                "Digite um Nickname válido (de 3 a 16 letras/números, sem espaços) na barra lateral.",
                0.0,
            )
            self.nick_entry.focus_set()
            return

        self.save_config()
        self.is_launching = True
        self.play_btn.configure(state="disabled", text="⏳ PREPARANDO...", fg_color="#3c096c")

        threading.Thread(
            target=self.launch_pipeline_worker, args=(username,), daemon=True
        ).start()

    def ensure_options_txt_resourcepacks(self, default_packs: list[str]):
        """Garante que as 3 texturas do Cobblemon estejam ativadas no options.txt da pasta versions\\cobble."""
        options_path = os.path.join(self.modpack_dir, "options.txt")
        base_packs = [
            "vanilla",
            "mod_resources",
            "mod/mega_showdown:resourcepacks/regionbiasmsd",
            "mod/cobblemon:resourcepacks/regionbiasforms",
        ]
        for p in default_packs:
            if p not in base_packs:
                base_packs.append(p)
        packs_json = json.dumps(base_packs)

        if not os.path.exists(options_path):
            with open(options_path, "w", encoding="utf-8") as f:
                f.write(f"resourcePacks:{packs_json}\n")
                f.write("incompatibleResourcePacks:[]\n")
                f.write("lang:pt_br\n")
            return

        try:
            with open(options_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            found = False
            new_lines = []
            for line in lines:
                if line.startswith("resourcePacks:"):
                    new_lines.append(f"resourcePacks:{packs_json}\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"resourcePacks:{packs_json}\n")
            with open(options_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception:
            pass

    def sync_modpack_files(self) -> dict:
        """
        Sincroniza todos os mods, resourcepacks e configs diretamente dentro de
        <.minecraft do TLauncher>\\versions\\cobble!
        """
        self.set_ui_status(
            "🌐 Conectando ao servidor de atualização...",
            "Buscando manifesto oficial no GitHub...",
            0.05,
        )

        req = urllib.request.Request(
            f"{MANIFEST_URL}?t={int(time.time())}",
            headers={"User-Agent": "LimboLauncher/1.1", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))

        files_list = manifest.get("files", [])
        expected_mods = set()
        to_download = []

        always_sync_configs = {
            "config/cobblemon/main.json",
            "config/immediatelyfast.json",
            "config/sodium-options.json",
        }

        self.set_ui_status(
            "🔍 Verificando arquivos na pasta do TLauncher...",
            f"Conferindo {len(files_list)} arquivos em {self.modpack_dir}...",
            0.12,
        )

        for item in files_list:
            rel_path = item["path"]
            expected_sha = item["sha1"]
            url = item["url"]
            local_path = os.path.join(self.modpack_dir, rel_path.replace("/", os.sep))

            if rel_path.startswith("mods/"):
                expected_mods.add(os.path.normcase(local_path))

            if (
                rel_path.startswith("config/") or rel_path == "servers.dat"
            ) and rel_path not in always_sync_configs:
                if os.path.exists(local_path) and not self.force_repair:
                    continue

            needs_dl = True
            if os.path.exists(local_path):
                try:
                    if sha1_file(local_path) == expected_sha:
                        needs_dl = False
                except Exception:
                    needs_dl = True

            if needs_dl:
                to_download.append((rel_path, local_path, url, expected_sha))

        # Remover mods obsoletos da pasta versions\cobble\mods
        mods_dir = os.path.join(self.modpack_dir, "mods")
        if os.path.exists(mods_dir):
            for fname in os.listdir(mods_dir):
                fpath = os.path.join(mods_dir, fname)
                if os.path.isfile(fpath) and fname.endswith(".jar"):
                    if os.path.normcase(fpath) not in expected_mods:
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass

        if to_download:
            total = len(to_download)
            completed = 0

            def _download_one(entry):
                rel_p, loc_p, dl_url, _ = entry
                os.makedirs(os.path.dirname(loc_p), exist_ok=True)
                tmp_p = loc_p + ".tmp"
                req_f = urllib.request.Request(
                    dl_url, headers={"User-Agent": "LimboLauncher/1.1"}
                )
                with urllib.request.urlopen(req_f, timeout=60) as r, open(
                    tmp_p, "wb"
                ) as out_f:
                    shutil.copyfileobj(r, out_f)
                os.replace(tmp_p, loc_p)
                return rel_p

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(_download_one, item) for item in to_download]
                for fut in as_completed(futures):
                    rel_done = fut.result()
                    completed += 1
                    pct = 0.15 + (0.45 * (completed / total))
                    short_name = os.path.basename(rel_done)
                    self.set_ui_status(
                        f"⬇️ Atualizando Modpack ({completed}/{total})...",
                        f"Baixado: {short_name}",
                        pct,
                    )
        else:
            self.set_ui_status(
                "✅ Modpack 100% sincronizado!",
                f"Todos os 78 mods verificados em {self.modpack_dir}",
                0.60,
            )

        default_rps = manifest.get("default_resourcepacks", [])
        if default_rps:
            self.ensure_options_txt_resourcepacks(default_rps)

        return manifest

    def find_or_install_java21(self) -> str:
        """
        Procura primeiro o Java 21 já instalado pelo TLauncher em:
        <.minecraft>\\runtime\\java-runtime-delta\\windows\\java-runtime-delta\\bin\\java.exe
        ou pelo instalador padrão Mojang. Só baixa se não existir!
        """
        tl_java_candidates = [
            os.path.join(
                self.mc_root_dir,
                "runtime",
                JVM_RUNTIME,
                "windows",
                JVM_RUNTIME,
                "bin",
                "java.exe",
            ),
            os.path.join(
                self.mc_root_dir,
                "runtime",
                JVM_RUNTIME,
                "windows-x64",
                JVM_RUNTIME,
                "bin",
                "java.exe",
            ),
        ]
        for cand in tl_java_candidates:
            if os.path.isfile(cand):
                return cand

        java_exec = mll.runtime.get_executable_path(JVM_RUNTIME, self.mc_root_dir)
        if java_exec and os.path.isfile(java_exec):
            return java_exec

        # Caso o amigo ainda não tenha Java 21 no .minecraft, instalar automaticamente
        self.set_ui_status(
            "☕ Instalando Java 21 Oficial na pasta .minecraft...",
            "Isso ocorre apenas na 1ª vez caso o TLauncher ainda não tenha o Java 21.",
            0.64,
        )
        max_prog = {"val": 100}

        def set_max(m):
            max_prog["val"] = max(1, m)

        def set_prog(p):
            ratio = p / max_prog["val"]
            self.set_ui_status(
                "☕ Baixando Java 21 Runtime Oficial...",
                f"Progresso Java: {int(ratio * 100)}%",
                0.64 + (0.14 * ratio),
            )

        callback = {
            "setStatus": lambda s: self.set_ui_status("☕ Instalando Java 21...", s),
            "setProgress": set_prog,
            "setMax": set_max,
        }
        mll.runtime.install_jvm_runtime(JVM_RUNTIME, self.mc_root_dir, callback=callback)
        return mll.runtime.get_executable_path(JVM_RUNTIME, self.mc_root_dir) or "java"

    def ensure_tlauncher_json_compatible(self, json_path: str):
        """
        O TLauncher usa a chave 'values' em vez do padrão Mojang 'value' dentro dos .json.
        Garante que ambas as chaves ('values' e 'value') existam para funcionar tanto no
        TLauncher quanto no Limbo Launcher instantaneamente!
        """
        if not os.path.isfile(json_path):
            return
        try:
            with open(json_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            changed = False
            args_dict = data.get("arguments")
            if isinstance(args_dict, dict):
                for section in ["game", "jvm"]:
                    for item in args_dict.get(section, []):
                        if isinstance(item, dict):
                            if "values" in item and "value" not in item:
                                item["value"] = item["values"]
                                changed = True
                            elif "value" in item and "values" not in item:
                                item["values"] = item["value"]
                                changed = True
            if changed:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def normalize_all_version_jsons(self):
        """Normaliza todos os JSONs de versões relevantes na .minecraft (1.21.1, cobble e neoforge)."""
        candidates = [
            os.path.join(self.mc_root_dir, "versions", MC_VERSION, f"{MC_VERSION}.json"),
            os.path.join(self.modpack_dir, f"{MODPACK_FOLDER_NAME}.json"),
            os.path.join(
                self.mc_root_dir,
                "versions",
                f"neoforge-{NEOFORGE_VERSION}",
                f"neoforge-{NEOFORGE_VERSION}.json",
            ),
        ]
        for c in candidates:
            self.ensure_tlauncher_json_compatible(c)

    def ensure_neoforge_installed(self, java_exec: str) -> str:
        """
        Verifica se o NeoForge 21.1.262 já existe na pasta .minecraft do TLauncher
        (seja como versions\\cobble\\cobble.json ou versions\\neoforge-21.1.262).
        Se não existir (1ª vez no PC de um amigo), instala o NeoForge na .minecraft!
        """
        self.normalize_all_version_jsons()

        neoforge_client_lib = os.path.join(
            self.mc_root_dir,
            "libraries",
            "net",
            "neoforged",
            "neoforge",
            NEOFORGE_VERSION,
            f"neoforge-{NEOFORGE_VERSION}-client.jar",
        )
        cobble_json = os.path.join(self.modpack_dir, f"{MODPACK_FOLDER_NAME}.json")
        nf_loader = mll.mod_loader.get_mod_loader("neoforge")
        nf_version_id = nf_loader.get_installed_version(MC_VERSION, NEOFORGE_VERSION)
        nf_json = os.path.join(
            self.mc_root_dir, "versions", nf_version_id, f"{nf_version_id}.json"
        )

        # Se já tem o cobble.json do TLauncher e a biblioteca cliente do NeoForge instalada
        if os.path.isfile(cobble_json) and os.path.isfile(neoforge_client_lib) and not self.force_repair:
            return MODPACK_FOLDER_NAME

        # Se já tem o neoforge-21.1.262 instalado na .minecraft
        if os.path.isfile(nf_json) and os.path.isfile(neoforge_client_lib) and not self.force_repair:
            return nf_version_id

        # Caso contrário, instalar NeoForge 21.1.262 na .minecraft compartilhando libraries/assets
        self.set_ui_status(
            f"🔨 Instalando NeoForge {NEOFORGE_VERSION} na .minecraft...",
            "Aproveitando bibliotecas do TLauncher (Apenas no 1º início)...",
            0.78,
        )
        max_nf = {"val": 100}

        def nf_set_max(m):
            max_nf["val"] = max(1, m)

        def nf_set_prog(p):
            ratio = p / max_nf["val"]
            self.set_ui_status(
                f"🔨 Instalando NeoForge {NEOFORGE_VERSION}...",
                f"Verificando bibliotecas do jogo: {int(ratio * 100)}%",
                0.78 + (0.18 * ratio),
            )

        nf_callback = {
            "setStatus": lambda s: self.set_ui_status(
                f"🔨 Instalando NeoForge {NEOFORGE_VERSION}...", s
            ),
            "setProgress": nf_set_prog,
            "setMax": nf_set_max,
        }
        installed_id = nf_loader.install(
            MC_VERSION,
            self.mc_root_dir,
            loader_version=NEOFORGE_VERSION,
            callback=nf_callback,
            java=java_exec,
        )
        self.normalize_all_version_jsons()
        self.force_repair = False
        return installed_id

    def launch_pipeline_worker(self, username: str):
        try:
            self.log(f"[JOGAR] Iniciando preparação para o treinador: {username}")

            # Etapa 1: Sincronizar Modpack em <.minecraft>\versions\cobble
            self.sync_modpack_files()
            self.log("[JOGAR] Sincronização do Modpack concluída.")

            # Etapa 2: Localizar ou instalar Java 21 na <.minecraft>\runtime
            java_exec = self.find_or_install_java21()
            self.log(f"[JOGAR] Java 21 localizado em: {java_exec}")

            # Etapa 3: Verificar ou instalar NeoForge 21.1.262 na <.minecraft>
            version_id = self.ensure_neoforge_installed(java_exec)
            self.log(f"[JOGAR] Perfil NeoForge selecionado: {version_id}")

            # Etapa 4: Gerar UUID Offline Determinístico (Mine Pirata)
            offline_uuid = get_offline_uuid(username)
            ram_gb = int(round(self.ram_slider.get()))

            self.set_ui_status(
                "🚀 Iniciando Limbo Cobblemon...",
                f"Treinador: {username}  |  RAM: {ram_gb}GB  |  Pasta: versions\\{MODPACK_FOLDER_NAME}",
                0.98,
            )

            jvm_args = [
                f"-Xmx{ram_gb}G",
                "-Djava.net.preferIPv6Addresses=system",
                "-XX:+UnlockExperimentalVMOptions",
                "-XX:+UseG1GC",
                "-XX:G1NewSizePercent=20",
                "-XX:G1ReservePercent=20",
                "-XX:MaxGCPauseMillis=50",
                "-XX:G1HeapRegionSize=32M",
            ]

            options: mll.types.MinecraftOptions = {
                "username": username,
                "uuid": offline_uuid,
                "token": "0",
                "executablePath": java_exec,
                "gameDirectory": self.modpack_dir,
                "jvmArguments": jvm_args,
                "launcherName": APP_NAME,
                "launcherVersion": APP_VERSION,
            }

            if self.auto_connect_var.get():
                if os.path.exists(r"D:\ServidorCobblemon\user_jvm_args.txt"):
                    options["quickPlayMultiplayer"] = "127.0.0.1:25565"
                    self.log("[JOGAR] Host detectado: Conectando via 127.0.0.1 (Ping 0ms local)!")
                else:
                    v6_ok = False
                    try:
                        with socket.create_connection((DEFAULT_SERVER_IPV6, 25565), timeout=2.0):
                            v6_ok = True
                    except Exception:
                        v6_ok = False

                    if v6_ok:
                        options["quickPlayMultiplayer"] = f"[{DEFAULT_SERVER_IPV6}]:25565"
                        self.log(f"[JOGAR] IPv6 ativo! Conectando via IPv6 Direto [{DEFAULT_SERVER_IPV6}]:25565 (Ping ~20ms)!")
                    else:
                        options["quickPlayMultiplayer"] = DEFAULT_SERVER_IP
                        self.log(f"[JOGAR] Fallback Playit: Conectando via {DEFAULT_SERVER_IP} (Ping ~180ms)!")

            mc_cmd = mll.command.get_minecraft_command(
                version_id, self.mc_root_dir, options
            )
            self.log(f"[JOGAR] Comando de inicialização montado ({len(mc_cmd)} argumentos). Abrindo Minecraft...")

            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(
                mc_cmd,
                cwd=self.modpack_dir,
                creationflags=creation_flags,
            )
            self.log(f"[JOGAR] Processo do Minecraft iniciado (PID: {proc.pid}).")

            self.set_ui_status(
                "🎮 Jogo em execução!",
                "O Minecraft está abrindo (pode levar ~20s para carregar os mods). Bom jogo!",
                1.0,
            )
            self.after(
                0, lambda: self.play_btn.configure(text="🎮 EM JOGO", fg_color="#1b4332")
            )

            ret_code = proc.wait()
            self.log(f"[JOGAR] Processo do Minecraft encerrado (código: {ret_code}).")

            self.is_launching = False
            self.set_ui_status(
                "Pronto para iniciar sua jornada!",
                f"Modpack vinculado em: {self.modpack_dir}",
                1.0,
            )
            self.after(
                0,
                lambda: self.play_btn.configure(
                    state="normal", text="▶  JOGAR", fg_color=COLOR_PURPLE
                ),
            )

        except Exception as e:
            import traceback
            self.is_launching = False
            err_msg = str(e)
            tb_str = traceback.format_exc()
            self.log(f"[ERRO JOGAR] {type(e).__name__}: {err_msg}\n{tb_str}")
            self.set_ui_status(
                "❌ Erro durante a preparação (veja a aba 📋 Logs):",
                err_msg[:110],
                0.0,
            )
            self.after(
                0,
                lambda: self.play_btn.configure(
                    state="normal", text="▶  JOGAR", fg_color=COLOR_PURPLE
                ),
            )


def _extract_version_from_code(code_str: str) -> str:
    for line in code_str.splitlines():
        s = line.strip()
        if s.startswith("APP_VERSION") and "=" in s:
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


if __name__ == "__main__":
    while True:
        target_cls = LimboLauncherApp
        if getattr(sys, "frozen", False) and os.path.isfile(LOCAL_CORE_SCRIPT):
            try:
                with open(LOCAL_CORE_SCRIPT, "r", encoding="utf-8") as _f:
                    _core_code = _f.read()
                _core_ver = _extract_version_from_code(_core_code)
                if parse_version_tuple(_core_ver) >= parse_version_tuple(APP_VERSION) and "class LimboLauncherApp" in _core_code:
                    _ns = dict(globals())
                    _ns["__name__"] = "__limbo_core__"
                    exec(compile(_core_code, LOCAL_CORE_SCRIPT, "exec"), _ns)
                    target_cls = _ns.get("LimboLauncherApp", LimboLauncherApp)
                else:
                    os.remove(LOCAL_CORE_SCRIPT)
            except Exception:
                target_cls = LimboLauncherApp

        app = target_cls()
        app.mainloop()
        if not getattr(app, "reload_requested", False):
            break
