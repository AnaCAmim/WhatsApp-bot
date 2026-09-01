"""
gui.py
------
Interface gráfica (Tkinter) para o Disparador de WhatsApp.

Fluxo de uso:
  1. Clicar em "Conectar" e escanear o QR Code (só na primeira vez;
     depois a sessão fica salva na pasta chrome-profile/).
  2. Carregar um arquivo .csv (colunas: nome,telefone) ou .txt
     (um telefone por linha) com os contatos.
  3. Escrever a mensagem (pode usar {nome} para personalizar).
  4. Ajustar o intervalo entre envios e clicar em "Iniciar disparo".
"""

import csv
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, scrolledtext

from bot import WhatsAppBot

PROFILE_PATH = Path("chrome-profile").resolve()

COR_FUNDO = "#eef1f6"
COR_CARD = "#ffffff"
COR_BORDA = "#dfe4ea"
COR_PRIMARIA = "#1f8a4c"
COR_PRIMARIA_HOVER = "#176b3a"
COR_PRIMARIA_ESCURA = "#12592e"
COR_PERIGO = "#c0392b"
COR_TEXTO = "#1a2733"
COR_TEXTO_SEC = "#5f6b7a"

EXTENSOES_MIDIA = [
    ("Imagens e vídeos", "*.png *.jpg *.jpeg *.gif *.webp *.mp4 *.mov *.avi *.mkv"),
    ("Imagens", "*.png *.jpg *.jpeg *.gif *.webp"),
    ("Vídeos", "*.mp4 *.mov *.avi *.mkv"),
    ("Todos os arquivos", "*.*"),
]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Disparador de WhatsApp")
        self.root.geometry("820x880")
        self.root.minsize(720, 720)
        self.root.configure(background=COR_FUNDO)

        self._configurar_estilos()

        self.bot = WhatsAppBot(PROFILE_PATH, log_callback=self._enfileirar_log)
        self.contatos = []
        self.caminho_midia: str | None = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.progress_queue: "queue.Queue[tuple[int, int]]" = queue.Queue()
        self.enviando = False

        self._montar_interface()
        self._processar_filas()

    # ------------------------------------------------------------
    # Estilo visual
    # ------------------------------------------------------------
    def _configurar_estilos(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=COR_FUNDO)
        style.configure("Card.TFrame", background=COR_CARD)

        style.configure("TLabelframe", background=COR_CARD, borderwidth=1,
                        relief="solid", bordercolor=COR_BORDA)
        style.configure("TLabelframe.Label", background=COR_CARD,
                        font=("Segoe UI", 10, "bold"), foreground=COR_TEXTO)

        style.configure("TLabel", background=COR_CARD, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=COR_PRIMARIA_ESCURA,
                         font=("Segoe UI", 18, "bold"), foreground="white")
        style.configure("HeaderSub.TLabel", background=COR_PRIMARIA_ESCURA,
                         font=("Segoe UI", 9), foreground="#dcf5e6")
        style.configure("Sub.TLabel", background=COR_CARD,
                         foreground=COR_TEXTO_SEC, font=("Segoe UI", 9))
        style.configure("Aviso.TLabel", background=COR_CARD,
                         foreground="#a15c00", font=("Segoe UI", 9))

        style.configure("TButton", font=("Segoe UI", 9), padding=7)

        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"),
                         padding=10, foreground="white", background=COR_PRIMARIA)
        style.map("Accent.TButton",
                   background=[("active", COR_PRIMARIA_HOVER), ("disabled", "#9fbfae")])

        style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"),
                         padding=10, foreground="white", background=COR_PERIGO)
        style.map("Danger.TButton",
                   background=[("active", "#96281d"), ("disabled", "#e0a9a2")])

        style.configure("Horizontal.TProgressbar", background=COR_PRIMARIA, thickness=14,
                         troughcolor="#e2e6ea", bordercolor="#e2e6ea")

    # ------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------
    def _montar_interface(self):
        pad = {"padx": 16, "pady": 8}

        # ---------- Banner de cabeçalho ----------
        cabecalho = tk.Frame(self.root, background=COR_PRIMARIA_ESCURA)
        cabecalho.pack(fill="x")

        titulo = ttk.Label(cabecalho, text="📨  Disparador de WhatsApp", style="Header.TLabel")
        titulo.pack(anchor="w", padx=18, pady=(16, 0))

        subtitulo = ttk.Label(
            cabecalho,
            text="Automatize o envio de mensagens, imagens e vídeos pelo WhatsApp Web.",
            style="HeaderSub.TLabel",
        )
        subtitulo.pack(anchor="w", padx=18, pady=(2, 16))

        corpo = ttk.Frame(self.root, style="TFrame")
        corpo.pack(fill="both", expand=True)

        # ---------- Seção Conexão ----------
        frame_conexao = ttk.LabelFrame(corpo, text="🔌  1. Conexão")
        frame_conexao.pack(fill="x", **pad)

        linha_conexao = ttk.Frame(frame_conexao, style="Card.TFrame")
        linha_conexao.pack(fill="x", padx=10, pady=8)

        self.status_pill = tk.Label(linha_conexao, text="● Desconectado",
                                     font=("Segoe UI", 9, "bold"), foreground="#8a1c14",
                                     background="#fbe4e1", padx=10, pady=4)
        self.status_pill.pack(side="left")

        self.btn_conectar = ttk.Button(linha_conexao, text="Conectar ao WhatsApp",
                                        command=self._conectar)
        self.btn_conectar.pack(side="left", padx=12)

        # ---------- Seção Contatos ----------
        frame_contatos = ttk.LabelFrame(corpo, text="👥  2. Contatos")
        frame_contatos.pack(fill="x", **pad)

        linha1 = ttk.Frame(frame_contatos, style="Card.TFrame")
        linha1.pack(fill="x", padx=10, pady=8)

        self.btn_carregar = ttk.Button(linha1, text="Carregar arquivo (.csv ou .txt)",
                                        command=self._carregar_contatos)
        self.btn_carregar.pack(side="left")

        self.contatos_var = tk.StringVar(value="Nenhum contato carregado.")
        ttk.Label(linha1, textvariable=self.contatos_var, style="Sub.TLabel").pack(side="left", padx=12)

        ajuda = ttk.Label(
            frame_contatos,
            text='Formato CSV esperado: colunas "nome,telefone" (telefone com DDI, ex.: 5583999999999).\n'
                 "Se o arquivo for .txt, uma coluna só com telefones (um por linha) também funciona.",
            style="Sub.TLabel", justify="left",
        )
        ajuda.pack(anchor="w", padx=10, pady=(0, 10))

        # ---------- Seção Mensagem ----------
        frame_msg = ttk.LabelFrame(corpo, text="✉️  3. Mensagem")
        frame_msg.pack(fill="both", expand=False, **pad)

        ttk.Label(frame_msg, text='Use {nome} para personalizar a mensagem.',
                  style="Sub.TLabel").pack(anchor="w", padx=10, pady=(10, 4))

        self.txt_mensagem = tk.Text(frame_msg, height=6, wrap="word", font=("Segoe UI", 10),
                                     relief="solid", borderwidth=1, highlightthickness=1,
                                     highlightbackground=COR_BORDA, highlightcolor=COR_PRIMARIA)
        self.txt_mensagem.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.txt_mensagem.insert("1.0", "Olá {nome}, tudo bem?")

        # ---------- Seção Mídia ----------
        frame_midia = ttk.LabelFrame(corpo, text="🖼️  4. Imagem ou vídeo (opcional)")
        frame_midia.pack(fill="x", **pad)

        linha_midia = ttk.Frame(frame_midia, style="Card.TFrame")
        linha_midia.pack(fill="x", padx=10, pady=8)

        self.btn_midia = ttk.Button(linha_midia, text="Selecionar imagem/vídeo",
                                     command=self._selecionar_midia)
        self.btn_midia.pack(side="left")

        self.midia_var = tk.StringVar(value="Nenhum arquivo selecionado.")
        ttk.Label(linha_midia, textvariable=self.midia_var, style="Sub.TLabel").pack(side="left", padx=12)

        self.btn_remover_midia = ttk.Button(linha_midia, text="Remover",
                                             command=self._remover_midia, state="disabled")
        self.btn_remover_midia.pack(side="left", padx=6)

        ttk.Label(
            frame_midia,
            text="O texto da mensagem será enviado como legenda junto com o arquivo.",
            style="Sub.TLabel", justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 10))

        # ---------- Seção Configurações de envio ----------
        frame_cfg = ttk.LabelFrame(corpo, text="⏱️  5. Intervalo entre envios (segundos)")
        frame_cfg.pack(fill="x", **pad)

        linha_cfg = ttk.Frame(frame_cfg, style="Card.TFrame")
        linha_cfg.pack(fill="x", padx=10, pady=8, anchor="w")

        ttk.Label(linha_cfg, text="Mínimo:").grid(row=0, column=0, padx=(0, 4))
        self.spin_min = ttk.Spinbox(linha_cfg, from_=1, to=120, width=6)
        self.spin_min.set(5)
        self.spin_min.grid(row=0, column=1, padx=(0, 16))

        ttk.Label(linha_cfg, text="Máximo:").grid(row=0, column=2, padx=(0, 4))
        self.spin_max = ttk.Spinbox(linha_cfg, from_=1, to=180, width=6)
        self.spin_max.set(12)
        self.spin_max.grid(row=0, column=3)

        aviso = ttk.Label(
            frame_cfg,
            text="⚠️ Intervalos curtos ou envios sem consentimento aumentam o risco de "
                 "bloqueio do número pelo WhatsApp. Use com moderação e apenas para "
                 "contatos que autorizaram o recebimento.",
            style="Aviso.TLabel", justify="left", wraplength=700,
        )
        aviso.pack(anchor="w", padx=10, pady=(0, 10))

        # ---------- Seção Ações ----------
        frame_acoes = ttk.Frame(corpo, style="TFrame")
        frame_acoes.pack(fill="x", padx=16, pady=(4, 8))

        self.btn_iniciar = ttk.Button(frame_acoes, text="▶ Iniciar disparo", style="Accent.TButton",
                                       command=self._iniciar_disparo, state="disabled")
        self.btn_iniciar.pack(side="left")

        self.btn_parar = ttk.Button(frame_acoes, text="■ Parar", style="Danger.TButton",
                                     command=self._parar_disparo, state="disabled")
        self.btn_parar.pack(side="left", padx=8)

        self.progress = ttk.Progressbar(frame_acoes, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=12)

        self.progress_label_var = tk.StringVar(value="")
        ttk.Label(frame_acoes, textvariable=self.progress_label_var,
                  background=COR_FUNDO).pack(side="left")

        # ---------- Log ----------
        frame_log = ttk.LabelFrame(corpo, text="📜  Log")
        frame_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.log_widget = scrolledtext.ScrolledText(frame_log, height=10, state="disabled",
                                                      font=("Consolas", 9), background="#1e1e1e",
                                                      foreground="#d4d4d4", insertbackground="white",
                                                      relief="flat", borderwidth=0)
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=10)

        self.root.protocol("WM_DELETE_WINDOW", self._ao_fechar)

    # ------------------------------------------------------------
    # Log / progresso thread-safe
    # ------------------------------------------------------------
    def _enfileirar_log(self, mensagem: str):
        self.log_queue.put(mensagem)

    def _processar_filas(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_widget.configure(state="normal")
                self.log_widget.insert("end", msg + "\n")
                self.log_widget.see("end")
                self.log_widget.configure(state="disabled")
        except queue.Empty:
            pass

        try:
            while True:
                atual, total = self.progress_queue.get_nowait()
                self.progress["maximum"] = max(total, 1)
                self.progress["value"] = atual
                self.progress_label_var.set(f"{atual}/{total}")
        except queue.Empty:
            pass

        self.root.after(150, self._processar_filas)

    # ------------------------------------------------------------
    # Ações: conectar
    # ------------------------------------------------------------
    def _conectar(self):
        self.btn_conectar.configure(state="disabled")
        self.status_pill.configure(text="● Conectando...", foreground="#7a4a00", background="#fbe9cf")

        def tarefa():
            ok = self.bot.conectar()
            self.root.after(0, lambda: self._pos_conectar(ok))

        threading.Thread(target=tarefa, daemon=True).start()

    def _pos_conectar(self, ok: bool):
        if ok:
            self.status_pill.configure(text="● Conectado", foreground="#0f5c2e", background="#dcf5e6")
            self._atualizar_estado_botao_iniciar()
        else:
            self.status_pill.configure(text="● Falha na conexão", foreground="#8a1c14", background="#fbe4e1")
            messagebox.showerror("Conexão", "Não foi possível conectar ao WhatsApp Web. "
                                              "Veja o log para detalhes.")
        self.btn_conectar.configure(state="normal")

    # ------------------------------------------------------------
    # Ações: contatos
    # ------------------------------------------------------------
    def _carregar_contatos(self):
        caminho = filedialog.askopenfilename(
            title="Selecionar arquivo de contatos",
            filetypes=[("CSV ou texto", "*.csv *.txt"), ("Todos os arquivos", "*.*")],
        )
        if not caminho:
            return

        try:
            contatos = self._parsear_arquivo_contatos(Path(caminho))
        except Exception as exc:
            messagebox.showerror("Erro ao ler arquivo", str(exc))
            return

        if not contatos:
            messagebox.showwarning("Contatos", "Nenhum contato válido foi encontrado no arquivo.")
            return

        self.contatos = contatos
        self.contatos_var.set(f"{len(contatos)} contato(s) carregado(s).")
        self._enfileirar_log(f"{len(contatos)} contato(s) carregado(s) de {Path(caminho).name}")
        self._atualizar_estado_botao_iniciar()

    # ------------------------------------------------------------
    # Ações: mídia
    # ------------------------------------------------------------
    def _selecionar_midia(self):
        caminho = filedialog.askopenfilename(
            title="Selecionar imagem ou vídeo",
            filetypes=EXTENSOES_MIDIA,
        )
        if not caminho:
            return
        self.caminho_midia = caminho
        self.midia_var.set(Path(caminho).name)
        self.btn_remover_midia.configure(state="normal")
        self._enfileirar_log(f"Mídia selecionada: {Path(caminho).name}")

    def _remover_midia(self):
        self.caminho_midia = None
        self.midia_var.set("Nenhum arquivo selecionado.")
        self.btn_remover_midia.configure(state="disabled")

    @staticmethod
    def _parsear_arquivo_contatos(caminho: Path):
        contatos = []
        if caminho.suffix.lower() == ".csv":
            with open(caminho, newline="", encoding="utf-8-sig") as f:
                amostra = f.read(4096)
                f.seek(0)
                try:
                    dialeto = csv.Sniffer().sniff(amostra, delimiters=",;\t")
                    delimitador = dialeto.delimiter
                except csv.Error:
                    delimitador = ","
                leitor = csv.reader(f, delimiter=delimitador)
                linhas = list(leitor)
            if not linhas:
                return contatos

            cabecalho = [c.strip().lower() for c in linhas[0]]
            tem_cabecalho = "telefone" in cabecalho or "phone" in cabecalho
            inicio = 1 if tem_cabecalho else 0

            if tem_cabecalho:
                idx_nome = cabecalho.index("nome") if "nome" in cabecalho else None
                idx_tel = (cabecalho.index("telefone") if "telefone" in cabecalho
                           else cabecalho.index("phone"))
            else:
                idx_nome, idx_tel = (0, 1) if len(linhas[0]) > 1 else (None, 0)

            for linha in linhas[inicio:]:
                if not linha or not any(c.strip() for c in linha):
                    continue
                telefone = linha[idx_tel].strip() if idx_tel < len(linha) else ""
                nome = (linha[idx_nome].strip()
                        if idx_nome is not None and idx_nome < len(linha) else "")
                if telefone:
                    contatos.append({"nome": nome, "telefone": telefone})
        else:
            # .txt: um telefone por linha
            with open(caminho, encoding="utf-8-sig") as f:
                for linha in f:
                    telefone = linha.strip()
                    if telefone:
                        contatos.append({"nome": "", "telefone": telefone})
        return contatos

    # ------------------------------------------------------------
    # Ações: disparo
    # ------------------------------------------------------------
    def _atualizar_estado_botao_iniciar(self):
        pode_iniciar = self.bot.esta_conectado() and bool(self.contatos) and not self.enviando
        self.btn_iniciar.configure(state="normal" if pode_iniciar else "disabled")

    def _iniciar_disparo(self):
        mensagem = self.txt_mensagem.get("1.0", "end").strip()
        if not mensagem:
            messagebox.showwarning("Mensagem", "Escreva uma mensagem antes de iniciar.")
            return
        if not self.contatos:
            messagebox.showwarning("Contatos", "Carregue uma lista de contatos antes de iniciar.")
            return

        try:
            delay_min = float(self.spin_min.get())
            delay_max = float(self.spin_max.get())
        except ValueError:
            messagebox.showerror("Intervalo", "Os campos de intervalo devem ser números.")
            return
        if delay_min > delay_max:
            delay_min, delay_max = delay_max, delay_min

        confirmar = messagebox.askyesno(
            "Confirmar disparo",
            f"Enviar mensagem para {len(self.contatos)} contato(s)?\n\n"
            "Confirme que todos consentiram em receber esta mensagem."
        )
        if not confirmar:
            return

        self.enviando = True
        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")
        self.progress["value"] = 0

        def tarefa():
            resultado = self.bot.enviar_em_massa(
                self.contatos, mensagem, delay_min, delay_max,
                progresso_callback=lambda a, t: self.progress_queue.put((a, t)),
                caminho_midia=self.caminho_midia,
            )
            self.root.after(0, lambda: self._pos_disparo(resultado))

        threading.Thread(target=tarefa, daemon=True).start()

    def _parar_disparo(self):
        self.bot.stop()
        self.btn_parar.configure(state="disabled")

    def _pos_disparo(self, resultado: dict):
        self.enviando = False
        self.btn_parar.configure(state="disabled")
        self._atualizar_estado_botao_iniciar()

        status = "interrompido pelo usuário" if resultado["interrompido"] else "concluído"
        self._enfileirar_log(
            f"Disparo {status}. Enviados: {resultado['enviados']} | "
            f"Falhas: {resultado['falhas']}"
        )
        messagebox.showinfo(
            "Disparo finalizado",
            f"Status: {status}\nEnviados: {resultado['enviados']}\nFalhas: {resultado['falhas']}"
        )

    # ------------------------------------------------------------
    def _ao_fechar(self):
        if self.enviando:
            if not messagebox.askyesno("Sair", "Um disparo está em andamento. Deseja realmente sair?"):
                return
            self.bot.stop()
        self.bot.encerrar()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
