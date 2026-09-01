# Disparador de WhatsApp

Interface gráfica (desktop, Tkinter) para automatizar o envio de mensagens
pelo WhatsApp Web usando Selenium.

## Estrutura do projeto

```
whatsapp_bot/
├── main.py                # ponto de entrada (abre a interface)
├── gui.py                 # interface gráfica (Tkinter)
├── bot.py                 # lógica de automação (Selenium)
├── requirements.txt
├── contatos_exemplo.csv   # modelo de arquivo de contatos
└── chrome-profile/        # criado automaticamente na 1ª execução
                            # (guarda a sessão logada do WhatsApp Web)
```

## Pré-requisitos

- Python 3.9+
- Google Chrome instalado
- Um número de WhatsApp para escanear o QR Code na primeira execução

## Instalação

```bash
cd whatsapp_bot
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

O Selenium (a partir da versão 4.6) baixa e gerencia o `chromedriver`
automaticamente — não é necessário instalar nada além do Chrome.

## Como usar

```bash
python main.py
```

1. **Conectar**: clique em "Conectar ao WhatsApp". Uma janela do Chrome vai
   abrir com o WhatsApp Web — escaneie o QR Code com o celular. Da segunda
   vez em diante a sessão fica salva em `chrome-profile/` e o login costuma
   ser automático.
2. **Contatos**: clique em "Carregar arquivo" e selecione um `.csv` (colunas
   `nome,telefone`) ou `.txt` (um telefone por linha). Use o
   `contatos_exemplo.csv` como modelo. O telefone deve incluir o DDI
   (código do país), por exemplo `5583999999999` para o Brasil.
3. **Mensagem**: escreva o texto. Use `{nome}` no meio da frase para
   personalizar automaticamente com o nome de cada contato.
4. **Intervalo**: defina um intervalo mínimo e máximo (em segundos) entre
   um envio e outro — o valor real é sorteado dentro dessa faixa a cada
   mensagem, para simular um comportamento mais humano.
5. **Iniciar disparo**: confirme e acompanhe o progresso e o log em tempo
   real. É possível clicar em "Parar" a qualquer momento para interromper
   com segurança entre um envio e outro.

## Avisos importantes

- Esta ferramenta automatiza o **WhatsApp Web não-oficial**. Isso não é a
  API oficial de negócios do WhatsApp (WhatsApp Business Platform), então
  há risco real de o número ser **temporária ou permanentemente banido**
  se detectar comportamento de spam.
- Envie mensagens **apenas para pessoas que consentiram** em recebê-las
  (ex.: clientes que se cadastraram, leads que pediram contato). Envio em
  massa para números frios pode violar os Termos de Serviço do WhatsApp e,
  dependendo do caso, legislações de proteção de dados (como a LGPD no
  Brasil) e leis contra spam.
- Para operações comerciais em maior escala e com garantias de conformidade,
  considere migrar para a **WhatsApp Business Platform (Cloud API)**
  oficial da Meta.
- Use intervalos generosos entre mensagens e evite disparar para centenas
  de contatos de uma vez com o mesmo número — isso reduz bastante o risco
  de bloqueio.

## Solução de problemas

- **"Timeout aguardando login"**: verifique sua conexão com a internet e
  se o app do WhatsApp no celular está atualizado; tente novamente.
- **"Não foi possível abrir a conversa"**: normalmente indica número sem
  WhatsApp, número mal formatado (falta o DDI) ou carregamento lento —
  aumente o timeout em `bot.py` se sua internet for instável.
- Se o Chrome não abrir, confirme que ele está instalado e que nenhuma
  outra instância está usando a mesma pasta `chrome-profile/`.
