import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import socket, threading, json, struct, time, base64, uuid, os, sys
from io import BytesIO
from queue import Queue, Empty
import traceback

# ── Dependências opcionais ────────────────────────────────────────────────────
import sounddevice as sd
import numpy as np

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False
    print("⚠  Pillow não encontrado — avatares limitados")
    print("   Instale com: pip install pillow")

AUDIO_OK = True
AUDIOOP_OK = True

# ── Constantes de Rede ────────────────────────────────────────────────────────
DISC_PORT  = 55100    # UDP broadcast — descoberta de salas
CTRL_PORT  = 55101    # TCP — controle e mensagens de texto
VOICE_PORT = 55102    # UDP — dados de voz
DISC_INTERVAL = 2.5   # segundos entre broadcasts de sala

# ── Constantes de Áudio ───────────────────────────────────────────────────────
A_CHUNK    = 1024
A_RATE     = 44100
A_CHANNELS = 1

# ── Paleta de Cores ───────────────────────────────────────────────────────────
BG0 = '#0d1117'   # fundo principal
BG1 = '#161b22'   # superfície
BG2 = '#21262d'   # superfície elevada
BDR = '#30363d'   # borda
ACC = '#58a6ff'   # azul destaque
GRN = '#3fb950'   # verde
RED = '#f85149'   # vermelho
YEL = '#d29922'   # amarelo
TXT = '#e6edf3'   # texto primário
TX2 = '#8b949e'   # texto secundário
TX3 = '#6e7681'   # texto terciário
PRP = '#bc8cff'   # roxo

AVATAR_COLORS = [
    '#1f6feb', '#6e40c9', '#1a7f37', '#9e6a03', '#da3633',
    '#0969da', '#8250df', '#2da44e', '#bf8700', '#cf222e',
    '#0550ae', '#5a32a3', '#166534', '#7d4e00', '#a40e26',
]

FONT_FAMILIES = ['Segoe UI', 'SF Pro Display', 'Helvetica Neue', 'Ubuntu', 'sans-serif']


def best_font():
    """Retorna a melhor fonte disponível."""
    import tkinter.font as tkfont
    available = set(tkfont.families())
    for f in FONT_FAMILIES:
        if f in available:
            return f
    return 'TkDefaultFont'


# ── Utilitários de Rede ───────────────────────────────────────────────────────

