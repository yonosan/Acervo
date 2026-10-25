# Compilar o Acervo para Windows

Requer Windows 10/11 x64, Python 3.11, FFmpeg, ffprobe, Node.js e Edge WebView2. FFmpeg, ffprobe e Node devem estar no PATH.

No PowerShell, dentro da pasta do código:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-frozen.txt
.\.venv\Scripts\python.exe build_windows.py
```

O resultado fica em `dist/Acervo/Acervo.exe`, acompanhado de `_internal`. Mantenha a pasta inteira. O script não instala nem substitui o aplicativo em uso e não copia dados pessoais.

As versões registradas em requirements-frozen.txt vêm do ambiente usado no pacote 1.9.0. O FFmpeg original e o Node estão identificados em SOURCES.md. Para reproduzir o ambiente de mídia, use as mesmas versões desses binários. Não é prometida reprodução idêntica de bytes: caminhos, ambiente e metadados da compilação podem variar.

Para distribuir um pacote compilado, inclua LICENSE, os avisos de terceiros, seus textos de licença e acesso ao código-fonte correspondente. Consulte o pacote Acervo-Fontes-1.9.0.zip e SOURCES.md. O script gera a pasta de execução local; não substitui essa preparação de distribuição.
