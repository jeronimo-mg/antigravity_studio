# Antigravity Studio Playground (Python Full-Stack)

Interface visual inspirada no **Google AI Studio Playground**, projetada especificamente para recuperar, visualizar, ramificar (*Fork*) e continuar conversas do **Agente Antigravity** e dos modelos **Gemini** usando `previous_interaction_id` e `environment_id` na **Interactions API**.

---

## Principais Funcionalidades

1. **Recuperação Completa de Conversas (`previous_interaction_id`):**
   - Basta colar o **ID da última interação** (`v1_...`) no botão **"🔗 Importar / Continuar por ID"**.
   - O backend percorre recursivamente toda a lista encadeada de `previous_interaction_id` na Interactions API usando `include_input=true`, reconstrói todos os turnos desde o Turno #1 (incluindo inputs do usuário, pensamentos `thought`, chamadas de ferramentas `function_call` e respostas `model_output`) e salva tudo localmente em um banco **SQLite** (`antigravity_studio.db`).
2. **Continuação no Mesmo Container Linux (`environment_id`):**
   - Detecta e preserva automaticamente o `environment_id` (`env_...`) retornado pelo agente Antigravity, garantindo que os próximos turnos continuem exatamente com os mesmos arquivos, repositórios e pacotes instalados no sandbox.
3. **Ramificação de Conversa (*Fork* de Qualquer Turno):**
   - Cada card de turno exibe seu `interaction_id`, `previous_interaction_id` e `environment_id`, permitindo clicar em **"🌿 Ramificar (Fork)"** ou **"🎯 Apontar ID aqui"** para continuar a conversa a partir de qualquer ponto anterior.
4. **Suporte a Execução em Background e Continuação de Interações Incompletas:**
   - Suporta `background=True` com polling automático e botão de cancelamento.
   - Se o agente atingir o orçamento de tokens (`max_total_tokens`) e retornar `status: "incomplete"`, um botão **"▶️ Continuar de onde parou"** envia automaticamente `input="continue"` encadeado no mesmo `previous_interaction_id` e `environment_id`.
5. **Exportação para Markdown e Gerador de Código (`Get code`):**
   - Exporte qualquer sessão com 1 clique para `.md` ou gere o snippet atualizado em Python (`google-genai`) e `cURL` já preenchido com o `previous_interaction_id` e `environment_id` ativos.
6. **Download do Snapshot do Ambiente Sandbox (`Download Snapshot`):**
   - Assim como no **Google AI Studio Playground** (onde existe o botão **Download** nas configurações de ambiente para baixar o snapshot do container), a interface inclui o botão **"📦 Download Snapshot"** na barra superior e **"⬇️ Download Environment Snapshot (.tar.gz)"** no painel lateral *Ambiente Sandbox*. Permite baixar com 1 clique o arquivo `.tar.gz` completo de todos os arquivos e artefatos criados pelo agente.
7. **Montagem de Fontes e Repositórios GitHub (`sources`):**
   - Assim como no **Google AI Studio Playground**, permite vincular fontes antes do primeiro turno através do botão **"＋ Adicionar Fonte"** no painel *Run Settings*.
   - Suporta clonagem de repositórios Git públicos (ex: GitHub), pastas do Google Cloud Storage (GCS) ou arquivos de texto direto (inline) montados no contêiner Linux (ex: `/workspace/meu-repo`). O backend estrutura automaticamente o parâmetro `environment: {"type": "remote", "sources": [...]}` na chamada inicial à Interactions API.
