# Antigravity Studio Mobile (App Android Nativo — Kotlin + Jetpack Compose)

Versão **100% nativa para Android** do **Antigravity Studio Playground**, construída em **Kotlin**, **Jetpack Compose (Material 3)**, **Room (SQLite)** e **OkHttp3**.

O aplicativo funciona de forma totalmente autônoma no celular, comunicando-se diretamente com a **Gemini Interactions API** e a **Environments API** (`https://generativelanguage.googleapis.com/v1beta/...`), sem depender de nenhum servidor Python externo.

---

## Principais Funcionalidades Portadas para o Celular

1. **Recuperação Completa de Conversas (`previous_interaction_id`):**
   - Toque em **"🔗 Importar ID"** no menu lateral e cole qualquer `interaction_id` (`v1_...`).
   - O app percorre recursivamente toda a lista encadeada de `previous_interaction_id` usando `include_input=true`, reconstrói todos os turnos desde o Turno #1 (incluindo *thoughts* e *tool calls*) e salva no banco **SQLite (Room)** local do aparelho (`antigravity_studio.db`).
2. **Continuação no Mesmo Container Linux (`environment_id`):**
   - Preserva automaticamente o `environment_id` (`env_...`) retornado pelo agente Antigravity, garantindo que os próximos turnos continuem exatamente com os mesmos arquivos e pacotes no sandbox remoto.
3. **Ramificação de Conversa (*Fork*) e Apontamento de ID:**
   - Em qualquer turno da conversa, toque em **"🌿 Ramificar (Fork)"** para criar uma nova sessão até aquele ponto ou **"🎯 Apontar ID aqui"** para encadear a próxima mensagem a partir daquele turno específico.
4. **Execução em Background (`background=true`), Polling Automático e Continuação de Turnos Incompletos:**
   - Inclui chave **"⚡ Background"** na barra inferior com *polling* automático a cada 3,2 segundos enquanto o turno estiver `in_progress`, além de botão de cancelamento (`POST /v1beta/interactions/{id}/cancel`).
   - Quando um turno retorna `status: "incomplete"`, exibe o botão **"▶️ Continuar de onde parou"** que envia `input="continue"` encadeado no mesmo `previous_interaction_id` e `environment_id`.
5. **Gerenciador de Ambiente Sandbox & Upload/Download de Arquivos (`Environments API`):**
   - **Provisionamento proativo (`POST /v1beta/environments`)** com suporte a `sources` (repositórios GitHub, buckets GCS e arquivos inline).
   - **Upload de Arquivos ou Pastas do Android:** Permite selecionar arquivos ou uma pasta inteira do celular (`DocumentFile` / `Storage Access Framework`), compacta automaticamente em `.tar.gz` em memória (incluindo extração preventiva `.extracted.txt` para documentos `.odt`, `.odp`, `.docx` e `.pptx`) e envia com `extract=true`.
   - **Explorador de Arquivos e Download de Snapshot (`.tar.gz`):** Navegue pelos arquivos de `/workspace` no container remoto e baixe/compartilhe o snapshot completo `.tar.gz` ou arquivos individuais usando o menu nativo de compartilhamento do Android (`FileProvider`).
6. **Gerador de Código (`Get Code`) e Exportação Markdown (`.md`):**
   - Visualize e copie snippets prontos em **Python (`google-genai`)** e **cURL**, ou exporte a conversa inteira como arquivo `.md`.

---

## Estrutura do Projeto (`/antigravity_android`)

```text
antigravity_android/
├── .github/workflows/android-build.yml   # Workflow GitHub Actions que gera o APK na nuvem
├── build.gradle.kts                      # Configuração Gradle raiz
├── settings.gradle.kts                   # Definição do projeto Android
├── gradle.properties                     # Propriedades JVM/AndroidX
├── gradle/libs.versions.toml             # Catálogo de versões (Compose, Room, OkHttp, Commons Compress)
└── app/
    ├── build.gradle.kts                  # Dependências e configuração do módulo :app
    └── src/main/
        ├── AndroidManifest.xml           # Permissões de Internet, MainActivity e FileProvider
        ├── java/com/antigravity/studio/
        │   ├── AntigravityApp.kt         # Classe Application (inicializa Room + Repository)
        │   ├── MainActivity.kt           # Activity principal com Jetpack Compose
        │   ├── data/
        │   │   ├── model/Models.kt       # Modelos de domínio e serialização JSON
        │   │   ├── local/AppDatabase.kt  # Banco SQLite local via Room (sessions, turns, settings)
        │   │   └── remote/InteractionsClient.kt # Cliente completo Interactions API + Environments API
        │   └── ui/
        │       ├── StudioViewModel.kt    # Estado da UI, polling em background, upload/download
        │       ├── theme/Theme.kt        # Tema escuro Material 3 (Slate/Indigo/Sky)
        │       ├── components/DialogsAndSheets.kt # Modais de API Key, Import, Run Settings e Sandbox
        │       └── screens/MainScreen.kt # Tela principal com Drawer, cards de turno e composer
        └── res/                          # Ícones vetoriais, temas, strings e file_paths.xml
```

---

## Como Gerar e Instalar o App Android (`.apk`)

### Opção A: Usando o Android Studio no seu Computador
1. Baixe o snapshot deste ambiente clicando no botão **Download** nas configurações de ambiente (*Environment settings*) do AI Studio Playground.
2. Extraia o arquivo baixado e abra a pasta **`antigravity_android`** no **Android Studio** (Ladybug / Koala ou superior).
3. Aguarde o Android Studio sincronizar o Gradle automaticamente (*Gradle Sync*).
4. Conecte seu celular Android via cabo USB (ou Depuração por Wi-Fi) ou inicie um emulador e clique em **Run ▶️** (ou vá em **Build > Build Bundle(s) / APK(s) > Build APK(s)** para gerar o arquivo `app-debug.apk`).

### Opção B: Compilando na Nuvem via GitHub Actions (Sem precisar instalar o Android Studio)
1. Suba o conteúdo da pasta `antigravity_android` para um repositório no seu GitHub.
2. O arquivo `.github/workflows/android-build.yml` já está configurado: a cada *push* (ou clicando em *Run workflow* na aba **Actions** do GitHub), o GitHub compila automaticamente o projeto e disponibiliza o arquivo **`antigravity-studio-debug-apk`** pronto para baixar e instalar no celular!