def local_ip():
    """Detecta o IP local da máquina."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'


def send_tcp(sock, obj):
    """Envia objeto JSON com prefixo de tamanho via TCP."""
    try:
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        sock.sendall(struct.pack('>I', len(data)) + data)
        return True
    except:
        return False


def recv_tcp(sock):
    """Recebe mensagem JSON com prefixo de tamanho via TCP."""
    hdr = _recvn(sock, 4)
    if hdr is None:
        return None
    n = struct.unpack('>I', hdr)[0]
    if n > 20 * 1024 * 1024:  # limite de 20MB
        return None
    raw = _recvn(sock, n)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except:
        return None


def _recvn(sock, n):
    """Recebe exatamente n bytes."""
    buf = b''
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


# ── Utilitários de Avatar ────────────────────────────────────────────────────

def make_avatar_img(name, size=40):
    """Cria avatar circular com iniciais."""
    if not PIL_OK:
        return None
    col = AVATAR_COLORS[sum(ord(c) for c in (name or '?')) % len(AVATAR_COLORS)]
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=col)
    initials = ''.join(p[0].upper() for p in (name or '?').split()[:2])[:2] or '?'
    fs = max(8, size // 3)
    font = None
    font_paths = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        'C:/Windows/Fonts/arialbd.ttf',
        '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
    ]
    for fp in font_paths:
        try:
            font = ImageFont.truetype(fp, fs)
            break
        except:
            pass
    try:
        if font:
            bb = d.textbbox((0, 0), initials, font=font)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            d.text(((size - tw) // 2, (size - th) // 2 - 1), initials,
                   fill='white', font=font)
        else:
            d.text((size // 5, size // 5), initials, fill='white')
    except:
        pass
    return img


def make_round_tkimg(img_pil, size):
    """Aplica máscara circular e converte para PhotoImage."""
    if not PIL_OK or img_pil is None:
        return None
    img_pil = img_pil.resize((size, size), Image.LANCZOS).convert('RGBA')
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img_pil, mask=mask)
    return ImageTk.PhotoImage(out)


def img_to_b64(img, max_size=200):
    """Converte imagem PIL para base64 PNG."""
    if img.size[0] > max_size or img.size[1] > max_size:
        img = img.copy()
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, 'PNG')
    return base64.b64encode(buf.getvalue()).decode()


def b64_to_pil(b64str):
    """Converte base64 para imagem PIL."""
    if not PIL_OK or not b64str:
        return None
    try:
        return Image.open(BytesIO(base64.b64decode(b64str)))
    except:
        return None


# ── Chat Server (host da sala) ────────────────────────────────────────────────

class ChatServer:
    """Servidor TCP+UDP que roda na máquina que criou a sala."""

    def __init__(self, room_name, on_event):
        self.room_name = room_name
        self.on_event = on_event      # callback(dict) chamado no thread do servidor
        self.clients = {}             # cid -> {sock, name, avatar, addr}
        self.next_cid = 1
        self.running = False
        self._lock = threading.Lock()
        self._voice_addrs = {}        # cid -> (ip, port) aprendido via UDP
        self._voice_lock = threading.Lock()

        # TCP para controle/texto
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp.bind(('', CTRL_PORT))
        self._tcp.listen(30)

        # UDP para voz
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp.bind(('', VOICE_PORT))
        self._udp.settimeout(1.0)

        # UDP para discovery broadcast
        self._disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._disc.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._disc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def start(self):
        self.running = True
        threading.Thread(target=self._accept_loop,    daemon=True).start()
        threading.Thread(target=self._voice_relay,    daemon=True).start()
        threading.Thread(target=self._discovery_loop, daemon=True).start()

    def stop(self):
        self.running = False
        for sock in [self._tcp, self._udp, self._disc]:
            try: sock.close()
            except: pass
        with self._lock:
            for c in self.clients.values():
                try: c['sock'].close()
                except: pass

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _discovery_loop(self):
        ip = local_ip()
        while self.running:
            try:
                with self._lock:
                    count = len(self.clients) + 1
                payload = json.dumps({
                    'type': 'room_announce',
                    'room': self.room_name,
                    'host': ip,
                    'port': CTRL_PORT,
                    'users': count,
                }).encode()
                self._disc.sendto(payload, ('<broadcast>', DISC_PORT))
            except:
                pass
            time.sleep(DISC_INTERVAL)

    # ── TCP Accept ────────────────────────────────────────────────────────────

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self._tcp.accept()
                conn.settimeout(None)
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True
                ).start()
            except:
                break

    def _handle_client(self, sock, addr):
        cid = None
        try:
            msg = recv_tcp(sock)
            if not msg or msg.get('type') != 'join':
                sock.close()
                return

            name   = msg.get('name', 'Anônimo')[:40]
            avatar = msg.get('avatar', '')

            with self._lock:
                cid = self.next_cid
                self.next_cid += 1
                self.clients[cid] = {
                    'sock': sock, 'name': name,
                    'avatar': avatar, 'addr': addr,
                }
                current_users = [
                    {'id': i, 'name': c['name'], 'avatar': c['avatar']}
                    for i, c in self.clients.items()
                ]

            # Envia info da sala para o novo cliente
            send_tcp(sock, {
                'type': 'room_info',
                'room': self.room_name,
                'your_id': cid,
                'users': current_users,
            })

            # Notifica outros clientes
            self._broadcast({
                'type': 'user_join',
                'id': cid, 'name': name, 'avatar': avatar,
            }, exclude=cid)

            # Notifica host (UI)
            self.on_event({'type': 'user_join', 'id': cid, 'name': name, 'avatar': avatar})

            # Loop de mensagens
            while self.running:
                msg = recv_tcp(sock)
                if msg is None:
                    break
                t = msg.get('type')
                if t == 'text':
                    content = msg.get('content', '')[:2000]
                    broadcast_msg = {
                        'type': 'text', 'id': cid, 'name': name,
                        'content': content, 'ts': time.time(),
                    }
                    self._broadcast(broadcast_msg, exclude=cid)
                    self.on_event(broadcast_msg)

        except Exception:
            pass
        finally:
            if cid is not None:
                with self._lock:
                    gone_name = self.clients.pop(cid, {}).get('name', '?')
                with self._voice_lock:
                    self._voice_addrs.pop(cid, None)
                leave_msg = {'type': 'user_leave', 'id': cid, 'name': gone_name}
                self._broadcast(leave_msg)
                self.on_event(leave_msg)
            try: sock.close()
            except: pass

    def _broadcast(self, msg, exclude=None):
        with self._lock:
            targets = [(i, c['sock']) for i, c in self.clients.items()
                       if i != exclude]
        for _, sock in targets:
            send_tcp(sock, msg)

    # ── Relay de Voz (UDP) ────────────────────────────────────────────────────

    def _voice_relay(self):
        """
        Recebe pacotes UDP de voz e retransmite para todos os outros.
        Formato do pacote: [4 bytes CID uint32 big-endian][PCM bytes]
        O CID=0 é reservado para o host.
        """
        while self.running:
            try:
                data, addr = self._udp.recvfrom(65535)
            except socket.timeout:
                continue
            except:
                break

            if len(data) < 4:
                continue

            cid = struct.unpack('>I', data[:4])[0]

            with self._voice_lock:
                self._voice_addrs[cid] = addr
                all_addrs = dict(self._voice_addrs)

            for other_cid, other_addr in all_addrs.items():
                if other_cid != cid:
                    try:
                        self._udp.sendto(data, other_addr)
                    except:
                        pass

    # ── API pública para o host ────────────────────────────────────────────────

    def broadcast_text(self, name, content):
        self._broadcast({
            'type': 'text', 'id': 0, 'name': name,
            'content': content, 'ts': time.time(),
        })

    def register_host_voice_addr(self, addr):
        """Registra o endereço UDP do host para receber voz dos clientes."""
        with self._voice_lock:
            self._voice_addrs[0] = addr

    def get_user_count(self):
        with self._lock:
            return len(self.clients) + 1


# ── Chat Client (quem entra na sala) ─────────────────────────────────────────

class ChatClient:
    """Conecta a um ChatServer como cliente."""

    def __init__(self, host, name, avatar_b64, on_event):
        self.host     = host
        self.name     = name
        self.avatar   = avatar_b64
        self.on_event = on_event
        self.cid      = None
        self.room_name = ''
        self._tcp  = None
        self._udp  = None
        self.running = False

    def connect(self):
        """Conecta ao servidor e retorna room_info dict."""
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.settimeout(6)
        self._tcp.connect((self.host, CTRL_PORT))
        self._tcp.settimeout(None)

        send_tcp(self._tcp, {
            'type':   'join',
            'name':   self.name,
            'avatar': self.avatar,
        })

        info = recv_tcp(self._tcp)
        if not info or info.get('type') != 'room_info':
            self._tcp.close()
            raise ConnectionError("Resposta inválida do servidor")

        self.cid       = info['your_id']
        self.room_name = info['room']

        # UDP para voz
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind(('', 0))
        self._udp.settimeout(1.0)

        self.running = True
        threading.Thread(target=self._tcp_recv_loop,  daemon=True).start()
        threading.Thread(target=self._udp_recv_loop,  daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

        return info

    def disconnect(self):
        self.running = False
        for s in [self._tcp, self._udp]:
            try: s.close()
            except: pass

    def send_text(self, content):
        send_tcp(self._tcp, {'type': 'text', 'content': content})

    def send_voice(self, audio_bytes):
        if self._udp and self.cid is not None:
            try:
                pkt = struct.pack('>I', self.cid) + audio_bytes
                self._udp.sendto(pkt, (self.host, VOICE_PORT))
            except:
                pass

    def _tcp_recv_loop(self):
        while self.running:
            msg = recv_tcp(self._tcp)
            if msg is None:
                if self.running:
                    self.on_event({'type': 'disconnected'})
                break
            self.on_event(msg)

    def _udp_recv_loop(self):
        while self.running:
            try:
                data, _ = self._udp.recvfrom(65535)
                if len(data) > 4:
                    sender_cid = struct.unpack('>I', data[:4])[0]
                    audio      = data[4:]
                    self.on_event({'type': 'voice', 'id': sender_cid, 'audio': audio})
            except socket.timeout:
                continue
            except:
                break

    def _heartbeat_loop(self):
        """Envia pacote vazio periodicamente para o servidor aprender nosso endereço UDP."""
        while self.running:
            try:
                pkt = struct.pack('>I', self.cid)
                self._udp.sendto(pkt, (self.host, VOICE_PORT))
            except:
                pass
            time.sleep(1.5)


# ── Descoberta de Salas ───────────────────────────────────────────────────────

class RoomDiscovery:
    """Escuta broadcasts UDP para descobrir salas na rede."""

    def __init__(self, on_room):
        self.on_room  = on_room   # callback({'room', 'host', 'port', 'users'})
        self._sock    = None
        self.running  = False
        self._seen    = {}        # host -> last_seen_time

    def start(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except:
                pass
            self._sock.bind(('', DISC_PORT))
            self._sock.settimeout(1.0)
            self.running = True
            threading.Thread(target=self._loop, daemon=True).start()
        except Exception as e:
            print(f"Discovery error: {e}")

    def stop(self):
        self.running = False
        try: self._sock.close()
        except: pass

    def _loop(self):
        while self.running:
            try:
                data, addr = self._sock.recvfrom(2048)
                msg = json.loads(data.decode('utf-8'))
                if msg.get('type') == 'room_announce':
                    host = msg.get('host', addr[0])
                    self._seen[host] = time.time()
                    self.on_room({
                        'room':  msg.get('room', 'Sala'),
                        'host':  host,
                        'port':  msg.get('port', CTRL_PORT),
                        'users': msg.get('users', 1),
                    })
            except socket.timeout:
                pass
            except:
                pass

    def get_stale_hosts(self, max_age=8.0):
        cutoff = time.time() - max_age
        return {h for h, t in self._seen.items() if t < cutoff}


# ── Motor de Áudio ────────────────────────────────────────────────────────────

class AudioEngine:
    """Gerencia captura e reprodução de áudio via sounddevice (Alternativa ao PyAudio)."""

    def __init__(self):
        self._in_stream   = None
        self._out_stream  = None
        self.muted        = False
        self.running      = False
        self._play_queue  = Queue(maxsize=30)
        self.capture_cb   = None

    @property
    def available(self):
        try:
            import sounddevice
            import numpy
            return True
        except ImportError:
            return False

    def start(self, capture_cb):
        if not self.available:
            return False
        self.capture_cb = capture_cb
        self.running    = True

        try:
            # Configuração do Stream de Entrada (Microfone)
            self._in_stream = sd.InputStream(
                samplerate=A_RATE,
                channels=A_CHANNELS,
                dtype='int16',
                blocksize=A_CHUNK,
                callback=self._capture_callback
            )
            self._in_stream.start()
        except Exception as e:
            print(f"Erro na entrada de áudio: {e}")

        try:
            # Configuração do Stream de Saída (Alto-falantes)
            self._out_stream = sd.OutputStream(
                samplerate=A_RATE,
                channels=A_CHANNELS,
                dtype='int16',
                blocksize=A_CHUNK,
                callback=self._playback_callback
            )
            self._out_stream.start()
        except Exception as e:
            print(f"Erro na saída de áudio: {e}")

        return True

    def stop(self):
        self.running = False
        if self._in_stream:
            try:
                self._in_stream.stop()
                self._in_stream.close()
            except: pass
        if self._out_stream:
            try:
                self._out_stream.stop()
                self._out_stream.close()
            except: pass
        self._in_stream  = None
        self._out_stream = None

    def terminate(self):
        self.stop()
        # sounddevice não exige terminação global como o PyAudio

    def _capture_callback(self, indata, frames, time, status):
        """Chamado pelo sounddevice quando novos dados do microfone chegam."""
        if status:
            print(f"Status de entrada: {status}")
        if not self.muted and self.capture_cb and self.running:
            # indata é um array numpy (frames, channels)
            # convertemos para bytes para manter compatibilidade com o resto do código
            self.capture_cb(indata.tobytes())

    def _playback_callback(self, outdata, frames, time, status):
        """Chamado pelo sounddevice quando precisa de dados para tocar."""
        if status:
            print(f"Status de saída: {status}")
        
        chunks = []
        # Coleta todos os chunks disponíveis na fila
        while True:
            try:
                chunks.append(self._play_queue.get_nowait())
            except Empty:
                break
        
        if not chunks:
            outdata.fill(0)
            return

        # Mixagem usando numpy (substitui audioop.add)
        arrays = [np.frombuffer(c, dtype=np.int16) for c in chunks]
        
        # Encontra o tamanho mínimo entre os chunks e o buffer solicitado
        min_len = min(min(len(a) for a in arrays), frames * A_CHANNELS)
        
        # Soma os chunks em int32 para evitar overflow
        mixed = np.zeros(min_len, dtype=np.int32)
        for a in arrays:
            mixed += a[:min_len]
        
        # Limita os valores ao range do int16 e converte
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        
        # Preenche o buffer de saída do sounddevice
        # outdata tem formato (frames, channels)
        outdata[:min_len, 0] = mixed
        if min_len < frames:
            outdata[min_len:, 0] = 0

    def play(self, audio_bytes):
        try:
            self._play_queue.put_nowait(audio_bytes)
        except:
            pass

    def set_muted(self, muted):
        self.muted = muted

    def play(self, audio_bytes):
        try:
            self._play_queue.put_nowait(audio_bytes)
        except Full:
            pass  # descarta se a fila estiver cheia

    def set_muted(self, muted):
        self.muted = muted


# ── Aplicação Principal ───────────────────────────────────────────────────────

class LocalChatApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('LocalChat')
        self.geometry('960x660')
        self.minsize(720, 520)
        self.configure(bg=BG0)

        self._font = best_font()

        # Estado do perfil
        self.profile_name      = tk.StringVar(value='')
        self.profile_avatar_b64 = ''
        self._profile_pil       = None   # PIL Image do avatar

        # Estado da sessão
        self.server    = None
        self.client    = None
        self.audio     = AudioEngine()
        self.discovery = None
        self.rooms     = {}       # host -> info dict
        self.room_users = {}      # id -> {name, avatar}
        self.is_host   = False
        self.in_room   = False
        self.my_id     = None
        self.room_name = ''
        self.muted     = False

        # Voz do host
        self._host_voice_sock = None
        self._host_voice_addr = None

        # Widgets que precisamos referenciar
        self._room_entries      = {}   # host -> frame widget
        self._users_frame       = None
        self._chat_text         = None
        self._msg_var           = None
        self._msg_entry         = None
        self._mute_btn          = None
        self._avatar_label      = None
        self._avatar_btn        = None

        self._setup_styles()
        self._show_profile()

    # ─────────────────────────────────────────────────────────────────────────
    # Estilos
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        f = self._font
        s.configure('TFrame',     background=BG0)
        s.configure('S1.TFrame',  background=BG1)
        s.configure('S2.TFrame',  background=BG2)
        s.configure('TLabel',     background=BG0, foreground=TXT, font=(f, 10))
        s.configure('Title.TLabel', background=BG0, foreground=TXT, font=(f, 20, 'bold'))
        s.configure('Sub.TLabel', background=BG0, foreground=TX2, font=(f, 9))
        s.configure('Sidebar.TLabel', background=BG1, foreground=TXT, font=(f, 10))
        s.configure('TScrollbar', background=BG2, troughcolor=BG1, bordercolor=BG1,
                    arrowcolor=TX2, relief='flat')

    def _btn(self, parent, text, cmd, bg=BG2, fg=TXT, font_size=10, bold=False,
             padx=12, pady=6, width=None, cursor='hand2', relief='flat'):
        weight = 'bold' if bold else 'normal'
        kw = dict(text=text, command=cmd, bg=bg, fg=fg, relief=relief,
                  font=(self._font, font_size, weight), padx=padx, pady=pady,
                  cursor=cursor, activebackground=bg, activeforeground=fg,
                  bd=0, highlightthickness=0)
        if width:
            kw['width'] = width
        return tk.Button(parent, **kw)

    def _label(self, parent, text, fg=TXT, bg=BG0, size=10, bold=False, **kw):
        weight = 'bold' if bold else 'normal'
        return tk.Label(parent, text=text, fg=fg, bg=bg,
                        font=(self._font, size, weight), **kw)

    def _entry(self, parent, var, width=24, size=12, **kw):
        return tk.Entry(parent, textvariable=var, bg=BG2, fg=TXT,
                        insertbackground=TXT, relief='flat', bd=6,
                        font=(self._font, size), width=width,
                        highlightthickness=1, highlightcolor=ACC,
                        highlightbackground=BDR, **kw)

    def _sep(self, parent, orient='x', color=BDR, thickness=1):
        if orient == 'x':
            tk.Frame(parent, bg=color, height=thickness).pack(fill='x')
        else:
            tk.Frame(parent, bg=color, width=thickness).pack(fill='y', side='left')

    # ─────────────────────────────────────────────────────────────────────────
    # Tela de Perfil
    # ─────────────────────────────────────────────────────────────────────────

    def _show_profile(self):
        self._clear()
        self.title('LocalChat — Perfil')

        outer = tk.Frame(self, bg=BG0)
        outer.place(relx=0.5, rely=0.5, anchor='center')

        # Cabeçalho
        self._label(outer, 'LocalChat', fg=ACC, size=30, bold=True).pack(pady=(0, 4))
        self._label(outer, 'Chat de voz e texto para sua rede local',
                    fg=TX2, size=10).pack(pady=(0, 28))

        # Avatar clicável
        av_frame = tk.Frame(outer, bg=BG0)
        av_frame.pack(pady=(0, 6))

        self._avatar_label = tk.Label(av_frame, bg=BG0, cursor='hand2')
        self._avatar_label.pack()
        self._avatar_label.bind('<Button-1>', lambda e: self._pick_avatar())

        self._refresh_avatar_preview(80)

        self._label(outer, '📷  Clique para escolher foto de perfil',
                    fg=TX2, size=9, bg=BG0).pack(pady=(0, 24))

        # Campo de nome
        self._label(outer, 'Seu nome', fg=TX2, size=9, bg=BG0).pack(anchor='w')

        name_entry = self._entry(outer, self.profile_name, width=26, size=13)
        name_entry.pack(pady=(4, 24), ipady=7)
        name_entry.focus_set()
        name_entry.bind('<Return>',    lambda e: self._profile_done())
        name_entry.bind('<KeyRelease>', lambda e: self._refresh_avatar_preview(80))

        self._btn(outer, 'Entrar na rede  →', self._profile_done,
                  bg=ACC, fg='white', font_size=11, bold=True,
                  padx=24, pady=10).pack()

    def _refresh_avatar_preview(self, size=80):
        if not PIL_OK or self._avatar_label is None:
            return
        if self._profile_pil:
            img = make_round_tkimg(self._profile_pil, size)
        else:
            name = self.profile_name.get() or '?'
            pil = make_avatar_img(name, size)
            img = make_round_tkimg(pil, size) if pil else None

        if img:
            self._avatar_label.config(image=img, text='', bg=BG0)
            self._avatar_label._img = img
        else:
            self._avatar_label.config(
                text='👤', font=(self._font, 40), fg=TX2, image='', bg=BG0)

    def _pick_avatar(self):
        path = filedialog.askopenfilename(
            title='Escolher foto de perfil',
            filetypes=[('Imagens', '*.png *.jpg *.jpeg *.gif *.bmp *.webp')])
        if not path:
            return
        try:
            img = Image.open(path).convert('RGBA')
            w, h = img.size
            side = min(w, h)
            img  = img.crop(((w - side) // 2, (h - side) // 2,
                             (w + side) // 2, (h + side) // 2))
            img  = img.resize((300, 300), Image.LANCZOS)
            self._profile_pil       = img
            self.profile_avatar_b64 = img_to_b64(img, 200)
            self._refresh_avatar_preview(80)
        except Exception as e:
            messagebox.showerror('Erro', f'Não foi possível carregar imagem:\n{e}')

    def _profile_done(self):
        name = self.profile_name.get().strip()
        if not name:
            messagebox.showwarning('Nome obrigatório',
                                   'Por favor, digite seu nome para continuar.')
            return
        if not self.profile_avatar_b64 and PIL_OK:
            pil = make_avatar_img(name, 200)
            if pil:
                self.profile_avatar_b64 = img_to_b64(pil, 200)
        self._show_lobby()

    # ─────────────────────────────────────────────────────────────────────────
    # Lobby
    # ─────────────────────────────────────────────────────────────────────────

    def _show_lobby(self):
        self._clear()
        self.title('LocalChat — Salas')
        self.rooms        = {}
        self._room_entries = {}

        # Inicia descoberta de salas
        self.discovery = RoomDiscovery(self._on_room_found)
        self.discovery.start()

        # ── Barra superior ──
        topbar = tk.Frame(self, bg=BG1, padx=16, pady=10)
        topbar.pack(fill='x')

        # Badge do perfil
        pf_frame = tk.Frame(topbar, bg=BG1, cursor='hand2')
        pf_frame.pack(side='left')
        pf_frame.bind('<Button-1>', lambda e: self._show_profile())

        if PIL_OK and self.profile_avatar_b64:
            pil  = b64_to_pil(self.profile_avatar_b64)
            timg = make_round_tkimg(pil, 34)
            if timg:
                al = tk.Label(pf_frame, image=timg, bg=BG1, cursor='hand2')
                al._img = timg
                al.pack(side='left', padx=(0, 8))
                al.bind('<Button-1>', lambda e: self._show_profile())

        self._label(pf_frame, self.profile_name.get(), fg=TXT, bg=BG1, size=11, bold=True
                    ).pack(side='left')

        # Título central
        self._label(topbar, '📡  LocalChat', fg=ACC, bg=BG1, size=14, bold=True
                    ).pack(side='left', padx=20)

        # Botões
        btn_frame = tk.Frame(topbar, bg=BG1)
        btn_frame.pack(side='right')

        self._btn(btn_frame, '✏ Editar Perfil', self._show_profile,
                  bg=BG2, fg=TX2, font_size=9, padx=10, pady=4
                  ).pack(side='left', padx=4)

        self._btn(btn_frame, '＋ Criar Sala', self._dialog_create_room,
                  bg=ACC, fg='white', font_size=10, bold=True, padx=14, pady=6
                  ).pack(side='left')

        self._sep(self)

        # ── Corpo ──
        body = tk.Frame(self, bg=BG0)
        body.pack(fill='both', expand=True, padx=24, pady=18)

        self._label(body, 'Salas disponíveis na rede', fg=TXT, size=14, bold=True
                    ).pack(anchor='w')
        self._label(body, 'Salas descobertas automaticamente. Funciona com LAN e Tailscale.',
                    fg=TX2, size=9).pack(anchor='w', pady=(2, 14))

        # Lista de salas
        list_wrap = tk.Frame(body, bg=BG1, bd=1, relief='solid',
                             highlightbackground=BDR, highlightthickness=1)
        list_wrap.pack(fill='both', expand=True)

        self._room_canvas = tk.Canvas(list_wrap, bg=BG1, highlightthickness=0, bd=0)
        self._room_canvas.pack(side='left', fill='both', expand=True)

        vsb = ttk.Scrollbar(list_wrap, orient='vertical',
                            command=self._room_canvas.yview)
        vsb.pack(side='right', fill='y')
        self._room_canvas.configure(yscrollcommand=vsb.set)

        self._rooms_inner = tk.Frame(self._room_canvas, bg=BG1)
        self._rooms_cwin  = self._room_canvas.create_window(
            (0, 0), window=self._rooms_inner, anchor='nw')

        self._rooms_inner.bind('<Configure>',
            lambda e: self._room_canvas.configure(
                scrollregion=self._room_canvas.bbox('all')))
        self._room_canvas.bind('<Configure>',
            lambda e: self._room_canvas.itemconfig(self._rooms_cwin, width=e.width))

        self._no_rooms_lbl = self._label(
            self._rooms_inner,
            '🔍  Procurando salas na rede...',
            fg=TX2, size=11, bg=BG1)
        self._no_rooms_lbl.pack(expand=True, pady=50)

        # ── Conexão manual (Tailscale / IP fixo) ──
        manual_frame = tk.Frame(body, bg=BG0)
        manual_frame.pack(fill='x', pady=(14, 0))

        self._label(manual_frame, '🔗  Tailscale / IP manual:', fg=TX2, size=9
                    ).pack(side='left')

        self._manual_ip = tk.StringVar()
        tk.Entry(manual_frame, textvariable=self._manual_ip,
                 bg=BG2, fg=TXT, insertbackground=TXT, relief='flat', bd=5,
                 font=(self._font, 10), width=20,
                 highlightthickness=1, highlightcolor=ACC,
                 highlightbackground=BDR).pack(side='left', padx=8, ipady=4)

        self._btn(manual_frame, 'Conectar', self._manual_connect,
                  bg=BG2, fg=TXT, font_size=9, padx=10, pady=4).pack(side='left')

        # Agenda limpeza de salas antigas
        self._schedule_room_cleanup()

    def _on_room_found(self, info):
        self.after(0, lambda i=info: self._add_room_entry(i))

    def _add_room_entry(self, info):
        host = info['host']
        # Não mostrar a própria sala se for host
        if host == local_ip():
            return
        if host in self._room_entries:
            # Atualizar contagem
            self.rooms[host] = info
            return

        self.rooms[host] = info

        if self._no_rooms_lbl:
            self._no_rooms_lbl.pack_forget()

        f = tk.Frame(self._rooms_inner, bg=BG2, cursor='hand2')
        f.pack(fill='x', padx=10, pady=5, ipady=2)
        f.bind('<Button-1>', lambda e, h=host: self._join_room(h))

        icon = tk.Label(f, text='🏠', bg=BG2, font=(self._font, 22), padx=12)
        icon.pack(side='left', pady=8)
        icon.bind('<Button-1>', lambda e, h=host: self._join_room(h))

        info_col = tk.Frame(f, bg=BG2)
        info_col.pack(side='left', fill='x', expand=True, pady=8)
        info_col.bind('<Button-1>', lambda e, h=host: self._join_room(h))

        name_lbl = tk.Label(info_col, text=info['room'], bg=BG2, fg=TXT,
                            font=(self._font, 11, 'bold'), cursor='hand2')
        name_lbl.pack(anchor='w')
        name_lbl.bind('<Button-1>', lambda e, h=host: self._join_room(h))

        sub_lbl = tk.Label(info_col,
                           text=f"Host: {host}  •  {info.get('users',1)} usuário(s)",
                           bg=BG2, fg=TX2, font=(self._font, 8))
        sub_lbl.pack(anchor='w')
        sub_lbl.bind('<Button-1>', lambda e, h=host: self._join_room(h))

        btn = self._btn(f, 'Entrar →', lambda h=host: self._join_room(h),
                        bg=ACC, fg='white', font_size=10, bold=True, pady=5)
        btn.pack(side='right', padx=12, pady=8)

        self._room_entries[host] = f

    def _schedule_room_cleanup(self):
        if not hasattr(self, '_rooms_inner'):
            return
        if self.discovery:
            for host in self.discovery.get_stale_hosts():
                w = self._room_entries.pop(host, None)
                if w:
                    w.destroy()
                self.rooms.pop(host, None)

        if len(self._room_entries) == 0 and self._no_rooms_lbl:
            self._no_rooms_lbl.pack(expand=True, pady=50)

        self.after(3000, self._schedule_room_cleanup)

    def _manual_connect(self):
        ip = self._manual_ip.get().strip()
        if not ip:
            messagebox.showwarning('IP necessário', 'Digite o endereço IP do host.')
            return
        self._do_join(ip)

    def _join_room(self, host):
        self._do_join(host)

    # ─────────────────────────────────────────────────────────────────────────
    # Criar Sala
    # ─────────────────────────────────────────────────────────────────────────

    def _dialog_create_room(self):
        dlg = tk.Toplevel(self)
        dlg.title('Criar Nova Sala')
        dlg.geometry('380x220')
        dlg.configure(bg=BG0)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        # Centralizar
        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width()  - 380) // 2
        y = self.winfo_y() + (self.winfo_height() - 220) // 2
        dlg.geometry(f'+{x}+{y}')

        self._label(dlg, 'Criar Nova Sala', fg=TXT, size=14, bold=True
                    ).pack(pady=(24, 4))
        self._label(dlg, 'Outros usuários na rede poderão entrar automaticamente.',
                    fg=TX2, size=9).pack(pady=(0, 16))

        self._label(dlg, 'Nome da sala', fg=TX2, size=9).pack(anchor='w', padx=30)

        room_var = tk.StringVar(value=f'Sala de {self.profile_name.get()}')
        entry = self._entry(dlg, room_var, width=28, size=12)
        entry.pack(padx=30, pady=(4, 20), ipady=7)
        entry.select_range(0, 'end')
        entry.focus_set()

        def do_create():
            name = room_var.get().strip()
            if not name:
                messagebox.showwarning('Nome', 'Digite um nome para a sala.')
                return
            dlg.destroy()
            if self.discovery:
                self.discovery.stop()
                self.discovery = None
            self._enter_room(room_name=name, is_host=True)

        self._btn(dlg, '✓ Criar Sala', do_create, bg=GRN, fg='white',
                  font_size=11, bold=True, padx=24, pady=9).pack()

        entry.bind('<Return>', lambda e: do_create())

    # ─────────────────────────────────────────────────────────────────────────
    # Entrar na Sala
    # ─────────────────────────────────────────────────────────────────────────

    def _do_join(self, host):
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        self._enter_room(host=host, is_host=False)

    def _enter_room(self, host=None, room_name=None, is_host=False):
        self.is_host    = is_host
        self.room_users = {}
        self.muted      = False

        if is_host:
            try:
                self.server = ChatServer(room_name, self._on_server_event)
                self.server.start()
            except Exception as e:
                messagebox.showerror('Erro ao criar sala',
                                     f'Não foi possível iniciar o servidor:\n{e}')
                self._show_lobby()
                return
            self.my_id     = 0
            self.room_name = room_name
            self.room_users[0] = {
                'name':   self.profile_name.get(),
                'avatar': self.profile_avatar_b64,
            }

            # Socket UDP do host para receber voz dos clientes
            self._host_voice_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._host_voice_sock.bind(('127.0.0.1', 0))
            self._host_voice_sock.settimeout(1.0)
            self._host_voice_addr = self._host_voice_sock.getsockname()
            self.server.register_host_voice_addr(self._host_voice_addr)
            threading.Thread(target=self._host_voice_recv, daemon=True).start()

        else:
            try:
                self.client = ChatClient(
                    host,
                    self.profile_name.get(),
                    self.profile_avatar_b64,
                    self._on_client_event,
                )
                info = self.client.connect()
                self.my_id     = self.client.cid
                self.room_name = info.get('room', 'Sala')
                for u in info.get('users', []):
                    self.room_users[u['id']] = {
                        'name':   u['name'],
                        'avatar': u.get('avatar', ''),
                    }
            except Exception as e:
                messagebox.showerror('Erro ao conectar',
                                     f'Não foi possível conectar à sala:\n{e}')
                self._show_lobby()
                return

        # Iniciar áudio
        if self.audio.available:
            self.audio.start(self._on_audio_capture)

        self.in_room = True
        self._build_room_screen()

    def _host_voice_recv(self):
        """Recebe áudio relayado pelo servidor e toca."""
        while self.in_room and self._host_voice_sock:
            try:
                data, _ = self._host_voice_sock.recvfrom(65535)
                if len(data) > 4:
                    audio = data[4:]
                    if self.audio.available:
                        self.audio.play(audio)
            except socket.timeout:
                continue
            except:
                break

    # ─────────────────────────────────────────────────────────────────────────
    # Tela da Sala
    # ─────────────────────────────────────────────────────────────────────────

    def _build_room_screen(self):
        self._clear()
        self.title(f'LocalChat — {self.room_name}')

        # ── Barra superior ──
        hdr = tk.Frame(self, bg=BG1, padx=14, pady=9)
        hdr.pack(fill='x')

        self._btn(hdr, '← Sair', self._leave_room,
                  bg=BG2, fg=TX2, font_size=9, padx=10, pady=4
                  ).pack(side='left')

        room_ic = '🏠' if self.is_host else '💬'
        self._label(hdr, f'{room_ic}  {self.room_name}',
                    fg=TXT, bg=BG1, size=13, bold=True).pack(side='left', padx=14)

        role_text = '(anfitrião)' if self.is_host else '(participante)'
        self._label(hdr, role_text, fg=TX2, bg=BG1, size=9).pack(side='left')

        ip_text = f'IP: {local_ip()}'
        self._label(hdr, ip_text, fg=TX3, bg=BG1, size=8).pack(side='right')

        self._sep(self)

        # ── Corpo: sidebar + chat ──
        body = tk.Frame(self, bg=BG0)
        body.pack(fill='both', expand=True)

        # ── Sidebar ──
        sidebar = tk.Frame(body, bg=BG1, width=230)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)

        self._label(sidebar, 'USUÁRIOS NA SALA', fg=TX3, bg=BG1, size=8, bold=True
                    ).pack(anchor='w', padx=12, pady=(14, 6))

        # Frame de usuários (scrollable)
        users_wrap = tk.Frame(sidebar, bg=BG1)
        users_wrap.pack(fill='both', expand=True, padx=4)

        self._users_canvas = tk.Canvas(users_wrap, bg=BG1,
                                       highlightthickness=0, bd=0)
        self._users_canvas.pack(side='left', fill='both', expand=True)

        u_vsb = ttk.Scrollbar(users_wrap, orient='vertical',
                              command=self._users_canvas.yview)
        u_vsb.pack(side='right', fill='y')
        self._users_canvas.configure(yscrollcommand=u_vsb.set)

        self._users_frame = tk.Frame(self._users_canvas, bg=BG1)
        self._users_cwin  = self._users_canvas.create_window(
            (0, 0), window=self._users_frame, anchor='nw')

        self._users_frame.bind('<Configure>',
            lambda e: self._users_canvas.configure(
                scrollregion=self._users_canvas.bbox('all')))
        self._users_canvas.bind('<Configure>',
            lambda e: self._users_canvas.itemconfig(self._users_cwin, width=e.width))

        # ── Painel de controles (voz + perfil) ──
        vc_panel = tk.Frame(sidebar, bg=BG1)
        vc_panel.pack(fill='x', side='bottom', pady=0)

        self._sep(vc_panel, color=BDR)

        # Mini-perfil
        mini_pf = tk.Frame(vc_panel, bg=BG1)
        mini_pf.pack(fill='x', padx=10, pady=(8, 4))

        if PIL_OK and self.profile_avatar_b64:
            pil  = b64_to_pil(self.profile_avatar_b64)
            timg = make_round_tkimg(pil, 30)
            if timg:
                al = tk.Label(mini_pf, image=timg, bg=BG1)
                al._img = timg
                al.pack(side='left', padx=(0, 7))

        self._label(mini_pf, self.profile_name.get(),
                    fg=TXT, bg=BG1, size=10, bold=True).pack(side='left')

        # Botão Mudo
        self._mute_icon_var = tk.StringVar(value='🎤  Microfone ativo')
        self._mute_btn = tk.Button(
            vc_panel,
            textvariable=self._mute_icon_var,
            bg=GRN, fg='white', relief='flat',
            font=(self._font, 10, 'bold'),
            padx=12, pady=7, cursor='hand2',
            activebackground=GRN, activeforeground='white',
            bd=0, highlightthickness=0,
            command=self._toggle_mute,
        )
        self._mute_btn.pack(fill='x', padx=10, pady=(4, 12))

        if not self.audio.available:
            self._label(vc_panel,
                        '⚠ pyaudio não disponível\n  Voz desativada',
                        fg=YEL, bg=BG1, size=8).pack(padx=10, pady=(0, 8))

        # ── Separador vertical ──
        tk.Frame(body, bg=BDR, width=1).pack(side='left', fill='y')

        # ── Área de chat ──
        chat_area = tk.Frame(body, bg=BG0)
        chat_area.pack(side='left', fill='both', expand=True)

        # Mensagens
        self._chat_text = tk.Text(
            chat_area,
            bg=BG0, fg=TXT, relief='flat',
            font=(self._font, 10),
            state='disabled',
            padx=16, pady=12,
            wrap='word',
            insertbackground=TXT,
            bd=0, highlightthickness=0,
            cursor='arrow',
        )
        self._chat_text.pack(fill='both', expand=True)

        # Tags de formatação
        f = self._font
        self._chat_text.tag_config('name',   foreground=ACC, font=(f, 10, 'bold'))
        self._chat_text.tag_config('name_me',foreground=PRP, font=(f, 10, 'bold'))
        self._chat_text.tag_config('time',   foreground=TX3, font=(f, 8))
        self._chat_text.tag_config('msg',    foreground=TXT, font=(f, 10))
        self._chat_text.tag_config('sys',    foreground=YEL, font=(f, 9, 'italic'))

        # Scrollbar do chat
        chat_vsb = ttk.Scrollbar(self._chat_text, orient='vertical',
                                  command=self._chat_text.yview)
        chat_vsb.pack(side='right', fill='y')
        self._chat_text.configure(yscrollcommand=chat_vsb.set)

        # ── Barra de input ──
        self._sep(chat_area, color=BDR)
        input_bar = tk.Frame(chat_area, bg=BG1, pady=0)
        input_bar.pack(fill='x')

        inp = tk.Frame(input_bar, bg=BG1)
        inp.pack(fill='x', padx=12, pady=10)

        self._msg_var   = tk.StringVar()
        self._msg_entry = tk.Entry(
            inp,
            textvariable=self._msg_var,
            bg=BG2, fg=TXT, insertbackground=TXT,
            relief='flat', bd=6,
            font=(self._font, 11),
            highlightthickness=1,
            highlightcolor=ACC,
            highlightbackground=BDR,
        )
        self._msg_entry.pack(side='left', fill='x', expand=True, ipady=7)
        self._msg_entry.bind('<Return>', lambda e: self._send_message())
        self._msg_entry.focus_set()

        send_btn = self._btn(inp, '↑ Enviar', self._send_message,
                             bg=ACC, fg='white', font_size=10, bold=True,
                             padx=14, pady=7)
        send_btn.pack(side='left', padx=(8, 0))

        # Mensagem inicial
        host_msg = '🏠  Sala criada! Aguardando participantes...' \
                   if self.is_host else '✅  Conectado à sala!'
        self._add_system_msg(host_msg)

        # Popula a lista de usuários
        self._refresh_users()

    # ─────────────────────────────────────────────────────────────────────────
    # Lista de Usuários
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_users(self):
        if self._users_frame is None:
            return
        for w in self._users_frame.winfo_children():
            w.destroy()

        for uid, user in sorted(self.room_users.items()):
            is_me = (uid == self.my_id)
            uf = tk.Frame(self._users_frame, bg=BG1)
            uf.pack(fill='x', pady=2, padx=4)

            # Avatar
            pil  = b64_to_pil(user.get('avatar', '')) if user.get('avatar') else None
            if pil is None and PIL_OK:
                pil = make_avatar_img(user['name'], 30)
            timg = make_round_tkimg(pil, 30) if pil else None

            if timg:
                al = tk.Label(uf, image=timg, bg=BG1)
                al._img = timg
                al.pack(side='left', padx=(2, 8), pady=3)
            else:
                tk.Label(uf, text='👤', bg=BG1, font=(self._font, 16)
                         ).pack(side='left', padx=(2, 6))

            # Nome
            display_name = user['name'] + (' (você)' if is_me else '')
            fg_color = PRP if is_me else TXT
            tk.Label(uf, text=display_name, bg=BG1, fg=fg_color,
                     font=(self._font, 10, 'bold' if is_me else 'normal')
                     ).pack(side='left')

    # ─────────────────────────────────────────────────────────────────────────
    # Chat de Texto
    # ─────────────────────────────────────────────────────────────────────────

    def _add_system_msg(self, text):
        if self._chat_text is None:
            return
        self._chat_text.config(state='normal')
        self._chat_text.insert('end', f'  {text}\n\n', 'sys')
        self._chat_text.config(state='disabled')
        self._chat_text.see('end')

    def _add_chat_msg(self, uid, name, content, ts=None):
        if self._chat_text is None:
            return
        import datetime
        t     = datetime.datetime.fromtimestamp(ts or time.time()).strftime('%H:%M')
        is_me = (uid == self.my_id)
        name_tag = 'name_me' if is_me else 'name'

        self._chat_text.config(state='normal')
        self._chat_text.insert('end', f'  {name}', name_tag)
        self._chat_text.insert('end', f'  {t}\n', 'time')
        self._chat_text.insert('end', f'  {content}\n\n', 'msg')
        self._chat_text.config(state='disabled')
        self._chat_text.see('end')

    def _send_message(self):
        if self._msg_var is None:
            return
        content = self._msg_var.get().strip()
        if not content:
            return
        self._msg_var.set('')

        name = self.profile_name.get()
        # Mostra para o próprio usuário
        self._add_chat_msg(self.my_id, name, content)

        if self.is_host and self.server:
            self.server.broadcast_text(name, content)
        elif self.client:
            self.client.send_text(content)

    # ─────────────────────────────────────────────────────────────────────────
    # Controles de Voz
    # ─────────────────────────────────────────────────────────────────────────

    def _toggle_mute(self):
        self.muted = not self.muted
        self.audio.set_muted(self.muted)
        if self._mute_btn is None:
            return
        if self.muted:
            self._mute_icon_var.set('🔇  Mutado')
            self._mute_btn.config(bg=RED, activebackground=RED)
        else:
            self._mute_icon_var.set('🎤  Microfone ativo')
            self._mute_btn.config(bg=GRN, activebackground=GRN)

    def _on_audio_capture(self, data):
        """Chamado pela thread de áudio com bytes PCM capturados."""
        if not self.in_room:
            return
        if self.is_host and self._host_voice_sock and self._host_voice_addr:
            pkt = struct.pack('>I', 0) + data
            try:
                self._host_voice_sock.sendto(pkt, ('127.0.0.1', VOICE_PORT))
            except:
                pass
        elif self.client:
            self.client.send_voice(data)

    # ─────────────────────────────────────────────────────────────────────────
    # Handlers de Eventos
    # ─────────────────────────────────────────────────────────────────────────

    def _on_server_event(self, evt):
        """Chamado em threads do servidor → agenda no thread da UI."""
        self.after(0, lambda e=evt: self._handle_server_event(e))

    def _handle_server_event(self, evt):
        if not self.in_room:
            return
        t = evt.get('type')
        if t == 'user_join':
            self.room_users[evt['id']] = {
                'name':   evt['name'],
                'avatar': evt.get('avatar', ''),
            }
            self._add_system_msg(f"👋  {evt['name']} entrou na sala")
            self._refresh_users()
        elif t == 'user_leave':
            self.room_users.pop(evt['id'], None)
            self._add_system_msg(f"🚪  {evt['name']} saiu da sala")
            self._refresh_users()
        elif t == 'text':
            # Não duplicar mensagem do host (já exibida em _send_message)
            if evt.get('id') != 0:
                self._add_chat_msg(evt['id'], evt['name'],
                                   evt['content'], evt.get('ts'))

    def _on_client_event(self, evt):
        """Chamado em threads do cliente → agenda no thread da UI."""
        self.after(0, lambda e=evt: self._handle_client_event(e))

    def _handle_client_event(self, evt):
        if not self.in_room:
            return
        t = evt.get('type')

        if t == 'text':
            # Não duplicar mensagem própria
            if evt.get('id') != self.my_id:
                self._add_chat_msg(evt['id'], evt['name'],
                                   evt['content'], evt.get('ts'))

        elif t == 'user_join':
            uid = evt['id']
            self.room_users[uid] = {
                'name':   evt['name'],
                'avatar': evt.get('avatar', ''),
            }
            self._add_system_msg(f"👋  {evt['name']} entrou na sala")
            self._refresh_users()

        elif t == 'user_leave':
            uid  = evt['id']
            name = self.room_users.get(uid, {}).get('name', '?')
            self.room_users.pop(uid, None)
            self._add_system_msg(f"🚪  {name} saiu da sala")
            self._refresh_users()

        elif t == 'voice':
            audio = evt.get('audio', b'')
            if audio and self.audio.available:
                self.audio.play(audio)

        elif t == 'disconnected':
            messagebox.showwarning('Desconectado',
                                   'A conexão com a sala foi perdida.')
            self._leave_room()

    # ─────────────────────────────────────────────────────────────────────────
    # Sair da Sala
    # ─────────────────────────────────────────────────────────────────────────

    def _leave_room(self):
        self.in_room = False

        if self.audio.available:
            self.audio.stop()

        if self.client:
            self.client.disconnect()
            self.client = None

        if self.server:
            self.server.stop()
            self.server = None

        if self._host_voice_sock:
            try: self._host_voice_sock.close()
            except: pass
            self._host_voice_sock = None

        self._users_frame = None
        self._chat_text   = None
        self._msg_var     = None
        self._msg_entry   = None
        self._mute_btn    = None

        self._show_lobby()

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários de UI
    # ─────────────────────────────────────────────────────────────────────────

    def _clear(self):
        for w in self.winfo_children():
            w.destroy()

    def on_close(self):
        if self.in_room:
            self._leave_room()
        if self.discovery:
            self.discovery.stop()
        self.audio.terminate()
        self.destroy()


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = LocalChatApp()
    app.protocol('WM_DELETE_WINDOW', app.on_close)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.on_close()