8. **Upload Direto de Arquivos e Pastas Locais via API de Ambientes (`Environments Upload API`):**
   - Permite enviar arquivos locais únicos, múltiplos arquivos, pastas inteiras do seu computador ou pacotes `.tar.gz`/`.tar` diretamente para um ambiente remoto usando a rota oficial `PUT /upload/v1beta/environments/{environment_id}/files/{path}` (equivalente a `client.environments.files.upload(...)` no SDK `google-genai`).
   - **Empacotamento automático de pastas em `.tar.gz`:** Ao selecionar uma pasta local na interface, o backend gera o arquivo `.tar.gz` em memória e realiza o upload com `extract=true` e `overwrite=true`, extraindo toda a árvore de diretórios dentro do sandbox (ex: `/workspace/meu-projeto`).
   - **Provisionamento automático (`POST /v1beta/environments`):** Se a sessão ainda estiver com `environment="remote"`, o sistema provisiona automaticamente o contêiner remoto antes de realizar o upload e vincula o `environment_id` retornado à sessão.
   - **Suporte a Upload Retomável (`uploadType=resumable`):** Opção para envio em chunks (`Content-Range`) e protocolo resumable Scotty para arquivos grandes, além de explorador de arquivos do ambiente (`GET /v1beta/environments/{environment_id}/files/{path}`).
9. **Chave Seletora `"Modo: Sandbox Remoto (Google) | Máquina Local"` & Agente / CLI Local (Estilo `gemini-cli` / `claude code`):**
   - Na interface web (barra do compositor e painel *Run Settings*), alterne instantaneamente entre **`☁️ Sandbox Remoto (Google)`** e **`💻 Máquina Local`**.
   - Quando em **`💻 Máquina Local`**, o agente executa comandos diretamente no terminal/sistema operacional do seu computador, cria pastas e arquivos no seu disco e lê/edita diretamente os arquivos da pasta do seu projeto (`local_workdir`), mantendo exatamente as mesmas **Capabilities e Supported Tools** da documentação oficial do Antigravity Agent (`https://aistudio.google.com/docs/antigravity-agent`):
     - `execute_bash`: Execução de comandos no terminal/shell do sistema operacional local com captura de `stdout`, `stderr` e `exit_code`.
     - `view_file`: Leitura de arquivos locais com numeração de linhas (`StartLine`/`EndLine`) e extração nativa de documentos (`.pdf`, `.odt`, `.odp`, `.docx`, `.pptx`).
     - `write_to_file`: Criação/sobrescrita/append de arquivos e diretórios pai diretamente no disco local.
     - `replace_file_content`: Edição cirúrgica de blocos de código em arquivos locais do projeto.
     - `list_dir`, `find_by_name`, `grep_search`: Navegação e busca rápida na árvore de arquivos do projeto local.
     - `web_search` e `browse_url`: Pesquisa web em tempo real e extração de conteúdo de páginas HTTP/HTTPS.

---

## Como Executar Localmente (Interface Web + CLI)

### 1. Instalar dependências
```bash
cd antigravity_studio
pip install -r requirements.txt
```

### 2. Configurar sua chave de API (ou configurar direto pela interface web)
```bash
export GEMINI_API_KEY="SUA_CHAVE_AQUI"
```
*(Você também pode clicar no botão **🔑 API Key** no canto superior direito da interface para salvar sua chave direto no banco SQLite).*

### 3. Iniciar a Interface Web (Playground)
```bash
python3 run.py
```
Depois, abra no navegador: **http://127.0.0.1:8000** e escolha na chave seletora:
- **Modo: `☁️ Sandbox Remoto (Google) | 💻 Máquina Local`**

### 4. Usar como Agente CLI no Terminal Local (`--cli`)
Você também pode rodar o agente diretamente no seu terminal (estilo `gemini-cli` ou `claude code`), apontando para qualquer pasta de projeto:

```bash
# Modo interativo (REPL) na pasta atual ou em um projeto específico:
python3 run.py --cli --workdir /caminho/do/seu/projeto

# Modo One-Shot (executa a tarefa localmente e encerra):
python3 run.py --cli --workdir /caminho/do/seu/projeto -p "Analise a estrutura do projeto, rode os testes e crie um arquivo RELATORIO.md"
```
No modo interativo do terminal, use `/help`, `/tools`, `/cd <pasta>`, `/pwd`, `/ls`, `/local`, `/remote`, `/model <nome>` ou `/exit`.

