# Dependências e licenças

Este pacote contém o código do Acervo e suas referências a dependências. Não inclui as instalações das bibliotecas, Python, FFmpeg, Node, navegadores ou arquivos de mídia. Esta lista registra as dependências diretas; não é uma auditoria completa de redistribuição de executáveis ou de dependências transitivas.

| Componente | Versão registrada | Licença declarada | Origem |
| --- | --- | --- | --- |
| FastAPI | 0.162.0 | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| yt-dlp (pacote Python) | 2026.10.21 | Unlicense | https://github.com/yt-dlp/yt-dlp |
| pywebview | 6.2.1 | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| Playwright | 1.64.1 | Apache-2.0 | https://github.com/microsoft/playwright-python |
| Mutagen | 1.48.1 | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |

Metadados conferidos nas instalações locais usadas na preparação. As licenças completas e avisos pertencem aos respectivos projetos.

O uso de Mutagen deve ser considerado na escolha de uma licença compatível e na redistribuição do conjunto. Não assuma que todos os componentes sejam MIT ou Unlicense.

FFmpeg e ffprobe: a licença depende da compilação e dos componentes habilitados. Consulte https://ffmpeg.org/legal.html e preserve as informações de compilação e fontes correspondentes se distribuir esses binários.

Os executáveis oficiais do yt-dlp podem conter componentes sob GPL e outras licenças, diferentes da licença isolada do pacote Python. Consulte https://github.com/yt-dlp/yt-dlp#licensing.

Ferramentas de desenvolvimento, runtimes e dependências transitivas também conservam suas próprias licenças. Nenhum aviso deste arquivo substitui suas exigências.
