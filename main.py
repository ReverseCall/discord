import socket
import threading
import sounddevice as sd
import numpy as np
import noisereduce as nr
import psutil  # pip install psutil

# ---------------- CONFIGURAÇÕES ----------------
PORT = 5000
CHUNK = 1024
FS = 44100
NOISE_REDUCTION = 50  # Percentual do filtro de ruído (0-100%)

# ---------------- FUNÇÕES DE ÁUDIO ----------------
def send_audio(target_ip):
    """Captura áudio do microfone, aplica filtro de ruído e envia para o host/cliente."""
    def callback(indata, frames, time, status):
        data = indata.copy()
        # Redução de ruído
        data = nr.reduce_noise(y=data.flatten(), sr=FS, prop_decrease=NOISE_REDUCTION/100.0)
        sock.sendto(data.tobytes(), (target_ip, PORT))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with sd.InputStream(channels=1, samplerate=FS, callback=callback, blocksize=CHUNK):
        print(f"Transmitindo áudio para {target_ip}...")
        threading.Event().wait()  # mantém a thread rodando

def receive_audio():
    """Recebe áudio via UDP e reproduz no alto-falante."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", PORT))
    print("Recebendo áudio...")
    while True:
        data, addr = sock.recvfrom(CHUNK*4)
        audio = np.frombuffer(data, dtype=np.float32)
        sd.play(audio, FS)
        sd.wait()

# ---------------- FUNÇÕES DE IP ----------------
def get_tailscale_ip():
    """Verifica se existe interface Tailscale e retorna seu IP."""
    for iface_name, iface_addrs in psutil.net_if_addrs().items():
        if "tailscale" in iface_name.lower():
            for addr in iface_addrs:
                if addr.family == socket.AF_INET:
                    return addr.address
    return None

def get_local_ip():
    """Retorna o IP Tailscale se existir, senão o IP da rede local."""
    ts_ip = get_tailscale_ip()
    if ts_ip:
        return ts_ip
    # IP da LAN
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

# ---------------- MENU PRINCIPAL ----------------
mode = input("Você quer criar uma sala (host) ou entrar (client)? [host/client]: ").strip().lower()

if mode == "host":
    host_ip = get_local_ip()
    print(f"\nSala criada! IP que deve ser compartilhado com outros usuários: {host_ip}\n")
    threading.Thread(target=receive_audio, daemon=True).start()
    input("Pressione Enter para encerrar a sala...\n")

elif mode == "client":
    target_ip = input("Digite o IP do host: ")
    threading.Thread(target=send_audio, args=(target_ip,), daemon=True).start()
    threading.Thread(target=receive_audio, daemon=True).start()
    input("Pressione Enter para sair...\n")

else:
    print("Opção inválida. Use 'host' ou 'client'.")